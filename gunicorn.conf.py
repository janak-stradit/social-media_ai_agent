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
errorlog = "-"
