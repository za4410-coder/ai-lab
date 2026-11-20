#!/usr/bin/env bash
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT to your Google Cloud project ID}"
GCP_REGION="${GCP_REGION:-us-central1}"
GCP_JOB="${GCP_JOB:-ai-lab-worker}"
AI_LAB_RESULTS_BUCKET="${AI_LAB_RESULTS_BUCKET:-}"
SERVICE_ACCOUNT="${GCP_SERVICE_ACCOUNT:-}"

gcloud config set project "$GCP_PROJECT"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

args=(run jobs deploy "$GCP_JOB"
  --project "$GCP_PROJECT"
  --region "$GCP_REGION"
  --source "./gcp_worker"
  --tasks 1
  --max-retries 0
  --task-timeout 15m
  --cpu 1
  --memory 512Mi)

if [[ -n "$SERVICE_ACCOUNT" ]]; then
  args+=(--service-account "$SERVICE_ACCOUNT")
fi
if [[ -n "$AI_LAB_RESULTS_BUCKET" ]]; then
  args+=(--set-env-vars "AI_LAB_RESULTS_BUCKET=$AI_LAB_RESULTS_BUCKET")
fi

echo "Deploying job $GCP_JOB to project $GCP_PROJECT in $GCP_REGION (no execution yet)."
gcloud "${args[@]}"
echo
echo "Deploy complete. Start the local controller with:"
echo "  export GCP_PROJECT='$GCP_PROJECT'"
echo "  export GCP_REGION='$GCP_REGION'"
echo "  export GCP_JOB='$GCP_JOB'"
if [[ -n "$AI_LAB_RESULTS_BUCKET" ]]; then
  echo "  export AI_LAB_RESULTS_BUCKET='$AI_LAB_RESULTS_BUCKET'"
fi
echo "  python3 app.py"
