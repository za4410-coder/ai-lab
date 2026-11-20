"""Authenticated one-shot SDXL RunPod HTTP worker. Poll /status, fetch /image."""
import io
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ["AI_LAB_JOB_TOKEN"]
JOB = json.loads(os.environ["AI_LAB_JOB_JSON"])
STATE = {"job_id": JOB["job_id"], "state": "starting"}
IMAGE = None
LOCK = threading.Lock()

def generate():
    global IMAGE
    begin = time.monotonic()
    try:
        from gcp_image_worker.worker import generate as sdxl_generate
        with LOCK:
            STATE["state"] = "generating"
        image_bytes, _ = sdxl_generate(JOB["payload"])
        with LOCK:
            IMAGE = image_bytes
            STATE.update(state="completed", duration_seconds=round(time.monotonic()-begin, 2))
    except Exception as exc:
        with LOCK:
            STATE.update(state="failed", error=str(exc)[:1000])
        print("Worker error:", repr(exc), flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        import hmac
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + TOKEN):
            return self.respond(401, b"Unauthorized", "text/plain")
        with LOCK:
            if self.path == "/status":
                return self.respond(200, json.dumps(STATE).encode(), "application/json")
            if self.path == "/image" and STATE["state"] == "completed" and IMAGE:
                return self.respond(200, IMAGE, "image/png")
        return self.respond(404, b"Not available", "text/plain")

    def respond(self, status, data, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

if __name__ == "__main__":
    if JOB["payload"].get("task") != "image":
        raise ValueError("Unsupported task")
    threading.Thread(target=generate, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
