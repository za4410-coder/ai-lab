#!/usr/bin/env bash
set -euo pipefail
: "${GCP_PROJECT:?Set GCP_PROJECT}"
: "${AI_LAB_RESULTS_BUCKET:?Set AI_LAB_RESULTS_BUCKET to a private bucket}"
GCP_REGION="${GCP_REGION:-us-central1}"
GCP_IMAGE_JOB="${GCP_IMAGE_JOB:-ai-lab-image-worker}"
GCP_GPU_SERVICE_ACCOUNT="${GCP_GPU_SERVICE_ACCOUNT:-ai-lab-gpu-worker@${GCP_PROJECT}.iam.gserviceaccount.com}"
REPOSITORY="ai-lab-gpu"
IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPOSITORY}/sdxl:latest"

echo "About to create/update a GPU Cloud Run Job (NVIDIA L4, 4 CPU, 16 GiB RAM)."
echo "Project: ${GCP_PROJECT}  Region: ${GCP_REGION}  Job: ${GCP_IMAGE_JOB}"
echo "This enables paid GPU execution ONLY when a dashboard image job is submitted."
read -r -p "Continue? [y/N] " answer
[[ "${answer}" == "y" || "${answer}" == "Y" ]] || exit 1

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com logging.googleapis.com --project="$GCP_PROJECT"
gcloud iam service-accounts describe "$GCP_GPU_SERVICE_ACCOUNT" --project="$GCP_PROJECT" >/dev/null 2>&1 || \
 gcloud iam service-accounts create ai-lab-gpu-worker --project="$GCP_PROJECT" --display-name="AI Lab GPU Worker"
gcloud storage buckets describe "gs://$AI_LAB_RESULTS_BUCKET" --project="$GCP_PROJECT" >/dev/null 2>&1 || \
 gcloud storage buckets create "gs://$AI_LAB_RESULTS_BUCKET" --project="$GCP_PROJECT" --location="$GCP_REGION" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding "gs://$AI_LAB_RESULTS_BUCKET" \
 --member="serviceAccount:$GCP_GPU_SERVICE_ACCOUNT" --role="roles/storage.objectCreator"
if ! gcloud artifacts repositories describe "$REPOSITORY" --location="$GCP_REGION" --project="$GCP_PROJECT" >/dev/null 2>&1; then
 gcloud artifacts repositories create "$REPOSITORY" --repository-format=docker --location="$GCP_REGION" --project="$GCP_PROJECT"
fi
gcloud builds submit ./gcp_image_worker --tag="$IMAGE" --project="$GCP_PROJECT"
gcloud run jobs deploy "$GCP_IMAGE_JOB" --image="$IMAGE" --project="$GCP_PROJECT" --region="$GCP_REGION" \
 --service-account="$GCP_GPU_SERVICE_ACCOUNT" \
 --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy \
 --cpu=4 --memory=16Gi --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=30m \
 --set-env-vars="AI_LAB_RESULTS_BUCKET=$AI_LAB_RESULTS_BUCKET"
echo "GPU worker deployed but NOT executed."
echo "Export GCP_IMAGE_JOB=$GCP_IMAGE_JOB and AI_LAB_RESULTS_BUCKET=$AI_LAB_RESULTS_BUCKET when launching app.py"
