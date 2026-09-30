# Production image - built and pushed to Amazon ECR by .github/workflows/deploy.yml
# and run on EC2 as two containers (web + scheduler, deploy/docker-compose.prod.yml).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    HF_HOME=/opt/hf-cache \
    WEB_CONCURRENCY=2

WORKDIR /app

# gcc/libpq-dev: building psycopg2 and friends; ffmpeg: video post-processing
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so code-only changes reuse these (large) cached layers.
# CPU-only torch: the default wheel bundles ~2.5 GB of CUDA libraries an EC2
# CPU instance can't use.
COPY requirements.txt .
RUN pip install "$(grep -E '^torch==' requirements.txt)" --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

# Chromium + its system libraries for the onboarding scraper's headless-browser tier
RUN playwright install --with-deps chromium

# Bake the embedding model into the image so containers don't download it on every start
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Run as a non-root user. UID 1000 matches the default "ubuntu" user on EC2, which
# owns the mounted .env and data directories.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app

COPY --chown=app:app . .
RUN mkdir -p static/uploads chroma_db \
    && chown app:app /app \
    && chown -R app:app static/uploads chroma_db /opt/hf-cache

USER app
EXPOSE 5000

# Web server. --timeout 900: a generation (LLM + image/video) can run for minutes;
# gunicorn's default 30 s would kill it. Workers come from WEB_CONCURRENCY (default 2).
# The scheduler runs in its own container (command: python scripts/run_scheduler.py),
# so the web container must run with SCHEDULER_ENABLED=false (set in the compose file).
CMD ["gunicorn", "--worker-class", "gthread", "--threads", "4", \
     "--timeout", "900", "--graceful-timeout", "60", \
     "--bind", "0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-", \
     "app:create_app(\"production\")"]
