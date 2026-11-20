#!/usr/bin/env python3
"""Open-weight SDXL image inference on a rented NVIDIA GPU. No hosted AI model API."""
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"

def generate(payload):
    import torch
    from diffusers import StableDiffusionXLPipeline
    if not torch.cuda.is_available():
        raise RuntimeError("No CUDA GPU detected. Deploy the image worker with --gpu=1.")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise ValueError("prompt must be a nonempty string of at most 2000 characters.")
    width = payload.get("width", 1024)
    height = payload.get("height", 1024)
    steps = payload.get("steps", 25)
    seed = payload.get("seed", 42)
    for name, value, minimum, maximum in (
        ("width", width, 512, 1024), ("height", height, 512, 1024),
        ("steps", steps, 1, 50), ("seed", seed, 0, 2147483647)
    ):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("{} out of range".format(name))
    if width % 64 or height % 64 or width * height > 1024 * 1024:
        raise ValueError("Image dimensions must be multiples of 64 and at most 1 megapixel.")

    print(json.dumps({"event": "loading_model", "model": MODEL_ID}), flush=True)
    pipe = StableDiffusionXLPipeline.from_pretrained(
        MODEL_ID, torch_dtype=torch.float16, use_safetensors=True, variant="fp16")
    pipe = pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    print(json.dumps({"event": "generating", "width": width, "height": height}), flush=True)
    with torch.inference_mode():
        image = pipe(prompt=prompt, width=width, height=height,
                     num_inference_steps=steps, generator=torch.Generator("cuda").manual_seed(seed)).images[0]
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), {"model": MODEL_ID, "prompt": prompt, "width": width,
                               "height": height, "steps": steps, "seed": seed}

def main():
    if len(sys.argv) != 2:
        raise SystemExit("Expected one JSON argument.")
    request = json.loads(sys.argv[1])
    job_id = request.get("job_id")
    payload = request.get("payload", {})
    if not isinstance(job_id, str) or not job_id.isalnum() or len(job_id) > 64:
        raise ValueError("Invalid job id.")
    if not isinstance(payload, dict) or payload.get("task") != "image":
        raise ValueError('This worker only supports {"task":"image"}.')
    bucket_name = os.getenv("AI_LAB_RESULTS_BUCKET", "")
    if not bucket_name:
        raise RuntimeError("AI_LAB_RESULTS_BUCKET must be configured.")
    started = time.monotonic()
    image_bytes, meta = generate(payload)
    from google.cloud import storage
    bucket = storage.Client().bucket(bucket_name)
    image_path = "ai-lab/jobs/{}/image.png".format(job_id)
    bucket.blob(image_path).upload_from_string(image_bytes, content_type="image/png")
    result = {"job_id": job_id, "task": "image",
              "output": {"image_uri": "gs://{}/{}".format(bucket_name, image_path), **meta},
              "duration_seconds": round(time.monotonic()-started, 2),
              "finished_at": datetime.now(timezone.utc).isoformat()}
    result_path = "ai-lab/jobs/{}/result.json".format(job_id)
    bucket.blob(result_path).upload_from_string(json.dumps(result), content_type="application/json")
    print("AI_LAB_RESULT " + json.dumps(result), flush=True)

if __name__ == "__main__":
    main()
