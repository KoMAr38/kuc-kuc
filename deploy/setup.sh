#!/usr/bin/env bash
# One-time setup of the Google Cloud project: APIs, identities, storage, queues.
# Safe to run again: every step first checks whether its work is already done.
#
#   bash deploy/setup.sh
#
# It creates no secrets with real credentials. The four Twilio secrets are created by the
# project owner by hand (see README), so they never pass through a script or a chat.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/common.sh

say "Project $PROJECT ($PROJECT_NUMBER), region $REGION"

say "Enabling APIs"
gcloud services enable --project "$PROJECT" \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com pubsub.googleapis.com cloudtasks.googleapis.com \
  cloudscheduler.googleapis.com secretmanager.googleapis.com storage.googleapis.com \
  aiplatform.googleapis.com texttospeech.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com

say "Service accounts (one per service, least privilege)"
ensure_sa() {
  gcloud iam service-accounts describe "$1@$SA_DOMAIN" --project "$PROJECT" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$1" --project "$PROJECT" --display-name "$2"
}
ensure_sa web-sa "Kuc-kuc web"
ensure_sa engine-sa "Kuc-kuc engine"
ensure_sa voice-sa "Kuc-kuc voice"
ensure_sa jobs-sa "Kuc-kuc jobs"
ensure_sa invoker-sa "Kuc-kuc invoker (Pub/Sub push, Cloud Tasks, Scheduler)"

grant() { # grant <service-account-name> <role>
  gcloud projects add-iam-policy-binding "$PROJECT" --quiet --condition=None \
    --member "serviceAccount:$1@$SA_DOMAIN" --role "$2" >/dev/null
}
for sa in web-sa engine-sa voice-sa jobs-sa; do
  grant "$sa" roles/datastore.user
  grant "$sa" roles/pubsub.publisher
done
grant engine-sa roles/cloudtasks.enqueuer
grant voice-sa roles/aiplatform.user
# Text-to-Speech has no role of its own: the caller only needs to be allowed to use the project's APIs.
grant voice-sa roles/serviceusage.serviceUsageConsumer

# engine creates tasks that call voice and engine as invoker-sa.
gcloud iam service-accounts add-iam-policy-binding "$INVOKER_SA" --project "$PROJECT" --quiet \
  --member "serviceAccount:engine-sa@$SA_DOMAIN" --role roles/iam.serviceAccountUser >/dev/null
# Pub/Sub signs its push requests as invoker-sa.
gcloud iam service-accounts add-iam-policy-binding "$INVOKER_SA" --project "$PROJECT" --quiet \
  --member "serviceAccount:service-$PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role roles/iam.serviceAccountTokenCreator >/dev/null

say "Firestore"
if ! gcloud firestore databases describe --database="(default)" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud firestore databases create --database="(default)" --project "$PROJECT" \
    --location="${FIRESTORE_LOCATION:-$REGION}" --type=firestore-native
fi
# Demo data and demo sessions delete themselves a day after they are written.
for group in demo_sessions demo_runs persons episodes calls timeline claims; do
  gcloud firestore fields ttls update expires_at --project "$PROJECT" \
    --collection-group="$group" --enable-ttl --async >/dev/null 2>&1 || true
done

say "Storage bucket (recordings are deleted after 7 days)"
gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file deploy/lifecycle.json >/dev/null
# Voice writes speech and recordings; web only reads recordings for the family page, so the public
# service can never change what is played on a call. An older setup gave web write access: taken back.
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --quiet \
  --member "serviceAccount:voice-sa@$SA_DOMAIN" --role roles/storage.objectUser >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --quiet \
  --member "serviceAccount:web-sa@$SA_DOMAIN" --role roles/storage.objectViewer >/dev/null
gcloud storage buckets remove-iam-policy-binding "gs://$BUCKET" --quiet \
  --member "serviceAccount:web-sa@$SA_DOMAIN" --role roles/storage.objectUser >/dev/null 2>&1 || true

say "Container registry"
gcloud artifacts repositories describe "$REPO" --project "$PROJECT" --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --project "$PROJECT" --location "$REGION" \
    --repository-format docker --description "Kuc-kuc images"

say "Pub/Sub topic"
gcloud pubsub topics describe events --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud pubsub topics create events --project "$PROJECT"

say "Cloud Tasks queues"
ensure_queue() { # ensure_queue <name> <flags...>
  local name="$1"
  shift
  if gcloud tasks queues describe "$name" --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
    gcloud tasks queues update "$name" --project "$PROJECT" --location "$REGION" "$@" >/dev/null
  else
    gcloud tasks queues create "$name" --project "$PROJECT" --location "$REGION" "$@" >/dev/null
  fi
}
# Real calls: one new call per second at most, whatever the backlog.
# Retries are spread over minutes, not the default fraction of a second, so a short outage of a
# service does not swallow a call or a timer. Whatever still gets lost, the watchdog reports.
ensure_queue calls --max-dispatches-per-second=1 --max-concurrent-dispatches=5 --max-attempts=5 \
  --min-backoff=10s --max-backoff=120s
ensure_queue calls-sim --max-dispatches-per-second=500 --max-concurrent-dispatches=1000 --max-attempts=3
ensure_queue timers --max-attempts=10 --min-backoff=10s --max-backoff=300s

say "Generated secrets"
ensure_random_secret() {
  gcloud secrets describe "$1" --project "$PROJECT" >/dev/null 2>&1 ||
    openssl rand -base64 48 | tr -d '\n' |
    gcloud secrets create "$1" --project "$PROJECT" --replication-policy automatic --data-file=- >/dev/null
}
ensure_random_secret session-secret
ensure_random_secret hash-salt
# The last part of the address Telegram delivers to, so letters and digits only.
gcloud secrets describe telegram-webhook-secret --project "$PROJECT" >/dev/null 2>&1 ||
  openssl rand -hex 24 | tr -d '\n' |
  gcloud secrets create telegram-webhook-secret --project "$PROJECT" --replication-policy automatic --data-file=- >/dev/null

say "Done. Secrets the owner still has to create by hand:"
for name in twilio-account-sid twilio-auth-token twilio-verify-sid twilio-from-number; do
  if gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1; then
    echo "  ok       $name"
  else
    echo "  MISSING  $name"
  fi
done
echo "For the family page (the site runs without them):"
for name in invite-codes telegram-bot-token; do
  if gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1; then
    echo "  ok       $name"
  else
    echo "  not yet  $name"
  fi
done
