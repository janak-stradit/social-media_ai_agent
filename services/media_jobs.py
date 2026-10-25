"""Images and videos made in the background, so a web request doesn't stay
open for the whole render (30-60 s for an image, 10-15 minutes for a video).

    start(user_id, media_type, platform, work, app) -> job id
        `work` is the media route's own function (api/routes.py
        generate_media); it runs on this small pool, outside gunicorn's
        request threads, inside an app context.
    get(user_id, job_id) -> {id, status: running|done|failed, result, error}

Job state lives in the database (db.MediaJob), so any web worker can answer
the page's "is it done?" check. A job still "running" long after it should
have finished (the server restarted mid-render) is reported as failed.
Results are also saved on the post itself (_persist_generated_media), so a
page opened later shows the image or video either way.
"""

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

WORKERS = 3  # renders at the same time per web worker (they mostly wait on the AI service)
MAX_RUNNING_PER_USER = 4
STALE_AFTER = {"video": timedelta(minutes=40), "image": timedelta(minutes=10)}

_pool = None
_pool_lock = threading.Lock()


class TooManyJobs(RuntimeError):
    pass


def _executor() -> ThreadPoolExecutor:
    global _pool  # pylint: disable=global-statement
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="media-job")
        return _pool


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def start(user_id: int, media_type: str, platform: str, work, app) -> str:
    from sqlalchemy.orm import Session

    from db import MediaJob, engine

    with Session(engine) as session:
        running = (
            session.query(MediaJob)
            .filter(
                MediaJob.user_id == user_id,
                MediaJob.status == "running",
                MediaJob.created_at > _now() - STALE_AFTER["video"],
            )
            .count()
        )
        if running >= MAX_RUNNING_PER_USER:
            raise TooManyJobs(f"You already have {running} images or videos being made. Wait for one to finish.")
        job = MediaJob(
            id=uuid.uuid4().hex,
            user_id=user_id,
            media_type=media_type,
            platform=platform,
            status="running",
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(job)
        session.commit()
        job_id = job.id
    _executor().submit(_run, job_id, work, app)
    return job_id


def _run(job_id: str, work, app) -> None:
    status, result, error = "failed", None, None
    try:
        with app.app_context():
            result = work()
        if isinstance(result, dict) and result.get("success"):
            status = "done"
        else:
            error = (result or {}).get("error") if isinstance(result, dict) else None
            error = error or "The image or video could not be made."
    except Exception as err:  # noqa: BLE001 - reported to the page, details logged
        logger.exception(f"Media job {job_id} failed: {err}")
        error = "The image or video could not be made. Please try again."
    _finish(job_id, status, result, error)


def _finish(job_id: str, status: str, result, error) -> None:
    from sqlalchemy.orm import Session

    from db import MediaJob, engine

    try:
        with Session(engine) as session:
            job = session.get(MediaJob, job_id)
            if job:
                job.status = status
                job.result_json = json.dumps(result, default=str) if result is not None else None
                job.error = (error or "")[:500] or None
                job.updated_at = _now()
                session.commit()
    except Exception as err:  # noqa: BLE001
        logger.error(f"Could not save media job {job_id}: {err}")


def get(user_id: int, job_id: str) -> dict | None:
    from sqlalchemy.orm import Session

    from db import MediaJob, engine

    with Session(engine) as session:
        job = session.get(MediaJob, job_id)
        if not job or job.user_id != user_id:
            return None
        if job.status == "running" and job.created_at < _now() - STALE_AFTER.get(job.media_type, STALE_AFTER["video"]):
            job.status, job.error, job.updated_at = "failed", "This was interrupted. Please create it again.", _now()
            session.commit()
        result = json.loads(job.result_json) if job.result_json else None
        return {
            "id": job.id,
            "status": job.status,
            "media_type": job.media_type,
            "platform": job.platform,
            "result": result,
            "error": job.error,
        }
