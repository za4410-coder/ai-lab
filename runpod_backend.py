"""RunPod v2 pod lifecycle for one-shot SDXL image jobs.

Requires RUNPOD_API_KEY and RUNPOD_IMAGE (public AMD64 CUDA image).
No GPU pod is created until the local dispatcher runs an explicitly queued job.
"""
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.runpod.io/v2/pods"
PNG = b"\x89PNG\r\n\x1a\n"

def api_request(method, url, data=None, token=None, timeout=35, binary=False):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=headers,
            data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(16000000 if binary else 1000000)
            if binary:
                return raw
            return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as exc:
        msg = exc.read(1600).decode(errors="replace")
        raise RuntimeError("RunPod/worker HTTP {}: {}".format(exc.code, msg)) from exc

def validate_image(payload):
    if payload.get("task") != "image":
        raise ValueError("RunPod currently supports SDXL image tasks only.")
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise ValueError("An image prompt (1-2000 characters) is required.")
    for key, low, high in (("width", 512, 1024), ("height", 512, 1024),
                           ("steps", 1, 50), ("seed", 0, 2147483647)):
        value = payload.get(key, {"width": 1024, "height": 1024, "steps": 25, "seed": 42}[key])
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid {}".format(key))
    w, h = payload.get("width", 1024), payload.get("height", 1024)
    if w % 64 or h % 64 or w * h > 1024 * 1024:
        raise ValueError("Dimensions must be multiples of 64, max 1 megapixel.")

def available_gpus(cloud):
    """Account-specific Pod availability, price and VRAM for exactly one cloud tier."""
    if cloud not in ("SECURE", "COMMUNITY"):
        raise ValueError("Invalid RunPod cloud tier")
    key = os.getenv("RUNPOD_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Set RUNPOD_API_KEY before listing RunPod GPUs.")
    url = "https://api.runpod.io/v2/catalog/gpus?include=AVAILABILITY&product=POD&count=1&cloud=" + cloud
    listing = api_request("GET", url, token=key)
    items = []
    for gpu in listing.get("gpus", []):
        if not isinstance(gpu, dict) or not gpu.get("id"):
            continue
        price = gpu.get("price") or {}
        status = gpu.get("availability", "UNKNOWN")
        cloud_key = cloud.lower()
        items.append({
            "id": gpu["id"], "name": gpu.get("name", gpu["id"]),
            "memory": gpu.get("memory"),
            "price": price.get(cloud_key),
            "availability": status,
            "cloud": cloud,
            "available": bool(gpu.get(cloud_key, False)) and status not in ("NONE", "UNAVAILABLE", "UNKNOWN"),
        })
    items.sort(key=lambda gpu: (not gpu["available"], -(gpu["memory"] or 0), gpu["price"] if isinstance(gpu["price"], (int,float)) else 99999))
    return items

def credentials():
    key = os.getenv("RUNPOD_API_KEY", "").strip()
    image = os.getenv("RUNPOD_IMAGE", "").strip()
    if not key or not image:
        raise RuntimeError("Set RUNPOD_API_KEY and RUNPOD_IMAGE before queuing RunPod jobs.")
    if not re.fullmatch(r"[A-Za-z0-9._:/@-]+", image):
        raise RuntimeError("RUNPOD_IMAGE must be a Docker registry image reference.")
    return key, image

def record_pod(job_id, pod_id):
    # Persist immediately so the pod can be found for cleanup after a controller crash.
    import app
    with app.connect() as con:
        row = con.execute("SELECT result FROM jobs WHERE id=?", (job_id,)).fetchone()
        prior = json.loads(row["result"]) if row and row["result"] else {}
        con.execute("UPDATE jobs SET result=?,updated_at=? WHERE id=?",
            (json.dumps({**prior, "runpod_id": pod_id, "terminated": False}), app.now(), job_id))
        app.event(con, job_id, "RunPod pod provisioned: " + pod_id)

def mark_terminated(job_id):
    import app
    with app.connect() as con:
        row = con.execute("SELECT result FROM jobs WHERE id=?", (job_id,)).fetchone()
        prior = json.loads(row["result"]) if row and row["result"] else {}
        con.execute("UPDATE jobs SET result=?,updated_at=? WHERE id=?",
            (json.dumps({**prior, "terminated": True}), app.now(), job_id))
        app.event(con, job_id, "RunPod pod terminated")

def terminate(pod_id, key):
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", pod_id):
        raise ValueError("Invalid RunPod pod ID")
    return api_request("DELETE", API + "/" + pod_id, token=key)

