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
