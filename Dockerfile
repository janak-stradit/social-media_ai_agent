# Production image - built and pushed to Amazon ECR by .github/workflows/deploy.yml
# and run on EC2 as two containers (web + scheduler, deploy/docker-compose.prod.yml).
# Pinned to Debian 12 (bookworm): the plain "3.11-slim" tag moved to Debian 13 (trixie),
# which Playwright 1.47's "install --with-deps" doesn't support (it falls back to the
# Ubuntu 20.04 package list, whose names don't exist on trixie, e.g. libasound2).
#
# Two stages to keep the image small: packages are installed into a virtualenv in
# a "builder" stage that has a compiler, and only that virtualenv is copied into the
# final image - no gcc, headers or pip caches ship to production.

# ---------- builder: install Python dependencies ----------
FROM python:3.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Only needed if a dependency has to be compiled from source; never reaches the final image
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# CPU-only torch: the default wheel bundles ~2.5 GB of CUDA libraries an EC2
# CPU instance can't use.
COPY requirements.txt .
RUN pip install "$(grep -E '^torch==' requirements.txt)" --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

# ---------- runtime ----------
FROM python:3.11-slim-bookworm

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    HF_HOME=/opt/hf-cache \
    WEB_CONCURRENCY=2

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Chromium + its system libraries for the onboarding scraper's headless-browser tier.
# No system ffmpeg: moviepy uses the ffmpeg binary bundled in the imageio-ffmpeg wheel.
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /tmp/*

# Bake the embedding model into the image so containers don't download it on every start
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Run as a non-root user. UID 1000 matches the default "ubuntu" user on EC2, which
# owns the mounted .env and data directories.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app

COPY --chown=app:app . .
RUN mkdir -p static/uploads chroma_db \
    && chown app:app /app \
    && chown -R app:app static/uploads chroma_db /opt/hf-cache

# Version on every log line (logging_setup.py); the deploy workflow passes the commit
ARG GIT_SHA=dev
ENV APP_VERSION=$GIT_SHA

USER app
EXPOSE 5000

# Web server (logging and other settings that belong with the code: gunicorn.conf.py).
# --timeout 1900: a generation (LLM + image/video) can run for minutes - a
# minimax-h3 video is given 1800 s (media_service._heyroute_video_timeout);
# gunicorn's default 30 s would kill it. Workers come from WEB_CONCURRENCY (default 2).
# The scheduler runs in its own container (command: python scripts/run_scheduler.py),
# so the web container must run with SCHEDULER_ENABLED=false (set in the compose file).
CMD ["gunicorn", "--worker-class", "gthread", "--threads", "4", \
     "--timeout", "1900", "--graceful-timeout", "60", \
     "--bind", "0.0.0.0:5000", "--config", "gunicorn.conf.py", \
     "app:create_app(\"production\")"]
