#!/usr/bin/env python3
"""AI Lab: local-first job API, durable SQLite queue and provider dispatcher."""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("AI_LAB_DATA_DIR", str(ROOT / "data"))).expanduser()
DB = DATA_DIR / "jobs.sqlite3"
HOST = os.getenv("AI_LAB_HOST", "127.0.0.1")
PORT = int(os.getenv("AI_LAB_PORT", "8765"))
VALID_PROVIDERS = {"local-demo", "gcp-cloud-run", "webhook", "runpod-secure", "runpod-community"}
VALID_STATUSES = {"draft", "queued", "running", "completed", "failed", "cancelled", "interrupted"}
STOP = threading.Event()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    con = sqlite3.connect(DB, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            provider TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            result TEXT,
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status,created_at);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            message TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id,id);
        """)
        # Never automatically resubmit cloud jobs after a Mac restart.
        con.execute("UPDATE jobs SET status='interrupted',updated_at=?,error=? WHERE status='running'",
                    (now(), "The controller restarted while this job was running. Verify cloud execution before retrying."))


def event(con, job_id, message):
    con.execute("INSERT INTO events(job_id,created_at,message) VALUES(?,?,?)",
                (job_id, now(), str(message)[:4000]))


def as_job(row):
    item = dict(row)
    item["payload"] = json.loads(item["payload"])
    if item["result"]:
        try:
            item["result"] = json.loads(item["result"])
        except (ValueError, TypeError):
            pass
    return item


def get_job(job_id):
    with connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return as_job(row) if row else None


def validate(data, partial=False):
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    allowed = {"title", "provider", "payload", "enqueue"}
    if set(data) - allowed:
        raise ValueError("Unknown fields: " + ", ".join(sorted(set(data) - allowed)))
    if not partial or "title" in data:
        if not isinstance(data.get("title"), str) or not data["title"].strip() or len(data["title"]) > 160:
            raise ValueError("Title must have 1-160 characters.")
    if not partial or "provider" in data:
        if data.get("provider") not in VALID_PROVIDERS:
            raise ValueError("Choose a supported execution provider.")
    if "payload" in data and (not isinstance(data["payload"], dict) or len(json.dumps(data["payload"])) > 100000):
        raise ValueError("Payload must be a JSON object smaller than 100 KB.")
    if "enqueue" in data and not isinstance(data["enqueue"], bool):
        raise ValueError("enqueue must be true or false.")


def create_job(data):
    validate(data)
    jid = uuid.uuid4().hex[:12]
    stamp = now()
    status = "queued" if data.get("enqueue") else "draft"
    with connect() as con:
        con.execute("""INSERT INTO jobs(id,title,provider,payload,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (jid, data["title"].strip(), data["provider"],
                     json.dumps(data.get("payload", {})), status, stamp, stamp))
        event(con, jid, "Job created as " + status)
    return get_job(jid)


def edit_job(jid, data):
    validate(data, partial=True)
    with connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise LookupError("Job not found.")
        if row["status"] not in ("draft", "queued"):
            raise RuntimeError("Only draft and queued jobs can be edited.")
        fields = {
            "title": data.get("title", row["title"]).strip(),
            "provider": data.get("provider", row["provider"]),
            "payload": json.dumps(data["payload"]) if "payload" in data else row["payload"],
            "status": "queued" if data.get("enqueue") else row["status"],
            "updated_at": now()
        }
        con.execute("""UPDATE jobs SET title=:title,provider=:provider,payload=:payload,
                       status=:status,updated_at=:updated_at WHERE id=:id""", {**fields, "id": jid})
        event(con, jid, "Job edited")
    return get_job(jid)


def change_status(jid, action):
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise LookupError("Job not found.")
        status = row["status"]
        transitions = {
            "enqueue": ({"draft"}, "queued"),
            "cancel": ({"draft", "queued"}, "cancelled"),
            "retry": ({"failed", "cancelled", "interrupted"}, "queued")
        }
        if action not in transitions:
            raise ValueError("Unknown action.")
        allowed, target = transitions[action]
        if status not in allowed:
            raise RuntimeError("Cannot " + action + " a job in status " + status)
        con.execute("""UPDATE jobs SET status=?,updated_at=?,error=NULL,
                       started_at=NULL,finished_at=NULL,result=NULL WHERE id=?""",
                    (target, now(), jid))
        event(con, jid, action.capitalize() + " requested")
    return get_job(jid)


def delete_job(jid):
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise LookupError("Job not found.")
        if row["status"] == "running":
            raise RuntimeError("A running job cannot be deleted. Wait for it to finish.")
        con.execute("DELETE FROM events WHERE job_id=?", (jid,))
        con.execute("DELETE FROM jobs WHERE id=?", (jid,))


