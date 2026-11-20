#!/usr/bin/env python3
"""Small Cloud Run worker: deterministic CPU tasks for end-to-end integration tests."""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

def main():
    if len(sys.argv) != 2:
        raise SystemExit("Expected exactly one JSON job argument")
    request = json.loads(sys.argv[1])
    job_id = request["job_id"]
    payload = request.get("payload", {})
    if not isinstance(job_id, str) or not job_id.isalnum() or len(job_id) > 64:
        raise ValueError("Invalid job ID")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    task = payload.get("task", "echo")
    start = time.monotonic()
    print(json.dumps({"event": "started", "job_id": job_id, "task": task}), flush=True)

    if task == "echo":
        output = {"echo": payload.get("text", payload.get("prompt", ""))}
    elif task == "sha256":
        text = payload.get("text", "")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        output = {"sha256": hashlib.sha256(text.encode()).hexdigest()}
    elif task == "sleep":
        seconds = payload.get("seconds", 3)
        if not isinstance(seconds, (int, float)) or not 0 <= seconds <= 300:
            raise ValueError("seconds must be between 0 and 300")
        time.sleep(seconds)
        output = {"slept_seconds": seconds}
    else:
        raise ValueError("Unsupported task. Supported: echo, sha256, sleep")

    result = {
        "job_id": job_id,
        "task": task,
        "output": output,
        "duration_seconds": round(time.monotonic() - start, 3),
        "finished_at": datetime.now(timezone.utc).isoformat()
    }
    bucket = os.getenv("AI_LAB_RESULTS_BUCKET", "").strip()
    if bucket:
        if not all(c.isalnum() or c in "-_." for c in bucket):
            raise ValueError("Invalid results bucket")
        from google.cloud import storage
        object_name = "ai-lab/jobs/{}/result.json".format(job_id)
        storage.Client().bucket(bucket).blob(object_name).upload_from_string(
            json.dumps(result), content_type="application/json")
        result["result_uri"] = "gs://{}/{}".format(bucket, object_name)
    print("AI_LAB_RESULT " + json.dumps(result), flush=True)

if __name__ == "__main__":
    main()
