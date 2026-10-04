#!/usr/bin/env python3
"""Read-only filesystem free-space checks; explicit directories, no application imports."""
import argparse
from datetime import datetime, timezone
import json
import os
import re
import shutil
import stat
from typing import NamedTuple


ROLES = frozenset({"database", "vectors", "backup", "saas"})
PERCENT_SCALE = 1_000_000


class InvalidArguments(Exception):
    pass


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's message/usage can contain unknown flags, paths and values.
        raise InvalidArguments from None


class Thresholds(NamedTuple):
    min_free_bytes: int | None
    min_free_percent_micros: int | None


def parse_options(argv=None):
    parser = SafeParser(prog="ops_health.py", allow_abbrev=False, description=__doc__)
    parser.add_argument("--directory", action="append", required=True, metavar="ROLE=PATH",
                        help="Existing directory; each role database/vectors/backup/saas at most once.")
    parser.add_argument("--min-free-bytes", action="append", metavar="BYTES",
                        help="Positive integer, at most 2^63-1. Specify this and/or percent explicitly.")
    parser.add_argument("--min-free-percent", action="append", metavar="PERCENT",
                        help="Decimal greater than 0 and at most 100, up to 6 fractional digits.")
    args = parser.parse_args(argv)
    directories = []
    seen = set()
    for item in args.directory:
        role, separator, path = item.partition("=")
        if role not in ROLES or role in seen or not separator or not path or "\0" in path:
            raise InvalidArguments
        seen.add(role)
        directories.append((role, path))
    byte_values, percent_values = args.min_free_bytes or [], args.min_free_percent or []
    if (not byte_values and not percent_values) or len(byte_values) > 1 or len(percent_values) > 1:
        raise InvalidArguments
    min_bytes = min_percent = None
    if byte_values:
        value = byte_values[0]
        if not re.fullmatch(r"[0-9]{1,19}", value) or not 1 <= int(value) <= 2 ** 63 - 1:
            raise InvalidArguments
        min_bytes = int(value)
    if percent_values:
        value = percent_values[0]
        if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,6})?", value):
            raise InvalidArguments
        integer, _, fraction = value.partition(".")
        min_percent = int(integer) * PERCENT_SCALE + int(fraction.ljust(6, "0"))
        if not 0 < min_percent <= 100 * PERCENT_SCALE:
            raise InvalidArguments
    return directories, Thresholds(min_bytes, min_percent)


def check_directory(role, path, thresholds):
    """One non-atomic observation; symlinks refer to their current target filesystem.

    Do not open files, initialize databases, create directories, or walk parents
    to make a missing configured target look healthy.
    """
    result = {"role": role, "status": "unknown", "reason": None,
              "total_bytes": None, "free_bytes": None,
              "min_free_bytes": thresholds.min_free_bytes,
              "min_free_percent": (thresholds.min_free_percent_micros / PERCENT_SCALE
                                   if thresholds.min_free_percent_micros is not None else None)}
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        result["reason"] = "PATH_NOT_FOUND"
        return result
    except NotADirectoryError:
        result["reason"] = "NOT_DIRECTORY"
        return result
    except Exception:
        result["reason"] = "STAT_FAILED"
        return result
    if not stat.S_ISDIR(mode):
        result["reason"] = "NOT_DIRECTORY"
        return result
    try:
        usage = shutil.disk_usage(path)
        total, used, free = usage.total, usage.used, usage.free
    except Exception:
        result["reason"] = "DISK_USAGE_FAILED"
        return result
    if (any(type(value) is not int for value in (total, used, free)) or total <= 0
            or not 0 <= free <= total or not 0 <= used <= total):
        result["reason"] = "INVALID_DISK_USAGE"
        return result
    result["total_bytes"], result["free_bytes"] = total, free
    low_bytes = thresholds.min_free_bytes is not None and free < thresholds.min_free_bytes
    # Exact integer comparison avoids rounding at the configured boundary.
    low_percent = (thresholds.min_free_percent_micros is not None
                   and free * 100 * PERCENT_SCALE < total * thresholds.min_free_percent_micros)
    result["status"] = "low" if low_bytes or low_percent else "ok"
    result["reason"] = "LOW_SPACE" if low_bytes or low_percent else "OK"
    return result


def report(status, reason, checks):
    return {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": status, "reason": reason, "checks": checks}


def main(argv=None):
    try:
        directories, thresholds = parse_options(argv)
    except InvalidArguments:
        result = report("unknown", "INVALID_ARGUMENT", [])
    else:
        # Roles can share a filesystem: report each observation, never sum them.
        checks = [check_directory(role, path, thresholds) for role, path in directories]
        statuses = {item["status"] for item in checks}
        status = "unknown" if "unknown" in statuses else "low" if "low" in statuses else "ok"
        result = report(status, {"unknown": "CHECKS_UNKNOWN", "low": "LOW_SPACE", "ok": "OK"}[status], checks)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return {"ok": 0, "low": 1, "unknown": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
