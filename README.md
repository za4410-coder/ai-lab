# AI Lab Control Center

A local-first Mac dashboard for creating, editing, deleting, queuing and tracking AI jobs. Runs entirely on Python's standard library: **no npm, Docker, Redis, or pip install required**.

## Quick start (macOS)

Requires Python 3.10+.

~~~bash
git clone https://github.com/za4410-coder/ai-lab.git
cd ai-lab
python3 app.py
~~~

Open **http://127.0.0.1:8765** in a browser. Create a **Local demo** job and select **Queue immediately after saving**, or create a draft and click **Run**. The demo waits two seconds and reports success without running a real model or spending cloud money.

Jobs and history persist in \`data/jobs.sqlite3\` (created automatically and ignored by Git).

You can customize the port and database directory:

~~~bash
AI_LAB_PORT=9000 AI_LAB_DATA_DIR="$HOME/.ai-lab" python3 app.py
~~~


## RunPod Secure and Community Cloud (v0.5)

Select **RunPod — Secure Cloud** or **RunPod — Community Cloud** from the dashboard New Job provider dropdown. A queued image job provisions a paid GPU Pod via RunPod REST v2, runs the open-weight SDXL model, retrieves a PNG over an authenticated HTTPS endpoint, and attempts to terminate the Pod. The image is saved locally in data/artifacts/JOB_ID.png.

### Configure RunPod

1. Sign up at https://www.runpod.io, add funds, and create an API key. Keep the key on your Mac.
2. Build and publish the worker image to your **own public Docker Hub repository** (Docker Desktop and buildx required):

~~~bash
cd ailab
git pull origin main
docker login
docker buildx build --platform linux/amd64 -f runpod_worker/Dockerfile -t YOUR_DOCKERHUB_USERNAME/ai-lab-sdxl:0.5 --push .
~~~

3. Launch the dashboard with your own API key and image reference:

~~~bash
export RUNPOD_API_KEY="YOUR_RUNPOD_API_KEY"
export RUNPOD_IMAGE="YOUR_DOCKERHUB_USERNAME/ai-lab-sdxl:0.5"
export RUNPOD_GPU_ID="NVIDIA GeForce RTX 4090"
export RUNPOD_MAX_SECONDS=1800
python3 app.py
~~~

Choose New Job, select RunPod Secure or Community, use the Generate image template and check Queue immediately.

Example JSON payload:

~~~json
{"task":"image","prompt":"A cinematic laboratory at night","width":1024,"height":1024,"steps":25,"seed":42}
~~~

The worker is built from runpod_worker/Dockerfile and reuses the SDXL inference module. No GCP quota, Cloud Storage bucket or hosted generative AI model API is required. RunPod GPU model availability can differ between Secure and Community; adjust RUNPOD_GPU_ID according to the RunPod catalog.

### Billing and privacy

**RunPod Pods incur charges while provisioned.** The controller attempts termination after success, failure, or timeout (30 minutes by default, configurable up to 60). It saves the created pod ID in SQLite and tries to terminate abandoned Pods when restarting. **This is not guaranteed cleanup**: Mac shutdown, loss of connectivity, or API failures can leave billable Pods running. Check https://www.runpod.io/console/pods after initial tests; manually terminate abandoned Pods immediately. We do not yet have a remote watchdog or dollar cap.

A temporary HTTPS proxy endpoint exposes port 8000 on RunPod, but its status and image routes require a random per-job Bearer token. The Mac saves the PNG locally with restricted permissions before deleting the Pod. Job prompts and the temporary token are supplied in Pod environment variables; provider and physical-host administrators may be able to inspect data, and Community hosts introduce additional parties. Use Secure Cloud for more sensitive workloads and avoid sharing raw sensitive footage with marketplace hosts.

First Pod provisioning plus model weight downloads can take several minutes. If Docker Hub denies image pulls, ensure your worker image is publicly pullable and built for Linux AMD64. GPU types and pricing are not guaranteed; review the RunPod console before submitting paid workloads.


## Open-model GPU image generation (v0.4)

The image flow **does not call Gemini, Imagen, OpenAI image APIs, or any other hosted model inference API**. An independent Cloud Run GPU worker downloads the open-weight **Stable Diffusion XL 1.0** model from Hugging Face and runs Diffusers/PyTorch on an NVIDIA L4 GPU. SDXL has an OpenRAIL++ model license; review its usage terms before commercial deployment. The GPU worker source is in `gcp_image_worker/`.

### One-time deployment

Use your existing GCP project and active gcloud login. You must configure a **private results bucket**, because PNG images should not be embedded into Cloud Logging. The deployment helper can create the bucket if it doesn't exist.

~~~bash
cd ailab
git pull origin main
export GCP_PROJECT=ai-lab-515319
export GCP_REGION=us-central1
export GCP_JOB=ai-lab-worker
export GCP_IMAGE_JOB=ai-lab-image-worker
export AI_LAB_RESULTS_BUCKET=ai-lab-515319-results
chmod +x deploy_gcp_image.sh
./deploy_gcp_image.sh
~~~

The helper explicitly asks for confirmation. It creates a dedicated GPU service account with **objectCreator** access to the results bucket, builds an image in Artifact Registry and deploys a **different** Cloud Run Job with **one NVIDIA L4 24 GB**, 4 vCPU, 16 GiB RAM, one task, no retries and a 30-minute task timeout. **Deployment itself does not execute GPU inference**, but building/storing the container and output files can have charges. If your project lacks GPU quota or your account lacks access to the worker service account, GCP will give a precise deployment error.

Ensure your local `gcloud` account has permission to read objects in the results bucket, such as `roles/storage.objectViewer`; your project owner account often has the needed permissions already. The execution identity needs to download public Hugging Face model files and may require outbound network access. Model weights are downloaded on each cold start, adding latency and potential external network usage; future versions can cache the model in a separate private asset store.

### Start and run

Run the controller from the same shell with all variables still exported. If you use `SSL_CERT_FILE` for your Python HTTPS certificates, set it first.

~~~bash
python3 app.py
~~~

Visit http://127.0.0.1:8765 and choose **New Job → Generate image · SDXL open model · NVIDIA L4**. The template sets GCP as provider with this input:

~~~json
{
  "task": "image",
  "prompt": "A cinematic photograph of a futuristic research laboratory at night, warm dramatic lighting, intricate details",
  "width": 1024,
  "height": 1024,
  "steps": 25,
  "seed": 42
}
~~~

Check **Queue immediately after saving** and save. The local dispatcher routes image tasks to `GCP_IMAGE_JOB` and echo tasks to your existing inexpensive CPU `GCP_JOB`.

The GPU worker uploads a PNG at `gs://BUCKET/ai-lab/jobs/JOB_ID/image.png` plus `result.json` to the same private bucket. The controller loads result JSON, renders a preview using the local `/api/jobs/JOB_ID/image` endpoint, and offers an **Open full-size image** link. No public bucket permissions or signed URLs are needed. For completed image jobs with missing result JSON, open **Details → Fetch worker result** to retrieve the matching Cloud Logging metadata.