def claim_job():
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not row:
            return None
        jid = row["id"]
        con.execute("""UPDATE jobs SET status='running',updated_at=?,started_at=?,
                       attempts=attempts+1 WHERE id=?""", (now(), now(), jid))
        event(con, jid, "Execution started")
        return get_job(jid)


def gcp_token():
    try:
        result = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True,
                                text=True, timeout=25, check=True)
        token = result.stdout.strip()
        if not token:
            raise RuntimeError("gcloud returned an empty access token.")
        return token
    except FileNotFoundError as exc:
        raise RuntimeError("Install the Google Cloud CLI (gcloud) to use GCP.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Google Cloud login failed: " + (exc.stderr or "")[-800:]) from exc


def request_json(url, method="GET", body=None, token=None, webhook=False):
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read(1000000).decode()
            return json.loads(raw) if raw else {"status": response.status}
    except urllib.error.HTTPError as exc:
        detail = exc.read(1500).decode(errors="replace")
        raise RuntimeError("Provider HTTP " + str(exc.code) + ": " + detail) from exc



def fetch_gcp_worker_result(job):
    """Fetch AI_LAB_RESULT from Cloud Logging, matching the actual worker job ID."""
    project = os.getenv("GCP_PROJECT", "").strip()
    name = os.getenv("GCP_IMAGE_JOB" if job.get("payload", {}).get("task") == "image" else "GCP_JOB", "").strip()
    if not project or not name:
        raise RuntimeError("GCP_PROJECT and GCP_JOB are required to read results.")
    filter_parts = [
        'resource.type="cloud_run_job"',
        'resource.labels.job_name="' + name.replace('"', '') + '"',
        'textPayload:"AI_LAB_RESULT"'
    ]
    if job.get("started_at"):
        filter_parts.append('timestamp>="' + job["started_at"] + '"')
    query = {"resourceNames": ["projects/" + project],
             "filter": " AND ".join(filter_parts),
             "orderBy": "timestamp desc", "pageSize": 100}
    token = gcp_token()
    for _ in range(5):
        response = request_json("https://logging.googleapis.com/v2/entries:list",
                                method="POST", body=query, token=token)
        for entry in response.get("entries", []):
            line = entry.get("textPayload", "")
            if not line and isinstance(entry.get("jsonPayload"), dict):
                line = entry["jsonPayload"].get("message", "")
            if not isinstance(line, str):
                continue
            marker = "AI_LAB_RESULT "
            at = line.find(marker)
            if at < 0:
                continue
            try:
                artifact = json.loads(line[at + len(marker):])
            except (ValueError, TypeError):
                continue
            if isinstance(artifact, dict) and artifact.get("job_id") == job["id"]:
                return artifact
        if not response.get("nextPageToken"):
            break
        query["pageToken"] = response["nextPageToken"]
    return None


def recover_gcp_result(job_id):
    job = get_job(job_id)
    if not job:
        raise LookupError("Job not found.")
    if job["provider"] != "gcp-cloud-run" or job["status"] != "completed":
        raise RuntimeError("Result retrieval requires a completed GCP job.")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if result.get("artifact"):
        return job
    artifact = fetch_gcp_worker_result(job)
    if artifact is None:
        raise RuntimeError("No matching worker result found in Cloud Logging. "
                           "Wait for log ingestion or check Cloud Run logs and roles/logging.viewer.")
    with connect() as con:
        con.execute("UPDATE jobs SET result=?,updated_at=? WHERE id=?",
                    (json.dumps({**result, "artifact": artifact}), now(), job_id))
        event(con, job_id, "Worker result retrieved from Cloud Logging")
    return get_job(job_id)


