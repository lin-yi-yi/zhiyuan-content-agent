"""Single-process ownership for local file-backed SQLite application lifetimes.

This advisory lock protects startup recovery as well as normal serving. It is
not a distributed lock, database transaction lock or a multi-worker architecture.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
from urllib.parse import unquote, urlsplit

from sqlalchemy.engine import make_url


class DatabaseRuntimeLockError(RuntimeError):
    """Sanitized startup error: no database URL, path or credentials."""


def _sqlite_file(database_url):
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    if str(url.query.get("uri", "")).lower() in {"true", "1", "yes"}:
        if url.query.get("mode") == "memory":
            return None
        if database.startswith("file:"):
            parsed = urlsplit(database)
            if parsed.netloc not in {"", "localhost"}:
                raise ValueError("Unsupported SQLite URI authority")
            database = unquote(parsed.path)
            if database == ":memory:":
                return None
    return Path(database).resolve()


@contextmanager
def local_database_runtime_lock(database_url, *, enabled=True):
    """Hold a database-scoped, nonblocking lock until the app has shut down.

    SQLite memory stores and non-SQLite databases do not use this file lock.
    The lock file is deliberately retained after closing: deleting it can let
    another process lock a different inode while the original lock is held.
    """
    if not enabled:
        yield
        return
    descriptor = None
    try:
        try:
            database = _sqlite_file(database_url)
            if database is not None:
                lock_path = database.with_name(database.name + ".runtime.lock")
                flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                descriptor = os.open(lock_path, flags, 0o600)
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise OSError("Invalid lock file")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise DatabaseRuntimeLockError("该本地数据库已由其他服务实例使用，请停止已有实例后再启动。") from None
        except DatabaseRuntimeLockError:
            raise
        except Exception:
            raise DatabaseRuntimeLockError("无法取得本地数据库运行锁，请检查数据目录和权限后重试。") from None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
