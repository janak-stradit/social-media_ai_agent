"""gunicorn settings that belong with the code (the Dockerfile CMD sets
workers/threads/timeouts). Logging: gunicorn's own messages use the app's
format (logging_setup.py); its access log is off because the app writes a
richer one per request (request id, user, duration)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logging_setup import logging_config

logconfig_dict = logging_config()
accesslog = None

# Threads per worker. A request mostly waits on the AI service (25-70 s for a
# post) rather than using the CPU, so many threads share one worker well:
# 2 workers x 16 = 32 requests at the same moment (was 2 x 4 = 8). Each
# thread may hold a database connection: db.py sizes the pool to match.
threads = int(os.getenv("GUNICORN_THREADS", "16"))
errorlog = "-"