**Costs and limits:** Cloud Run GPU executions incur GPU, CPU and memory charges; model download/build time may be significant. The job is capped at 30 minutes but has **no per-job dollar hard limit**. A Cloud Run spend cap configured earlier applies subject to your Cloud Billing cap eligibility and scope; always check the configured rules. Image requests are restricted to one PNG, dimensions up to 1024 × 1024, 50 inference steps, no automatic retries, and one execution at a time from the local queue.

**Privacy:** Model inference uses GCP hardware but not Google's managed generative AI inference services. Prompts and images still transit and reside in GCP; Hugging Face supplies publicly available model weights. Access to results is controlled via Cloud Storage IAM. Cloud Run's arguments and Cloud Logging may contain your prompts, so don't use confidential personal images or secrets in the initial test.


## Read human-readable GCP results in the dashboard (v0.3)

The dashboard now renders a **Worker output** card from actual remote result JSON, including task and elapsed duration. When a Cloud Run Job completes, the controller first tries the optional GCS result bucket and then queries **Cloud Logging** for the worker's `AI_LAB_RESULT` marker. No Cloud Storage bucket is required for simple `echo` / `sha256` / `sleep` test results.

**For an existing completed job:** open its **Details** dialog and click **Fetch worker result**. This retrieves the already-executed result and stores it locally in SQLite; it does not run or charge for another Cloud Run execution. If Cloud Logging has not ingested the log yet, wait briefly and try the same button again. Cloud Logging read permissions (such as `roles/logging.viewer`) may be required for your signed-in Google account. The lookup matches the parsed `job_id` exactly, so output from different jobs isn't mixed together.

