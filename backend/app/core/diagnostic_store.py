"""Private, bounded JSONL diagnostics for one cooperating process on POSIX.

No raw logging records, request bodies or exception messages are accepted. A
complete write plus fsync is a best effort receipt, not an exactly-once or
power-loss durability guarantee. Once filesystem state becomes uncertain, this
owner stops writing and retains the advisory lock until close().
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock

from app.core.diagnostic_schema import MAX_RECORD_BYTES, validate_record


MARKER_NAME = "diagnostics-format.json"
MARKER_FORMAT = "zhiyuan-diagnostics-jsonl"
LOG_NAME = "diagnostics.jsonl"
LOCK_NAME = ".diagnostics.lock"
MIN_FILE_BYTES = 8192
MAX_FILE_BYTES = 16777216
MAX_BACKUPS = 20
_MARKER = {"format": MARKER_FORMAT, "schema_version": 1}
_OPEN_FLAGS = os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


class StoreError(RuntimeError):
    """No filesystem path or original exception text is exposed."""

    def __init__(self):
        super().__init__("Diagnostic store initialization failed.")


class _Changed(RuntimeError):
    pass


def _identity(info):
    return info.st_dev, info.st_ino


def _private_file(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)


def _private_directory(info):
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) & 0o077 == 0)


def _write_all(descriptor, data):
    offset = 0
    while offset < len(data):
        try:
            count = os.write(descriptor, data[offset:])
        except InterruptedError:
            continue
        if type(count) is not int or count <= 0 or count > len(data) - offset:
            raise OSError("Incomplete diagnostic write")
        offset += count


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Changed()
        result[key] = value
    return result


class DiagnosticStore:
    def __init__(self, directory, *, max_bytes=1048576, backup_count=5):
        self._mutex = RLock()
        self._dir_fd = self._lock_fd = self._active_fd = None
        self._state, self._error_code = "healthy", None
        self._written = self._dropped = 0
        self._expected = {}
        try:
            if (type(max_bytes) is not int or not MIN_FILE_BYTES <= max_bytes <= MAX_FILE_BYTES
                    or type(backup_count) is not int or not 1 <= backup_count <= MAX_BACKUPS):
                raise _Changed()
            self._directory = Path(directory)
            if not self._directory.is_absolute():
                raise _Changed()
            self.max_bytes, self.backup_count = max_bytes, backup_count
            self._dir_fd = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY | _OPEN_FLAGS)
            self._directory_identity = _identity(os.fstat(self._dir_fd))
            self._check_directory()
            initial = self._scan()
            was_empty = not initial
            if not was_empty and MARKER_NAME not in initial:
                raise _Changed()
            self._lock_fd = os.open(LOCK_NAME, os.O_RDWR | os.O_CREAT | _OPEN_FLAGS, 0o600, dir_fd=self._dir_fd)
            self._check_open_file(LOCK_NAME, self._lock_fd)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Recheck after taking ownership; initialization never adopts extra
            # files which appeared during a competing open or directory change.
            current = self._scan()
            if set(current) != set(initial) | {LOCK_NAME}:
                raise _Changed()
            for name in initial:
                if _identity(current[name]) != _identity(initial[name]):
                    raise _Changed()
            if was_empty:
                descriptor = os.open(MARKER_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _OPEN_FLAGS,
                                     0o600, dir_fd=self._dir_fd)
                try:
                    self._check_open_file(MARKER_NAME, descriptor)
                    _write_all(descriptor, json.dumps(_MARKER, separators=(",", ":")).encode() + b"\n")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            self._check_marker()
            if LOG_NAME not in current:
                self._active_fd = os.open(LOG_NAME, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | _OPEN_FLAGS,
                                          0o600, dir_fd=self._dir_fd)
                os.fsync(self._active_fd)
            else:
                self._active_fd = os.open(LOG_NAME, os.O_RDWR | os.O_APPEND | _OPEN_FLAGS, dir_fd=self._dir_fd)
            self._check_open_file(LOG_NAME, self._active_fd)
            size = os.fstat(self._active_fd).st_size
            if size and os.pread(self._active_fd, 1, size - 1) != b"\n":
                # A prior partial append is preserved for operator inspection;
                # never merge the next event into an uncertain trailing record.
                raise _Changed()
            os.fsync(self._dir_fd)
            self._remember()
        except Exception:
            self._close_descriptors()
            raise StoreError() from None

    def _check_directory(self):
        opened = os.fstat(self._dir_fd)
        current = os.stat(self._directory, follow_symlinks=False)
        if (not _private_directory(opened) or not _private_directory(current)
                or _identity(opened) != self._directory_identity or _identity(current) != self._directory_identity):
            raise _Changed()

    def _scan(self):
        self._check_directory()
        entries = {}
        with os.scandir(self._dir_fd) as inventory:
            for entry in inventory:
                name = entry.name
                if name not in {LOCK_NAME, MARKER_NAME, LOG_NAME}:
                    match = re.fullmatch(re.escape(LOG_NAME) + r"\.([1-9][0-9]?)", name)
                    if match is None or not 1 <= int(match[1]) <= self.backup_count:
                        raise _Changed()
                info = os.stat(name, dir_fd=self._dir_fd, follow_symlinks=False)
                if not _private_file(info):
                    raise _Changed()
                if name == MARKER_NAME and info.st_size > 256:
                    raise _Changed()
                if name == LOCK_NAME and info.st_size != 0:
                    raise _Changed()
                if name not in {LOCK_NAME, MARKER_NAME} and info.st_size > self.max_bytes:
                    raise _Changed()
                entries[name] = info
        return entries

    def _check_open_file(self, name, descriptor):
        opened = os.fstat(descriptor)
        current = os.stat(name, dir_fd=self._dir_fd, follow_symlinks=False)
        if not _private_file(opened) or not _private_file(current) or _identity(opened) != _identity(current):
            raise _Changed()

    def _check_marker(self):
        descriptor = os.open(MARKER_NAME, os.O_RDONLY | _OPEN_FLAGS, dir_fd=self._dir_fd)
        try:
            self._check_open_file(MARKER_NAME, descriptor)
            data = os.read(descriptor, 257)
            if len(data) > 256:
                raise _Changed()
            marker = json.loads(data, object_pairs_hook=_unique_object)
            if (type(marker) is not dict or set(marker) != set(_MARKER)
                    or marker.get("format") != MARKER_FORMAT
                    or type(marker.get("schema_version")) is not int or marker["schema_version"] != 1):
                raise _Changed()
        finally:
            os.close(descriptor)

    def _snapshot(self):
        return {name: (_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                for name, info in self._scan().items()}

    def _remember(self):
        self._expected = self._snapshot()

    def _check_unchanged(self):
        actual = self._snapshot()
        if actual != self._expected:
            raise _Changed()
        self._check_open_file(LOCK_NAME, self._lock_fd)
        self._check_open_file(LOG_NAME, self._active_fd)

    def _rotate(self):
        self._check_unchanged()
        previous_files = self._expected
        rotated = {MARKER_NAME: previous_files[MARKER_NAME], LOCK_NAME: previous_files[LOCK_NAME],
                   f"{LOG_NAME}.1": previous_files[LOG_NAME]}
        oldest = f"{LOG_NAME}.{self.backup_count}"
        if oldest in self._expected:
            os.unlink(oldest, dir_fd=self._dir_fd)
        for index in range(self.backup_count - 1, 0, -1):
            source = f"{LOG_NAME}.{index}"
            if source in self._expected:
                os.rename(source, f"{LOG_NAME}.{index + 1}", src_dir_fd=self._dir_fd, dst_dir_fd=self._dir_fd)
                rotated[f"{LOG_NAME}.{index + 1}"] = previous_files[source]
        os.rename(LOG_NAME, f"{LOG_NAME}.1", src_dir_fd=self._dir_fd, dst_dir_fd=self._dir_fd)
        previous, self._active_fd = self._active_fd, None
        os.close(previous)
        self._active_fd = os.open(LOG_NAME, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | _OPEN_FLAGS,
                                  0o600, dir_fd=self._dir_fd)
        self._check_open_file(LOG_NAME, self._active_fd)
        os.fsync(self._active_fd)
        os.fsync(self._dir_fd)
        actual = self._snapshot()
        if set(actual) != set(rotated) | {LOG_NAME}:
            raise _Changed()
        for name, old in rotated.items():
            # Renames may update ctime, but cannot change inode, bytes or mtime.
            unchanged = actual[name] == old if name in {MARKER_NAME, LOCK_NAME} else actual[name][:3] == old[:3]
            if not unchanged:
                raise _Changed()
        self._check_open_file(LOG_NAME, self._active_fd)
        if actual[LOG_NAME][1] != 0:
            raise _Changed()
        self._expected = actual

    def append(self, payload: dict) -> bool:
        with self._mutex:
            if self._state != "healthy":
                self._dropped += 1
                return False
            try:
                if (type(payload) is not dict or ("schema_version" in payload
                        and (type(payload["schema_version"]) is not int or payload["schema_version"] != 1))):
                    raise ValueError()
                record = validate_record({**payload, "schema_version": 1})
                if record is None:
                    raise ValueError()
                line = json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode() + b"\n"
                if len(line) > MAX_RECORD_BYTES:
                    raise ValueError()
            except Exception:
                self._dropped += 1
                self._error_code = "INVALID_RECORD"
                return False
            try:
                self._check_unchanged()
                if os.fstat(self._active_fd).st_size + len(line) > self.max_bytes:
                    self._rotate()
                self._check_unchanged()
                _write_all(self._active_fd, line)
                os.fsync(self._active_fd)
                self._check_directory()
                self._check_open_file(LOG_NAME, self._active_fd)
                self._check_open_file(LOCK_NAME, self._lock_fd)
                actual = self._snapshot()
                if (set(actual) != set(self._expected)
                        or any(value != self._expected[name] for name, value in actual.items() if name != LOG_NAME)
                        or actual[LOG_NAME][0] != self._expected[LOG_NAME][0]
                        or actual[LOG_NAME][1] != self._expected[LOG_NAME][1] + len(line)):
                    raise _Changed()
                self._expected = actual
            except Exception as exc:
                self._state = "degraded"
                self._error_code = "STORE_CHANGED" if isinstance(exc, _Changed) else "WRITE_FAILED"
                self._dropped += 1
                return False
            self._written += 1
            self._error_code = None
            return True

    def status(self) -> dict:
        with self._mutex:
            return {"state": self._state, "written": self._written, "dropped": self._dropped,
                    "error_code": self._error_code}

    def _close_descriptors(self):
        # The lock is closed last, never unlinked, including failed initialization.
        for name in ("_active_fd", "_dir_fd", "_lock_fd"):
            descriptor = getattr(self, name, None)
            setattr(self, name, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    self._error_code = "CLOSE_FAILED"

    def close(self):
        with self._mutex:
            if self._state != "closed":
                self._close_descriptors()
                self._state = "closed"
