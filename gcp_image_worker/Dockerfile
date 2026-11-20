FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/tmp/huggingface
WORKDIR /app
RUN pip install --no-cache-dir "diffusers>=0.32,<1" "transformers>=4.48,<5" \
    "accelerate>=1.3,<2" "safetensors>=0.5,<1" "google-cloud-storage>=2.18,<4"
COPY worker.py /app/worker.py
ENTRYPOINT ["python", "/app/worker.py"]
CMD ["{}"]
