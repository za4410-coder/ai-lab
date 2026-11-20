FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/tmp/huggingface
WORKDIR /app
RUN pip install --no-cache-dir "diffusers>=0.32,<1" "transformers>=4.48,<5" \
    "accelerate>=1.3,<2" "safetensors>=0.5,<1" 
COPY gcp_image_worker/worker.py /app/gcp_image_worker/worker.py
RUN touch /app/gcp_image_worker/__init__.py
COPY runpod_worker/worker.py /app/runpod_worker/worker.py
ENTRYPOINT ["python", "/app/runpod_worker/worker.py"]
