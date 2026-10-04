#!/usr/bin/env python3
"""Read retained private diagnostic metadata by request ID, without taking a writer lock.

Exit 0: found, retained files scanned without detected defects. Exit 1: not
observed in that retained window. Exit 2: invalid arguments or uncertain scan.
Even exit 0/1 never proves complete history, request outcome, or absence of faults.
"""
import argparse
import io
import json
import os
from pathlib import Path
import re
import stat
import sys

# Import only the side-effect-free schema; never create import bytecode or load config.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.diagnostic_schema import MAX_RECORD_BYTES, validate_record


MARKER_NAME = "diagnostics-format.json"
MARKER = {"format": "zhiyuan-diagnostics-jsonl", "schema_version": 1}
LOG_NAME = "diagnostics.jsonl"
LOCK_NAME = ".diagnostics.lock"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_BACKUPS = 20
LOG_NAMES = [f"{LOG_NAME}.{index}" for index in range(MAX_BACKUPS, 0, -1)] + [LOG_NAME]
KNOWN_NAMES = frozenset([MARKER_NAME, LOCK_NAME, *LOG_NAMES])
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
REASON_CODES = frozenset({"INVALID_ARGUMENT", "UNSAFE_DIRECTORY", "DIRECTORY_CHANGED",
    "DIRECTORY_UNAVAILABLE", "TOO_MANY_FILES", "FILE_UNAVAILABLE", "UNSAFE_FILE", "FILE_CHANGED",
    "MARKER_MISSING", "MARKER_INVALID", "UNKNOWN_ENTRY", "NO_LOG_FILES", "FILE_TOO_LARGE",
    "RECORD_TOO_LARGE", "PARTIAL_LINE", "INVALID_RECORD", "READ_FAILED", "SCAN_UNAVAILABLE"})


class InvalidArguments(Exception):
    pass


