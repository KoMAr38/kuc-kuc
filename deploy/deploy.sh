#!/usr/bin/env bash
# Build one image with Cloud Build and deploy the services from it.
#
#   bash deploy/deploy.sh                 # voice, engine, web, both jobs, the Telegram address
#   SERVICES="voice" bash deploy/deploy.sh
#
# deploy/live.env says which parts of the site are switched on (KUCKUC_FEATURES) and other
# public settings. It holds no secrets.
#
# The family page needs three things the owner stores, and works without them until then:
#   secret invite-codes        who may enrol a person (one code, or several separated by commas)
#   secret telegram-bot-token  the bot that sends the family their notices
#   live.env                   KUCKUC_GOOGLE_OAUTH_CLIENT_ID and KUCKUC_TELEGRAM_BOT_NAME (public)
#
# Every service scales to zero. Nothing here contains a secret: services read their
# credentials from Secret Manager at start-up.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/common.sh

if [ -f deploy/live.env ]; then
  set -a
  source deploy/live.env
  set +a
fi

SERVICES="${SERVICES:-voice engine web forecast-scan watchdog loadtest telegram-webhook}"

# Never swap the servers a running phone call depends on: on 17 November a release in the middle of
# a call made one answer reach two servers and turned it into "unclear" (scripts/live_calls.py).
# Checked right before each swap, not before the build: a call can start while the image builds.
# Waits as long as a call can count as live (16 minutes), then stops. Any other failure (no
# credentials, no Python) stops the release at once: not knowing is not the same as a live call.
guard_live_calls() {
  [ -n "${FORCE_DEPLOY:-}" ] && return 0
  local python="${PYTHON:-.venv/Scripts/python}"
  [ -x "$python" ] || python=".venv/bin/python"
  local status
  for _ in $(seq 1 32); do
    status=0
    PYTHONPATH=src GCP_PROJECT="$PROJECT" KUCKUC_PROJECT="$PROJECT" "$python" scripts/live_calls.py || status=$?
    [ "$status" -eq 0 ] && return 0
    if [ "$status" -ne 1 ]; then
      echo "Could not check for a call under way (exit $status). Nothing was replaced; fix that and deploy again." >&2
      exit 1
    fi
    echo "   a call is under way; waiting for it to end" >&2
    sleep 30
  done
  echo "A call has counted as under way for 16 minutes. Deploy again later, or set FORCE_DEPLOY=1." >&2
  exit 1
}
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
# The version is named after the commit: changes not committed would run under a name that hides them.
if git rev-parse --git-dir >/dev/null 2>&1 && ! git diff --quiet HEAD --; then
  if [ "${ALLOW_DIRTY:-}" != "1" ]; then
    echo "There are changes not committed to git. Commit them first, or set ALLOW_DIRTY=1." >&2
    exit 1
  fi
  TAG="$TAG-dirty-$(date +%Y%m%d%H%M%S)"
fi
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/app:$TAG"

say "Building $IMAGE"
gcloud builds submit --project "$PROJECT" --tag "$IMAGE" --quiet .

# Values may contain commas (the feature list is JSON), so "|" separates the variables.
ENV="^|^KUCKUC_PROJECT=$PROJECT|KUCKUC_REGION=$REGION|KUCKUC_BUCKET=$BUCKET"
ENV="$ENV|KUCKUC_VOICE_PUBLIC_URL=$VOICE_URL|KUCKUC_ENGINE_URL=$ENGINE_URL|KUCKUC_WEB_PUBLIC_URL=$WEB_URL"
ENV="$ENV|KUCKUC_INVOKER_EMAIL=$INVOKER_SA|KUCKUC_FEATURES=${KUCKUC_FEATURES:-[]}"
for name in GEMINI_MODEL GEMINI_LOCATION GEMINI_THINKING_LEVEL TTS_VOICE_SR TTS_VOICE_EN TTS_RATE_SR GITHUB_URL \
  GOOGLE_OAUTH_CLIENT_ID TELEGRAM_BOT_NAME; do
  value="$(printenv "KUCKUC_$name" || true)"
  [ -n "$value" ] && ENV="$ENV|KUCKUC_$name=$value"
