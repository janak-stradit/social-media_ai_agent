"""Copy every file already in static/uploads/ to the S3 media bucket.

New content is copied automatically as it is generated; run this once after
setting S3_MEDIA_BUCKET to back up what was generated before, e.g. on EC2:

    cd /opt/socialmedia/app && /opt/socialmedia/venv/bin/python scripts/sync_uploads_to_s3.py
"""

import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from config import Config  # noqa: E402
from services import storage_service  # noqa: E402

if __name__ == "__main__":
    if not storage_service.enabled():
        sys.exit("S3_MEDIA_BUCKET is not set - nothing to do.")
    uploaded, present = storage_service.sync_local_to_s3()
    print(
        f"s3://{Config.S3_MEDIA_BUCKET}/{Config.S3_MEDIA_PREFIX}: "
        f"uploaded {uploaded} file(s), {present} already there."
    )
