"""Organization-scoped control-plane endpoints; no payment or secret write API."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.saas.commerce import CommerceError, billing_snapshot, request_upgrade
from app.saas.connections import ConnectionUnavailable, connection_snapshot, update_connection
from app.saas.context import current_tenant


router = APIRouter(prefix="/saas", tags=["saas"])


class ConnectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    is_default: StrictBool | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.enabled is None and self.is_default is None:
            raise ValueError("至少提供一个连接设置。")
        return self


class UpgradeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_plan: Literal["team"] = "team"
    note: str = Field(default="", max_length=1000)


def _context(owner=False):
    context = current_tenant.get()
    if context is None:
        raise HTTPException(401, "请先登录并选择组织。")
    if owner and context.role != "owner":
        raise HTTPException(403, "只有组织所有者可以修改连接或申请套餐。")
    return context


@router.get("/connections")
def connections():
    context = _context()
    return connection_snapshot(context.organization_id, owner=context.role == "owner")


@router.patch("/connections/{provider}")
def patch_connection(provider: str, body: ConnectionPatch):
    context = _context(owner=True)
    try:
        return update_connection(context.organization_id, provider, enabled=body.enabled, is_default=body.is_default)
    except ConnectionUnavailable as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/billing")
def billing():
    context = _context()
    from app.db.session import count_tenant_documents
    return billing_snapshot(context.organization_id, document_count=count_tenant_documents(context.organization_id))


@router.post("/billing/upgrade-request", status_code=201)
def upgrade_request(body: UpgradeInput):
    context = _context(owner=True)
    try:
        return request_upgrade(context.organization_id, context.user_id, requested_plan=body.requested_plan, note=body.note)
    except CommerceError as exc:
        raise HTTPException(422, str(exc)) from None
