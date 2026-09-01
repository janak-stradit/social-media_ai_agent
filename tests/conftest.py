"""Shared pytest fixtures.

Forces the app onto a throwaway sqlite database for the whole test session,
so the suite never touches a real Postgres instance and can run in CI with
no external services. `db.py` reads DATABASE_URL once at import time, so this
must be set before anything imports `db` -- conftest.py is always imported
before test modules in the same directory, which is early enough.
"""

import os
import tempfile

os.environ.setdefault("SECRET_KEY", "test-secret-key")

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = "sqlite:///" + _db_path.replace("\\", "/")

import pytest  # noqa: E402 -- must follow the DATABASE_URL env setup above


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    import db

    db.init_db()
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    """Start every test with empty competitor/opportunity tables."""
    from sqlalchemy import delete

    import db

    with db.Session(db.engine) as session:
        session.execute(delete(db.CompetitorPost))
        session.execute(delete(db.OpportunitySuggestion))
        session.commit()
    yield