def run(job):
    validate_image(job["payload"])
    key, image = credentials()
    cloud = "COMMUNITY" if job["provider"] == "runpod-community" else "SECURE"
    gpu_id = job["payload"].get("gpu_id") or os.getenv("RUNPOD_GPU_ID", "NVIDIA GeForce RTX 4090")
    if not isinstance(gpu_id, str) or len(gpu_id) > 120:
        raise ValueError("Invalid RunPod GPU selection.")
    token = secrets.token_urlsafe(32)
    job_json = json.dumps({"job_id": job["id"], "payload": job["payload"]}, separators=(",", ":"))
    request = {
        "name": "ai-lab-" + job["id"],
        "image": image,
        "cloud": cloud,
        "gpu": {"id": gpu_id, "count": 1},
        "disk": 40,
        "ports": ["8000/http"],
        "env": {"AI_LAB_JOB_JSON": job_json, "AI_LAB_JOB_TOKEN": token}
    }
    try:
        created = api_request("POST", API, data=request, token=key)
    except RuntimeError as exc:
        if "no longer any instances available" in str(exc).lower():
            raise RuntimeError("Selected {} GPU ({}) has no available Pod capacity right now. "
                "Open the job editor, refresh RunPod GPUs, and select another GPU with available stock. "
                "No Pod was created.".format(cloud.title(), gpu_id)) from exc
        raise
    pod_id = created.get("id")
    if not pod_id or not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", pod_id):
        raise RuntimeError("Pod creation returned no valid ID; check RunPod console before retry.")
    started = time.monotonic()
    record_pod(job["id"], pod_id)
    base = "https://{}-8000.proxy.runpod.net".format(pod_id)
    duration_limit = int(os.getenv("RUNPOD_MAX_SECONDS", "1800"))
    duration_limit = max(120, min(duration_limit, 3600))
    result = None
    failed = None
    try:
        while time.monotonic() - started < duration_limit:
            pod = api_request("GET", API + "/" + pod_id, token=key)
            state = pod.get("desiredStatus", "")
            if state in ("EXITED", "TERMINATED"):
                raise RuntimeError("Pod exited before image was downloaded.")
            if pod.get("runtime", {}).get("error"):
                raise RuntimeError("RunPod runtime error: " + str(pod["runtime"]["error"])[:300])
            try:
                status = api_request("GET", base + "/status", token=token, timeout=12)
            except (RuntimeError, urllib.error.URLError, TimeoutError, OSError):
                time.sleep(5)
                continue
            if status.get("job_id") != job["id"]:
                raise RuntimeError("RunPod worker job ID mismatch.")
            if status.get("state") == "failed":
                raise RuntimeError("RunPod image generation failed: " + str(status.get("error", ""))[:1000])
            if status.get("state") == "completed":
                image_bytes = api_request("GET", base + "/image", token=token, timeout=90, binary=True)
                if not image_bytes.startswith(PNG) or len(image_bytes) > 15000000:
                    raise RuntimeError("Worker returned an invalid or oversized PNG.")
                folder = Path(os.getenv("AI_LAB_DATA_DIR", str(Path(__file__).resolve().parent / "data"))) / "artifacts"
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / (job["id"] + ".png")
                temp = folder / (job["id"] + ".part")
                temp.write_bytes(image_bytes)
                os.chmod(temp, 0o600)
                temp.replace(target)
                result = {
                    "runpod_id": pod_id, "cloud": cloud,
                    "hourly_rate_reported": created.get("adjustedCostPerHr", created.get("costPerHr")),
                    "duration_seconds": round(time.monotonic() - started, 2),
                    "artifact": {"job_id": job["id"], "task": "image",
                                 "output": {"image_uri": "local://artifacts/" + job["id"] + ".png",
                                            "model": "stabilityai/stable-diffusion-xl-base-1.0"},
                                 "duration_seconds": status.get("duration_seconds")}
                }
                break
            time.sleep(5)
        else:
            raise RuntimeError("RunPod job timed out after {} seconds.".format(duration_limit))
    except Exception as exc:
        failed = exc
    finally:
        try:
            terminate(pod_id, key)
            mark_terminated(job["id"])
        except Exception as cleanup_exc:
            # Persist the pod ID in SQLite; never claim the GPU has stopped.
            if failed is None:
                failed = RuntimeError("Result downloaded, but pod termination FAILED. "
                                     "Terminate pod {} in RunPod console immediately: {}".format(pod_id, cleanup_exc))
            else:
                failed = RuntimeError("{}; ALSO pod termination failed for {}: {}".format(
                    failed, pod_id, cleanup_exc))
    if failed:
        raise failed
    result["terminated"] = True
    return result

def cleanup_orphans():
    """On startup, terminate pods recorded as non-terminated by AI Lab."""
    import app
    key = os.getenv("RUNPOD_API_KEY", "").strip()
    if not key:
        return
    with app.connect() as con:
        rows = con.execute("SELECT id,result FROM jobs WHERE provider IN ('runpod-community','runpod-secure') AND result IS NOT NULL").fetchall()
    for row in rows:
        try:
            info = json.loads(row["result"])
            if info.get("runpod_id") and not info.get("terminated"):
                terminate(info["runpod_id"], key)
                mark_terminated(row["id"])
        except Exception as exc:
            print("WARNING: RunPod pod may still incur charges for job {}: {}".format(row["id"], exc), flush=True)
