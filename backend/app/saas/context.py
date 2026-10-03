"""Trusted request identity, propagated to sync work and model logging."""
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class TenantContext:
    organization_id: str
    user_id: str
    role: str
    organization_name: str = ""


current_tenant: ContextVar[TenantContext | None] = ContextVar("current_tenant", default=None)


def is_saas_mode() -> bool:
    return os.getenv("SAAS_MODE", "false").lower() in {"true", "1", "yes", "on"}


def data_directory() -> Path:
    default = Path(__file__).resolve().parents[3] / ".data" / "saas"
    return Path(os.getenv("SAAS_DATA_DIR", str(default))).resolve()


def organization_directory(organization_id: str) -> Path:
    # Only server-generated identifiers are usable as filesystem paths.
    if not re.fullmatch(r"[a-f0-9]{32}", organization_id):
        raise ValueError("组织标识无效")
    return data_directory() / "tenants" / organization_id