Raw Cloud Run operation metadata remains visible under **Technical result** for diagnostics. Jobs using a custom container must print `AI_LAB_RESULT ` followed by a JSON object including the matching `job_id` to use this feature. Cloud Logging retention policies apply; if the logs have expired and no bucket was configured, the old output cannot be recovered automatically.

## GCP Cloud Run worker — end-to-end setup (v0.2)

This repository now contains a **real, deployable Cloud Run Job container** in `gcp_worker/`. It runs safe CPU-only validation tasks, not GPU video inference. Deployment is opt-in; nothing has been deployed into your GCP account.

### Prerequisites

1. Have a Google Cloud project with billing enabled and permissions to deploy Cloud Run Jobs and build source images.
2. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) on your Mac, then run `gcloud auth login`.
3. Set the target project and region:

~~~bash
export GCP_PROJECT="YOUR_PROJECT_ID"
export GCP_REGION="us-central1"
export GCP_JOB="ai-lab-worker"
gcloud config set project "$GCP_PROJECT"
~~~

### Optional: persist result.json in Cloud Storage

Create a dedicated, **private** bucket with a globally unique name if you want result JSON shown in the local dashboard. The bucket must be in an appropriate region.

~~~bash
export AI_LAB_RESULTS_BUCKET="YOUR_UNIQUE_BUCKET_NAME"
gcloud storage buckets create "gs://$AI_LAB_RESULTS_BUCKET" --location="$GCP_REGION" --uniform-bucket-level-access
~~~

For a least-privilege setup, create a dedicated execution service account, grant it object creation for this bucket, and allow your local gcloud identity to read artifacts. Replace placeholders with your actual project ID:

~~~bash
export GCP_SERVICE_ACCOUNT="ai-lab-worker@$GCP_PROJECT.iam.gserviceaccount.com"
gcloud iam service-accounts create ai-lab-worker --display-name="AI Lab Cloud Run Worker"
gcloud storage buckets add-iam-policy-binding "gs://$AI_LAB_RESULTS_BUCKET" \
  --member="serviceAccount:$GCP_SERVICE_ACCOUNT" --role="roles/storage.objectCreator"
~~~

Granting permission to deploy a job using that service account may also require `roles/iam.serviceAccountUser` for your deployer identity. The worker service account needs no permissions other than writing the result files. Your local identity needs permission to read files from the bucket (`roles/storage.objectViewer`). Do not create public buckets.

### Deploy from the repo

~~~bash
chmod +x deploy_gcp.sh
./deploy_gcp.sh
~~~

The script enables the Cloud Run, Cloud Build and Artifact Registry APIs and builds/deploys a 1-vCPU, 512-MiB, one-task, max-15-minute **Cloud Run Job** from `gcp_worker/`. It does **not** execute the job during deployment. Google Cloud may charge for builds, storage and executions. Review the deployment settings before running.

Now start the local dashboard from the **same terminal** so it inherits your environment variables:

~~~bash
python3 app.py
~~~

Visit http://127.0.0.1:8765, select **New Job → Google Cloud Run Job**, and use this payload:

~~~json
{"task":"echo","text":"Hello from my Mac"}
~~~

Check **Queue immediately after saving**. The local controller submits a new GCP execution, records its operation ID, polls completion and displays the returned result. When the bucket is configured, the worker uploads `gs://YOUR_BUCKET/ai-lab/jobs/JOB_ID/result.json`, which the Mac reads into the dashboard. The worker also emits an `AI_LAB_RESULT` structured log line to Cloud Logging.

Additional payloads:

~~~json
{"task":"sha256","text":"A private test string"}
~~~

~~~json
{"task":"sleep","seconds":10}
~~~

**Execution status note:** Cloud Run operation success means the container completed; this demo doesn't generate media yet. `sleep` is just for validating polling. Cloud Run logs can be viewed with `gcloud run jobs executions list --job="$GCP_JOB" --region="$GCP_REGION"`.

**Safety:** Stop the Mac controller only when jobs are idle if possible. A controller restart marks active jobs `interrupted` and will not automatically resubmit them. Check Cloud Run executions before using Retry to avoid duplicate charges. Workloads and payload arguments may appear in cloud logs and should not contain credentials or sensitive footage. Cloud Run jobs are a managed execution target; later GPU workloads will use a dedicated model container, not this simple CPU test worker.