def run_gcp(job):
    project = os.getenv("GCP_PROJECT", "").strip()
    region = os.getenv("GCP_REGION", "").strip()
    name = os.getenv("GCP_IMAGE_JOB" if job["payload"].get("task") == "image" else "GCP_JOB", "").strip()
    if not all((project, region, name)):
        raise RuntimeError("Set GCP_PROJECT, GCP_REGION and the relevant GCP_JOB or GCP_IMAGE_JOB.")
    # Cloud Run Job container should accept one JSON command-line argument.
    url = "https://run.googleapis.com/v2/projects/{}/locations/{}/jobs/{}:run".format(project, region, name)
    payload_string = json.dumps({"job_id": job["id"], "payload": job["payload"]})
    operation = request_json(url, method="POST",
                             body={"overrides": {"containerOverrides": [{"args": [payload_string]}]}},
                             token=gcp_token())
    operation_name = operation.get("name")
    if not operation_name or not operation_name.startswith("projects/"):
        raise RuntimeError("GCP returned no valid operation name: " + str(operation)[:500])
    with connect() as con:
        con.execute("UPDATE jobs SET result=?,updated_at=? WHERE id=?",
                    (json.dumps({"operation": operation_name, "state": "submitted"}), now(), job["id"]))
        event(con, job["id"], "GCP operation: " + operation_name)
    deadline = time.monotonic() + int(os.getenv("GCP_POLL_TIMEOUT_SECONDS", "7200"))
    while time.monotonic() < deadline and not STOP.is_set():
        time.sleep(5)
        operation = request_json("https://run.googleapis.com/v2/" + operation_name, token=gcp_token())
        if operation.get("done"):
            if operation.get("error"):
                raise RuntimeError("GCP execution failed: " + json.dumps(operation["error"])[:1200])
            result = {"operation": operation_name, "response": operation.get("response", {})}
            bucket = os.getenv("AI_LAB_RESULTS_BUCKET", "").strip()
            if bucket:
                # Cloud worker uploads result.json after processing; this is an optional
                # convenience, not a second source of execution status.
                uri = "gs://{}/ai-lab/jobs/{}/result.json".format(bucket, job["id"])
                result["result_uri"] = uri
                try:
                    downloaded = subprocess.run(["gcloud", "storage", "cat", uri],
                                                capture_output=True, text=True, timeout=40, check=True)
                    result["artifact"] = json.loads(downloaded.stdout)
                except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    result["artifact_error"] = "Cloud execution succeeded but result download failed: " + str(exc)[:500]
            if not result.get("artifact"):
                for attempt in range(3):
                    try:
                        artifact = fetch_gcp_worker_result(job)
                        if artifact:
                            result["artifact"] = artifact
                            result["artifact_source"] = "cloud-logging"
                            break
                    except Exception as exc:
                        result["artifact_error"] = "Log retrieval: " + str(exc)[:500]
                        break
                    if attempt < 2:
                        time.sleep(3)
            return result
    raise RuntimeError("GCP execution still unknown after polling stopped; inspect operation " + operation_name)


