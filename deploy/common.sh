#!/usr/bin/env bash
# Names shared by setup.sh and deploy.sh. Source it; do not run it.

PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-europe-west1}"
if [ -z "$PROJECT" ] || [ "$PROJECT" = "(unset)" ]; then
  echo "No project. Run: gcloud config set project <id>   (or set GCP_PROJECT)" >&2
  exit 1
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
BUCKET="$PROJECT-kuckuc"
REPO="kuckuc"
SA_DOMAIN="$PROJECT.iam.gserviceaccount.com"
INVOKER_SA="invoker-sa@$SA_DOMAIN"

# Cloud Run gives every service a predictable address, so services can know each
# other's URL before the first deploy.
VOICE_URL="https://voice-$PROJECT_NUMBER.$REGION.run.app"
ENGINE_URL="https://engine-$PROJECT_NUMBER.$REGION.run.app"
WEB_URL="https://web-$PROJECT_NUMBER.$REGION.run.app"

say() { printf '\n== %s\n' "$*"; }