done


have_secret() { gcloud secrets describe "$1" --project "$PROJECT" >/dev/null 2>&1; }

allow_secrets() { # allow_secrets <service-account-name> <secret>...
  local sa="$1"
  shift
  for secret in "$@"; do
    gcloud secrets add-iam-policy-binding "$secret" --project "$PROJECT" --quiet \
      --member "serviceAccount:$sa@$SA_DOMAIN" --role roles/secretmanager.secretAccessor >/dev/null
  done
}

run_deploy() { # run_deploy <service> <module> <flags...>
  local service="$1" module="$2"
  shift 2
  gcloud run deploy "$service" --project "$PROJECT" --region "$REGION" --image "$IMAGE" \
    --service-account "$service-sa@$SA_DOMAIN" \
    --min-instances 0 --cpu 1 --memory 512Mi --concurrency 40 \
    --command uvicorn --args "$module,--host,0.0.0.0,--port,8080,--loop,asyncio" \
    --set-env-vars "$ENV" --quiet "$@"
}

for service in $SERVICES; do
  case "$service" in
    voice)
      say "Deploying voice (public: the phone company calls it; it checks every request itself)"
      # Voice dials and never verifies numbers, so it does not get the Verify service's id.
      allow_secrets voice-sa twilio-account-sid twilio-auth-token twilio-from-number
      VOICE_SECRETS="KUCKUC_TWILIO_ACCOUNT_SID=twilio-account-sid:latest"
      VOICE_SECRETS="$VOICE_SECRETS,KUCKUC_TWILIO_AUTH_TOKEN=twilio-auth-token:latest"
      VOICE_SECRETS="$VOICE_SECRETS,KUCKUC_TWILIO_FROM_NUMBER=twilio-from-number:latest"
      guard_live_calls
      run_deploy voice kuckuc.voice.main:app --allow-unauthenticated \
        --timeout 30 --max-instances 20 --cpu-boost --set-secrets "$VOICE_SECRETS"
      # Only now, with the new revision running, is the old Verify secret taken away. A rollback of voice
      # to a revision from before 17 Nov 2026 needs it back first: allow_secrets voice-sa twilio-verify-sid.
      gcloud secrets remove-iam-policy-binding twilio-verify-sid --project "$PROJECT" --quiet \
        --member "serviceAccount:voice-sa@$SA_DOMAIN" --role roles/secretmanager.secretAccessor >/dev/null 2>&1 || true
      ;;
    engine)
      say "Deploying engine (private: only Pub/Sub and Cloud Tasks can reach it)"
      ENGINE_FLAGS=(--no-allow-unauthenticated --ingress internal --timeout 60 --max-instances 20)
      if have_secret telegram-bot-token; then
        # Only the engine sends messages, so only the engine gets the bot's token.
        allow_secrets engine-sa telegram-bot-token
        ENGINE_FLAGS+=(--set-secrets "KUCKUC_TELEGRAM_BOT_TOKEN=telegram-bot-token:latest")
      else
        echo "   no telegram-bot-token secret yet: family notices go to the log only"
      fi
      guard_live_calls
      run_deploy engine kuckuc.engine.main:app "${ENGINE_FLAGS[@]}"
      gcloud run services add-iam-policy-binding engine --project "$PROJECT" --region "$REGION" --quiet \
        --member "serviceAccount:$INVOKER_SA" --role roles/run.invoker >/dev/null

      say "Pub/Sub push subscription: events -> engine"
      SUB_FLAGS=(--push-endpoint "$ENGINE_URL/events" --push-auth-service-account "$INVOKER_SA"
        --push-auth-token-audience "$ENGINE_URL" --ack-deadline 30
        --min-retry-delay 10s --max-retry-delay 600s --expiration-period never)
      if gcloud pubsub subscriptions describe events-to-engine --project "$PROJECT" >/dev/null 2>&1; then
        gcloud pubsub subscriptions update events-to-engine --project "$PROJECT" "${SUB_FLAGS[@]}" >/dev/null
      else
        gcloud pubsub subscriptions create events-to-engine --project "$PROJECT" --topic events "${SUB_FLAGS[@]}"
      fi
      ;;
    web)
      say "Deploying web (public: landing page, demo and the family page)"
      allow_secrets web-sa twilio-account-sid twilio-auth-token twilio-verify-sid twilio-from-number hash-salt \
        session-secret
      WEB_SECRETS="KUCKUC_TWILIO_ACCOUNT_SID=twilio-account-sid:latest"
      WEB_SECRETS="$WEB_SECRETS,KUCKUC_TWILIO_AUTH_TOKEN=twilio-auth-token:latest"
      WEB_SECRETS="$WEB_SECRETS,KUCKUC_TWILIO_VERIFY_SID=twilio-verify-sid:latest"
      WEB_SECRETS="$WEB_SECRETS,KUCKUC_HASH_SALT=hash-salt:latest"
      # The demo refuses the service's own number, so web has to know which number that is.
      WEB_SECRETS="$WEB_SECRETS,KUCKUC_TWILIO_FROM_NUMBER=twilio-from-number:latest"
      # Signs the sign-in cookie of the family page.
      WEB_SECRETS="$WEB_SECRETS,KUCKUC_SESSION_SECRET=session-secret:latest"
      if have_secret invite-codes; then
        allow_secrets web-sa invite-codes
        WEB_SECRETS="$WEB_SECRETS,KUCKUC_INVITE_CODES=invite-codes:latest"
      else
        echo "   no invite-codes secret yet: nobody can enrol a person"
      fi
      if have_secret telegram-webhook-secret; then
        # Web receives what people write to the bot, at an address only Telegram knows. It never gets the token.
        allow_secrets web-sa telegram-webhook-secret
        WEB_SECRETS="$WEB_SECRETS,KUCKUC_TELEGRAM_WEBHOOK_SECRET=telegram-webhook-secret:latest"
      fi
      run_deploy web kuckuc.web.main:app --allow-unauthenticated \
        --timeout 30 --max-instances 10 --cpu-boost --set-secrets "$WEB_SECRETS"
      ;;
    forecast-scan)
      say "Deploying forecast-scan (job: runs for a few seconds every 3 hours, then exits)"
      # --args=... (with "="): the first argument starts with a dash and would otherwise be read as a flag.
      gcloud run jobs deploy forecast-scan --project "$PROJECT" --region "$REGION" --image "$IMAGE" \
        --service-account "jobs-sa@$SA_DOMAIN" --command python --args=-m,kuckuc.jobs.forecast_scan \
        --set-env-vars "$ENV" --max-retries 1 --task-timeout 300 --quiet
      gcloud run jobs add-iam-policy-binding forecast-scan --project "$PROJECT" --region "$REGION" --quiet \
        --member "serviceAccount:$INVOKER_SA" --role roles/run.invoker >/dev/null

      say "Cloud Scheduler: forecast-scan every 3 hours"
      JOB_URI="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/forecast-scan:run"
      SCHED_FLAGS=(--project "$PROJECT" --location "$REGION" --schedule "0 */3 * * *"
        --time-zone "Europe/Belgrade" --uri "$JOB_URI" --http-method POST
        --oauth-service-account-email "$INVOKER_SA")
      if gcloud scheduler jobs describe forecast-scan-3h --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
        gcloud scheduler jobs update http forecast-scan-3h "${SCHED_FLAGS[@]}" >/dev/null
      else
        gcloud scheduler jobs create http forecast-scan-3h "${SCHED_FLAGS[@]}" >/dev/null
      fi
      ;;
    watchdog)
      say "Deploying watchdog (job: every 10 minutes, closes a check that stopped moving and tells the family)"
      WATCHDOG_FLAGS=(--service-account "jobs-sa@$SA_DOMAIN" --command python --args=-m,kuckuc.jobs.watchdog
        --set-env-vars "$ENV" --max-retries 1 --task-timeout 120 --quiet)
      if have_secret telegram-bot-token; then
        allow_secrets jobs-sa telegram-bot-token
        WATCHDOG_FLAGS+=(--set-secrets "KUCKUC_TELEGRAM_BOT_TOKEN=telegram-bot-token:latest")
      fi
      gcloud run jobs deploy watchdog --project "$PROJECT" --region "$REGION" --image "$IMAGE" "${WATCHDOG_FLAGS[@]}"
      gcloud run jobs add-iam-policy-binding watchdog --project "$PROJECT" --region "$REGION" --quiet \
        --member "serviceAccount:$INVOKER_SA" --role roles/run.invoker >/dev/null
      WATCHDOG_URI="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/watchdog:run"
      WATCHDOG_SCHED=(--project "$PROJECT" --location "$REGION" --schedule "*/10 * * * *"
        --time-zone "Europe/Belgrade" --uri "$WATCHDOG_URI" --http-method POST
        --oauth-service-account-email "$INVOKER_SA")
      if gcloud scheduler jobs describe watchdog-10m --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
        gcloud scheduler jobs update http watchdog-10m "${WATCHDOG_SCHED[@]}" >/dev/null
      else
        gcloud scheduler jobs create http watchdog-10m "${WATCHDOG_SCHED[@]}" >/dev/null
      fi
      ;;
    loadtest)
      say "Deploying loadtest (job: simulated people, no telephony; started by hand, never by a schedule)"
      # Run it with:  gcloud run jobs execute loadtest --region "$REGION" --args=--n=2000 --wait
      # The module is part of the command, so --args on an execution replaces only the numbers.
      gcloud run jobs deploy loadtest --project "$PROJECT" --region "$REGION" --image "$IMAGE" \
        --service-account "jobs-sa@$SA_DOMAIN" --command=python,-m,kuckuc.jobs.loadtest --args=--n=2000 \
        --set-env-vars "$ENV" --max-retries 0 --task-timeout 900 --cpu 2 --memory 1Gi --quiet
      ;;
    telegram-webhook)
      if have_secret telegram-bot-token && have_secret telegram-webhook-secret; then
        say "Telling Telegram where to deliver the bot's messages (a job, so the token stays in the cloud)"
        allow_secrets jobs-sa telegram-bot-token telegram-webhook-secret
        TELEGRAM_SECRETS="KUCKUC_TELEGRAM_BOT_TOKEN=telegram-bot-token:latest"
        TELEGRAM_SECRETS="$TELEGRAM_SECRETS,KUCKUC_TELEGRAM_WEBHOOK_SECRET=telegram-webhook-secret:latest"
        gcloud run jobs deploy telegram-webhook --project "$PROJECT" --region "$REGION" --image "$IMAGE" \
          --service-account "jobs-sa@$SA_DOMAIN" --command python --args=-m,kuckuc.jobs.telegram_webhook \
          --set-env-vars "$ENV" --set-secrets "$TELEGRAM_SECRETS" \
          --max-retries 1 --task-timeout 60 --execute-now --wait --quiet
      else
        say "Telegram: no bot token stored yet, nothing to register"
      fi
      ;;
    *)
      echo "Unknown service: $service" >&2
      exit 1
      ;;
  esac
done

say "Deployed"
gcloud run services list --project "$PROJECT" --region "$REGION" \
  --format 'table(metadata.name, status.url, spec.template.metadata.annotations."autoscaling.knative.dev/maxScale")'
echo "web:    $WEB_URL"
echo "voice:  $VOICE_URL"
echo "engine: $ENGINE_URL (internal)"