def run_webhook(job):
    url = os.getenv("AI_LAB_WEBHOOK_URL", "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("AI_LAB_WEBHOOK_URL must be an HTTPS URL.")
    response = request_json(url, method="POST",
                            body={"job_id": job["id"], "title": job["title"], "payload": job["payload"]},
                            token=os.getenv("AI_LAB_WEBHOOK_TOKEN", "") or None)
    # A webhook submission acknowledges dispatch, not remote execution completion.
    return {"submitted": True, "provider_response": response}


def execute(job):
    if job["provider"] == "local-demo":
        time.sleep(2)
        return {"demo": True, "message": "Demo finished. No AI model was run.",
                "input": job["payload"]}
    if job["provider"] == "gcp-cloud-run":
        return run_gcp(job)
    if job["provider"] in ("runpod-secure", "runpod-community"):
        import runpod_backend
        return runpod_backend.run(job)
    if job["provider"] == "webhook":
        return run_webhook(job)
    raise RuntimeError("Unsupported provider.")


def worker():
    while not STOP.is_set():
        job = claim_job()
        if not job:
            STOP.wait(1)
            continue
        try:
            result = execute(job)
            with connect() as con:
                con.execute("UPDATE jobs SET status='completed',result=?,updated_at=?,finished_at=? WHERE id=?",
                            (json.dumps(result), now(), now(), job["id"]))
                event(con, job["id"], "Execution completed")
        except Exception as exc:
            with connect() as con:
                con.execute("UPDATE jobs SET status='failed',error=?,updated_at=?,finished_at=? WHERE id=?",
                            (str(exc)[:3000], now(), now(), job["id"]))
                event(con, job["id"], "Execution failed: " + str(exc)[:1000])



def image_object_path(job):
    artifact = (job.get("result") or {}).get("artifact") or {}
    output = artifact.get("output") or {}
    uri = output.get("image_uri", "")
    if not isinstance(uri, str) or not uri.startswith("gs://"):
        raise LookupError("This job has no generated image artifact.")
    bucket = os.getenv("AI_LAB_RESULTS_BUCKET", "")
    expected = "gs://{}/ai-lab/jobs/{}/image.png".format(bucket, job["id"])
    if not bucket or uri != expected:
        raise ValueError("Image path does not match configured result bucket and job ID.")
    return uri


def read_image(job_id):
    job = get_job(job_id)
    if not job:
        raise LookupError("Job not found.")
    if job["status"] != "completed":
        raise RuntimeError("Only completed jobs can serve images.")
    if job["provider"] in ("runpod-secure", "runpod-community"):
        path = DATA_DIR / "artifacts" / (job["id"] + ".png")
        if not path.is_file():
            raise LookupError("RunPod image artifact not present locally.")
        raw = path.read_bytes()
        if len(raw) > 15000000 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Invalid local PNG.")
        return raw
    if job["provider"] != "gcp-cloud-run":
        raise RuntimeError("Unsupported image provider.")
    uri = image_object_path(job)
    raw = subprocess.run(["gcloud", "storage", "cat", uri],
                         capture_output=True, timeout=90, check=True)
    if len(raw.stdout) > 20 * 1024 * 1024 or not raw.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Result is not a valid PNG image under 20 MB.")
    return raw.stdout


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args), flush=True)

    def send_json(self, obj, status=200):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 120000:
            raise ValueError("Request too large.")
        return json.loads(self.rfile.read(length)) if length else {}

    def handle_api(self, method):
        try:
            parts = self.path.split("?")[0].strip("/").split("/")
            if parts == ["api", "health"] and method == "GET":
                return self.send_json({"ok": True, "version": "0.2", "providers": sorted(VALID_PROVIDERS),
                                       "gcp_configured": all(os.getenv(k) for k in ("GCP_PROJECT", "GCP_REGION", "GCP_JOB")),
                                       "gcp_project": os.getenv("GCP_PROJECT", ""),
                                       "gcp_region": os.getenv("GCP_REGION", ""),
                                       "gcp_job": os.getenv("GCP_JOB", ""),
                                       "gcp_image_job": os.getenv("GCP_IMAGE_JOB", ""),
                                       "results_bucket_configured": bool(os.getenv("AI_LAB_RESULTS_BUCKET")),
                                       "runpod_configured": bool(os.getenv("RUNPOD_API_KEY") and os.getenv("RUNPOD_IMAGE"))})
            if parts == ["api", "runpod", "gpus"] and method == "GET":
                from urllib.parse import parse_qs, urlsplit
                import runpod_backend
                tier = parse_qs(urlsplit(self.path).query).get("cloud", ["SECURE"])[0]
                return self.send_json({"cloud": tier, "gpus": runpod_backend.available_gpus(tier)})
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "runpod-live" and method == "GET":
                job = get_job(parts[2])
                if not job:
                    raise LookupError("Job not found.")
                if job["provider"] not in ("runpod-community", "runpod-secure"):
                    raise ValueError("Only RunPod jobs provide RunPod telemetry.")
                import runpod_backend
                return self.send_json(runpod_backend.live_details(job))
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "image" and method == "GET":
                binary = read_image(parts[2])
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(binary)))
                self.send_header("Cache-Control", "private, max-age=60")
                self.end_headers()
                self.wfile.write(binary)
                return
            if parts == ["api", "jobs"]:
                if method == "GET":
                    with connect() as con:
                        jobs = [as_job(r) for r in con.execute("SELECT * FROM jobs ORDER BY created_at DESC,id DESC LIMIT 500")]
                    return self.send_json({"jobs": jobs})
                if method == "POST":
                    return self.send_json(create_job(self.read_json()), 201)
            if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
                jid = parts[2]
                if len(parts) == 3:
                    if method == "GET":
                        job = get_job(jid)
                        if not job:
                            raise LookupError("Job not found.")
                        with connect() as con:
                            job["events"] = [dict(r) for r in con.execute(
                                "SELECT created_at,message FROM events WHERE job_id=? ORDER BY id DESC LIMIT 100", (jid,))]
                        return self.send_json(job)
                    if method == "PATCH":
                        return self.send_json(edit_job(jid, self.read_json()))
                    if method == "DELETE":
                        delete_job(jid)
                        return self.send_json({"deleted": True})
                if len(parts) == 4 and method == "POST":
                    if parts[3] == "fetch-result":
                        return self.send_json(recover_gcp_result(jid))
                    return self.send_json(change_status(jid, parts[3]))
            self.send_json({"error": "Not found."}, 404)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, 409)
        except Exception as exc:
            self.send_json({"error": "Internal error: " + str(exc)}, 500)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            body = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.handle_api("GET")

    def do_POST(self):
        self.handle_api("POST")

    def do_PATCH(self):
        self.handle_api("PATCH")

    def do_DELETE(self):
        self.handle_api("DELETE")


def main():
    init_db()
    import runpod_backend
    runpod_backend.cleanup_orphans()
    thread = threading.Thread(target=worker, name="ai-lab-dispatcher", daemon=True)
    thread.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("AI Lab running at http://{}:{}".format(HOST, PORT), flush=True)
    print("Job database:", DB, flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