class ScanError(Exception):
    """Only fixed internal reason codes may cross the CLI boundary."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise InvalidArguments from None


def parse_options(argv=None):
    parser = SafeParser(prog="diagnostic_query.py", allow_abbrev=False, description=__doc__)
    parser.add_argument("--directory", required=True, action="append", metavar="ABS")
    parser.add_argument("--request-id", required=True, action="append", metavar="32LOWERHEX")
    parser.add_argument("--limit", action="append", metavar="1..200")
    args = parser.parse_args(argv)
    if len(args.directory) != 1 or len(args.request_id) != 1 or len(args.limit or []) > 1:
        raise InvalidArguments
    value = args.limit[0] if args.limit else "50"
    if not re.fullmatch(r"[0-9]{1,3}", value):
        raise InvalidArguments
    return args.directory[0], args.request_id[0], int(value)


def _arguments(directory, request_id, limit):
    try:
        directory = os.fspath(directory)
    except (TypeError, ValueError):
        raise InvalidArguments from None
    if (type(directory) is not str or not os.path.isabs(directory) or "\0" in directory
            or not directory or directory.endswith(os.sep)
            or any(part in {".", ".."} for part in directory.split(os.sep))
            or type(request_id) is not str or re.fullmatch(r"[a-f0-9]{32}", request_id) is None
            or type(limit) is not int or not 1 <= limit <= 200):
        raise InvalidArguments
    return directory


def _identity(info):
    return info.st_dev, info.st_ino


def _fingerprint(info):
    return (*_identity(info), info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private_directory(info):
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077


def _private_file(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)


def _open_directory(directory):
    descriptor = None
    try:
        before = os.stat(directory, follow_symlinks=False)
        if not _private_directory(before):
            raise ScanError("UNSAFE_DIRECTORY")
        descriptor = os.open(directory, READ_FLAGS | os.O_DIRECTORY)
        opened = os.fstat(descriptor)
        if not _private_directory(opened) or _identity(opened) != _identity(before):
            raise ScanError("DIRECTORY_CHANGED")
        return descriptor, opened
    except ScanError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise ScanError("DIRECTORY_UNAVAILABLE") from None


def _inventory(directory_fd):
    """Bound enumeration as well as content reads. Never print directory entries."""
    result = {}
    try:
        with os.scandir(directory_fd) as entries:
            for item in entries:
                if len(result) >= MAX_BACKUPS + 3:  # 21 logs plus marker and lock.
                    raise ScanError("TOO_MANY_FILES")
                if item.name in KNOWN_NAMES:
                    try:
                        result[item.name] = os.stat(item.name, dir_fd=directory_fd, follow_symlinks=False)
                    except OSError:
                        result[item.name] = None
                else:
                    result[item.name] = None  # Unknown entries are never opened.
        return result
    except ScanError:
        raise
    except Exception:
        raise ScanError("DIRECTORY_UNAVAILABLE") from None


def _open_file(directory_fd, name, expected):
    if expected is None:
        raise ScanError("FILE_UNAVAILABLE")
    if not _private_file(expected):
        raise ScanError("UNSAFE_FILE")
    descriptor = None
    try:
        descriptor = os.open(name, READ_FLAGS, dir_fd=directory_fd)
        opened = os.fstat(descriptor)
        if not _private_file(opened):
            raise ScanError("UNSAFE_FILE")
        if _fingerprint(opened) != _fingerprint(expected):
            raise ScanError("FILE_CHANGED")
        return descriptor
    except ScanError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise ScanError("FILE_UNAVAILABLE") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _json(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))


def _unchanged(directory_fd, name, descriptor, expected):
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return (_private_file(opened) and _private_file(named)
                and _fingerprint(opened) == _fingerprint(named) == _fingerprint(expected))
    except OSError:
        return False


def _marker(directory_fd, inventory):
    if MARKER_NAME not in inventory:
        raise ScanError("MARKER_MISSING")
    descriptor = _open_file(directory_fd, MARKER_NAME, inventory[MARKER_NAME])
    try:
        if os.fstat(descriptor).st_size > MAX_RECORD_BYTES:
            raise ScanError("MARKER_INVALID")
        data = os.read(descriptor, MAX_RECORD_BYTES + 1)
        try:
            value = _json(data)
        except Exception:
            raise ScanError("MARKER_INVALID") from None
        if (type(value) is not dict or value != MARKER
                or type(value.get("schema_version")) is not int):
            raise ScanError("MARKER_INVALID")
        if not _unchanged(directory_fd, MARKER_NAME, descriptor, inventory[MARKER_NAME]):
            raise ScanError("FILE_CHANGED")
    finally:
        os.close(descriptor)


def _scan_file(descriptor, size, request_id, limit, result, reasons):
    """Read at most the initial file size; concurrent appends are detected afterward."""
    data, remaining = bytearray(), size
    while remaining:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            reasons.add("FILE_CHANGED")
            break
        data.extend(chunk)
        remaining -= len(chunk)
    with io.BytesIO(data) as stream:
        remaining = len(data)
        while remaining:
            line = stream.readline(min(MAX_RECORD_BYTES + 1, remaining))
            if not line:
                reasons.add("FILE_CHANGED")
                break
            remaining -= len(line)
            result["counts"]["scanned"] += 1
            oversized = len(line) > MAX_RECORD_BYTES
            # Drain an oversized row without accumulating its content or mistaking
            # its tail for another JSON record. All reads stay inside the size cap.
            while not line.endswith(b"\n") and remaining and len(line) >= MAX_RECORD_BYTES:
                oversized = True
                line = stream.readline(min(65536, remaining))
                if not line:
                    reasons.add("FILE_CHANGED")
                    break
                remaining -= len(line)
            if oversized:
                reasons.add("RECORD_TOO_LARGE")
                result["counts"]["invalid"] += 1
                continue
            if not line.endswith(b"\n"):
                reasons.add("PARTIAL_LINE")
                result["counts"]["invalid"] += 1
                continue
            try:
                record = validate_record(_json(line))
            except Exception:
                record = None
            if record is None:
                reasons.add("INVALID_RECORD")
                result["counts"]["invalid"] += 1
                continue
            if record["request_id"] == request_id:
                result["counts"]["matched"] += 1
                if len(result["records"]) < limit:
                    result["records"].append(record)


def _report():
    return {"schema_version": 1, "scan_status": "unavailable", "match_status": "unknown",
            "complete": False, "records": [], "counts": {"scanned": 0, "matched": 0,
                "returned": 0, "invalid": 0, "changed": 0}, "truncated": False, "reason_codes": []}


def query(directory, request_id, limit=50):
    """Return (fixed report, exit code). Counts are rows, never request/usage totals."""
    result, reasons, changed = _report(), set(), set()
    directory_fd = None
    opened_logs = 0
    try:
        directory = _arguments(directory, request_id, limit)
        directory_fd, directory_info = _open_directory(directory)
        inventory = _inventory(directory_fd)
        _marker(directory_fd, inventory)
        if set(inventory) - KNOWN_NAMES:
            reasons.add("UNKNOWN_ENTRY")
        if LOCK_NAME in inventory and (inventory[LOCK_NAME] is None or not _private_file(inventory[LOCK_NAME])):
            reasons.add("UNSAFE_FILE")  # The reader never opens or acquires this lock.
        logs = [name for name in LOG_NAMES if name in inventory]
        if not logs:
            reasons.add("NO_LOG_FILES")
        for name in logs:
            descriptor = None
            try:
                descriptor = _open_file(directory_fd, name, inventory[name])
                if inventory[name].st_size > MAX_FILE_BYTES:
                    reasons.add("FILE_TOO_LARGE")
                    continue
                opened_logs += 1
                _scan_file(descriptor, inventory[name].st_size, request_id, limit, result, reasons)
            except ScanError as error:
                reasons.add(str(error))
                if str(error) == "FILE_CHANGED":
                    changed.add(name)
            except Exception:
                reasons.add("READ_FAILED")
            finally:
                if descriptor is not None:
                    if not _unchanged(directory_fd, name, descriptor, inventory[name]):
                        reasons.add("FILE_CHANGED")
                        changed.add(name)
                    os.close(descriptor)
        after = _inventory(directory_fd)
        for name in set(inventory) | set(after):
            old, new = inventory.get(name), after.get(name)
            if (name not in inventory or name not in after
                    or (old is None) != (new is None)
                    or (old is not None and _fingerprint(old) != _fingerprint(new))):
                changed.add(name)
                reasons.add("FILE_CHANGED")
        try:
            current = os.stat(directory, follow_symlinks=False)
            directory_same = _private_directory(current) and _identity(current) == _identity(directory_info)
        except OSError:
            directory_same = False
        if not directory_same:
            changed.add("directory")
            reasons.add("DIRECTORY_CHANGED")
    except InvalidArguments:
        reasons.add("INVALID_ARGUMENT")
    except ScanError as error:
        reasons.add(str(error))
    except Exception:
        reasons.add("SCAN_UNAVAILABLE")
    finally:
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except OSError:
                reasons.add("READ_FAILED")
    result["counts"]["changed"] = len(changed)
    result["counts"]["returned"] = len(result["records"])
    result["truncated"] = result["counts"]["matched"] > len(result["records"])
    result["scan_status"] = "unavailable" if not opened_logs else "partial" if reasons else "ok"
    result["match_status"] = ("found" if result["records"] else
                              "not_observed" if result["scan_status"] == "ok" else "unknown")
    result["reason_codes"] = sorted({reason if reason in REASON_CODES else "SCAN_UNAVAILABLE" for reason in reasons})
    code = 2 if result["scan_status"] != "ok" else 0 if result["records"] else 1
    return result, code


def main(argv=None):
    try:
        result, code = query(*parse_options(argv))
    except InvalidArguments:
        result, code = _report(), 2
        result["reason_codes"] = ["INVALID_ARGUMENT"]
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
