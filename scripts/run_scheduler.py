"""Run the scheduled-post publisher as its own process.

Production runs the web app under gunicorn with several workers; if each worker
started the scheduler thread, every due post would be published once per worker.
So the web service sets SCHEDULER_ENABLED=false and this script (one systemd
service, deploy/systemd/socialmedia-scheduler.service) is the only scheduler.
"""

import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from logging_setup import configure_logging

configure_logging()  # same log format as the web app

# Load .env before db.py reads DATABASE_URL at import time.
import config  # noqa: E402,F401  pylint: disable=unused-import,wrong-import-position
from db import init_db  # noqa: E402  pylint: disable=wrong-import-position
from scheduler_thread import (
    run_scheduler,
)

if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    init_db()
    run_scheduler(APP_ROOT)