---

### First release features

- CRUD job dashboard, filter/search, auto-refresh every 3 seconds, job activity history and results
- Local SQLite durable queue (single worker; FIFO), retries, cancellations of queued jobs
- Cloud Run Jobs integration via Google Cloud CLI credentials
- Generic HTTPS webhook connector for external worker providers
- Safe startup recovery: previously running jobs become **interrupted** instead of automatically retrying and potentially double-billing a cloud job
- Default binding to localhost only, with no inbound authentication (do **not** expose directly to the internet)

### GCP Cloud Run Jobs setup

The adapter is for an **already-created Cloud Run Job**, not GPU provisioning or model deployment. The job's container must accept **one JSON string command-line argument**. The argument has the shape:

~~~json
{"job_id":"abc123","payload":{"prompt":"A cinematic landscape","resolution":"1080p"}}
~~~

Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), authenticate and select the project:

~~~bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
export GCP_PROJECT=YOUR_PROJECT_ID
export GCP_REGION=us-central1
export GCP_JOB=YOUR_PREDEPLOYED_CLOUD_RUN_JOB_NAME
python3 app.py
~~~

Your GCP identity needs permission to execute the job **with overrides** and read the resulting operation, plus billing must be enabled. The Cloud Run job container itself performs the model work and manages output storage. The app sends the JSON payload via the Cloud Run Jobs v2 \`jobs:run\` REST API with \`containerOverrides.args\`, then polls its long-running operation. Review your GCP job's GPU, concurrency, duration and costs before enqueuing a cloud job. A completed Cloud Run operation means the job terminated successfully; it does not import media artifacts into the dashboard.

**Do not place confidential credentials in a job payload**: job arguments, logs and response history may be visible in GCP.

### External webhook provider

Configure your own provider gateway (RunPod, Vast.ai, or any provider-specific service) with an HTTPS endpoint:

~~~bash
export AI_LAB_WEBHOOK_URL=https://your-gateway.example.com/submit
export AI_LAB_WEBHOOK_TOKEN=YOUR_SECRET_TOKEN
python3 app.py
~~~

The controller sends:

~~~json
{"job_id":"abc123","title":"Example","payload":{"prompt":"test"}}
~~~

The token is sent as a Bearer header. A successful webhook HTTP response is recorded as **submitted/completed locally**, **not proof that remote GPU work finished**. Remote result polling, callbacks, and true cancellation are not part of v0.1.

### Operational limitations

- Only one background worker; queued jobs execute sequentially.
- No API login, user accounts, file uploads, media gallery, estimated costs, schedule/calendar, or remote GPU discovery yet.
- Editing is allowed only for draft/queued jobs. Cancellation stops a job before it starts, not an already-running remote job.
- Deletion is disabled while running.
- On restart, running jobs become **interrupted**. Check the provider console before retrying to avoid duplicate charges.
- The local worker must stay running for queued jobs to execute. Cloud Run Jobs themselves may continue independently after the Mac controller stops.
- Local demo does **not** generate video; that requires deploying a real model worker.
- Do not set \`AI_LAB_HOST=0.0.0.0\` unless you add authentication and network protections.

### API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /api/health | Controller health |
| GET | /api/jobs | List latest 500 jobs |
| POST | /api/jobs | Create draft or queued job |
| GET | /api/jobs/{id} | Job details and activity |
| PATCH | /api/jobs/{id} | Edit draft/queued job |
| DELETE | /api/jobs/{id} | Delete non-running job |
| POST | /api/jobs/{id}/enqueue | Queue draft job |
| POST | /api/jobs/{id}/cancel | Cancel draft/queued job |
| POST | /api/jobs/{id}/retry | Retry failed/cancelled/interrupted |

Example:

~~~bash
curl -s http://127.0.0.1:8765/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"title":"My first job","provider":"local-demo","payload":{"prompt":"hello"},"enqueue":true}'
~~~

### Next milestones

1. Real local model worker and dedicated video-generation workflow (ComfyUI/other model runtimes).
2. GPU VM provider lifecycle, runtime cost estimate, budgets, and automatic shutdown.
3. Artifacts in local/S3/GCS storage with preview, thumbnails and metadata.
4. Concurrent workers, priority and scheduling windows (including overnight jobs).
5. Secure remote access with authentication and audit trails.
