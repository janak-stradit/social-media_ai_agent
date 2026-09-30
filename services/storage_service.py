"""S3 storage for generated content and user uploads.

The app keeps working with local files under static/uploads/ (logo stamping,
video post-processing, email attachments and publishing all read them), and
every URL stays /static/uploads/<file>. S3 is the durable copy:

  * each generated or uploaded file is copied to s3://<bucket>/<prefix><file>
    in the background, so generation isn't slowed down;
  * when a file is missing on disk (new or rebuilt server, cleaned disk),
    ensure_local() pulls it back from S3 before it's served or read.

Disabled (every function is a no-op) unless S3_MEDIA_BUCKET is set.
"""

import functools
import mimetypes
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from config import Config

UPLOAD_URL_PREFIX = "/static/uploads/"
# Result keys that can point at a file under static/uploads/
_URL_KEYS = ("url", "clean_url", "original_url", "source_image_url")

_client = None
_client_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="s3-upload")
_uploaded: set[str] = set()


def enabled() -> bool:
    return bool(Config.S3_MEDIA_BUCKET)


def _get_client():
    """boto3 S3 client. Credentials come from AWS_ACCESS_KEY_ID /
    AWS_SECRET_ACCESS_KEY / AWS_PROFILE when set, otherwise from boto3's
    default chain (on EC2: the instance's IAM role)."""
    global _client  # pylint: disable=global-statement
    if _client is None:
        with _client_lock:
            if _client is None:
                import boto3

                session_kwargs = {"region_name": Config.AWS_REGION}
                if Config.AWS_ACCESS_KEY_ID and Config.AWS_SECRET_ACCESS_KEY:
                    session_kwargs["aws_access_key_id"] = Config.AWS_ACCESS_KEY_ID
                    session_kwargs["aws_secret_access_key"] = Config.AWS_SECRET_ACCESS_KEY
                elif Config.AWS_PROFILE:
                    session_kwargs["profile_name"] = Config.AWS_PROFILE
                _client = boto3.Session(**session_kwargs).client("s3")
    return _client


def _upload_root() -> str:
    return os.path.abspath(Config.UPLOAD_FOLDER)


def _relative_name(url_or_path: str | None) -> str | None:
    """static/uploads-relative name ("x.png", "brand_logos/y.png") for a
    /static/uploads/ URL or a path inside the upload folder; None for
    anything else (external URLs, files outside the upload folder)."""
    if not url_or_path or not isinstance(url_or_path, str):
        return None
    value = url_or_path.split("?", 1)[0].replace("\\", "/")
    if value.startswith(UPLOAD_URL_PREFIX):
        rel = value[len(UPLOAD_URL_PREFIX) :]
    else:
        root = _upload_root()
        path = os.path.abspath(url_or_path)
        if os.path.commonpath([root, path]) != root:
            return None
        rel = os.path.relpath(path, root).replace("\\", "/")
    rel = rel.lstrip("/")
    # Never let a crafted name escape the upload folder / key prefix
    if not rel or rel.startswith("../") or "/../" in f"/{rel}/":
        return None
    return rel


def _key(rel: str) -> str:
    return f"{Config.S3_MEDIA_PREFIX}{rel}"


def _local_path(rel: str) -> str:
    return os.path.join(_upload_root(), *rel.split("/"))


def _upload_now(rel: str) -> bool:
    path = _local_path(rel)
    if not os.path.isfile(path):
        return False
    extra = {"ContentType": mimetypes.guess_type(path)[0] or "application/octet-stream"}
    try:
        _get_client().upload_file(path, Config.S3_MEDIA_BUCKET, _key(rel), ExtraArgs=extra)
        _uploaded.add(rel)
        return True
    except Exception as e:
        _uploaded.discard(rel)
        print(f"[Storage] S3 upload failed for {rel}: {e}")
        return False


def upload(url_or_path: str | None, wait: bool = False) -> None:
    """Copies one local upload/generated file to S3 (in the background unless
    wait=True). Each file is uploaded once per process."""
    if not enabled():
        return
    rel = _relative_name(url_or_path)
    if not rel or rel in _uploaded:
        return
    _uploaded.add(rel)
    if wait:
        _upload_now(rel)
    else:
        _executor.submit(_upload_now, rel)


def upload_result(result) -> None:
    """Uploads every /static/uploads/ file referenced by a media result dict
    (or a list of them, e.g. carousel slides)."""
    if not enabled():
        return
    items = result if isinstance(result, list) else [result]
    for item in items:
        if isinstance(item, dict) and item.get("success", True):
            for key in _URL_KEYS:
                upload(item.get(key))


def mirror_to_s3(fn):
    """Decorator for MediaGenerationService's public methods: uploads the
    files named in the returned result. Never affects the result itself."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        try:
            upload_result(result)
        except Exception as e:
            print(f"[Storage] Could not queue S3 upload: {e}")
        return result

    return wrapper


def ensure_local(url_or_path: str | None) -> str | None:
    """Local path of an upload/generated file, downloading it from S3 first
    when it isn't on disk. None when it exists in neither place."""
    rel = _relative_name(url_or_path)
    if not rel:
        return None
    path = _local_path(rel)
    if os.path.isfile(path):
        return path
    if not enabled():
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{threading.get_ident()}.part"
    try:
        _get_client().download_file(Config.S3_MEDIA_BUCKET, _key(rel), tmp)
        os.replace(tmp, path)
        _uploaded.add(rel)
        return path
    except Exception as e:
        # A missing object is expected for files that were never uploaded
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if code not in ("404", "NoSuchKey"):
            print(f"[Storage] S3 download failed for {rel}: {e}")
        return None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def sync_local_to_s3() -> tuple[int, int]:
    """Uploads every file under static/uploads/ that isn't in S3 yet (used by
    scripts/sync_uploads_to_s3.py to back-fill existing content).
    Returns (uploaded, already_present)."""
    if not enabled():
        raise RuntimeError("S3_MEDIA_BUCKET is not set")
    client = _get_client()
    existing = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=Config.S3_MEDIA_BUCKET, Prefix=Config.S3_MEDIA_PREFIX):
        for obj in page.get("Contents", []):
            existing.add(obj["Key"])
    uploaded = present = 0
    root = _upload_root()
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.startswith(".") or name.endswith(".part"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/")
            if _key(rel) in existing:
                present += 1
            elif _upload_now(rel):
                uploaded += 1
    return uploaded, present
