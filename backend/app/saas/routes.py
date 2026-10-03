"""Account and organization APIs for a single-host, manually invited pilot."""
from datetime import timedelta
import importlib.util
import os
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy import text
from typing import Literal

from app.saas import auth
from app.saas.context import TenantContext, is_saas_mode
from app.saas.models import SaaSAuditLog, SaaSInvite, SaaSMembership, SaaSOrganization, SaaSSession, SaaSUser
from app.saas.store import CONTROL_LOCK, session_factory


def _private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/saas", tags=["saas"], dependencies=[Depends(_private_response)])
Role = Literal["owner", "editor", "reviewer", "viewer"]
InviteRole = Literal["editor", "reviewer", "viewer"]


def _email(value):
    value = value.strip().casefold()
    if not re.fullmatch(r"[^@\s\x00-\x1f\x7f]{1,64}@[a-z0-9.-]+\.[a-z]{2,63}", value) or len(value) > 320:
        raise ValueError("请输入有效邮箱地址")
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Register(Input):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=320)
    password: SecretStr
    organization_name: str = Field(default="", max_length=120)
    invite_token: str | None = Field(default=None, min_length=32, max_length=128)
    _normalize_email = field_validator("email")(_email)

    @field_validator("name", "organization_name")
    @classmethod
    def clean_name(cls, value):
        return value.strip()


class Login(Input):
    email: str = Field(max_length=320)
    password: SecretStr
    _normalize_email = field_validator("email")(_email)


class OrganizationCreate(Input):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("组织名称不能为空")
        return value.strip()


class MemberUpdate(Input):
    role: Role | None = None
    is_active: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def has_change(self):
        if self.role is None and self.is_active is None:
            raise ValueError("请指定角色或成员启用状态")
        return self


class InviteCreate(Input):
    email: str = Field(max_length=320)
    role: InviteRole = "viewer"
    _normalize_email = field_validator("email")(_email)


class InviteAccept(Input):
    token: str = Field(min_length=32, max_length=128)


def _require_mode():
    if not is_saas_mode():
        raise HTTPException(409, "当前为本地模式，账号功能未启用")


def _write(request):
    _require_mode()
    auth.validate_csrf(request)


def _context(db, request, *, owner=False):
    session, user, _ = auth.read_session(db, request)
    org = auth.selected_organization(request, session, auth.user_organizations(db, user.id))
    if org is None:
        raise HTTPException(403, "当前账号没有可访问的组织")
    if owner and org["role"] != "owner":
        raise HTTPException(403, "此操作需要组织所有者权限")
    return TenantContext(organization_id=org["id"], user_id=user.id, role=org["role"], organization_name=org["name"])


def _begin(db):
    db.execute(text("BEGIN IMMEDIATE"))


def organization_limits():
    """Admission limits for this single-host pilot, not throughput promises.

    Operators may lower these limits. Invalid configuration rejects admission
    before writing user/organization/membership records.
    """
    values = {}
    for name, maximum in (("SAAS_MAX_ORGANIZATIONS", 100), ("SAAS_MAX_OWNED_ORGANIZATIONS", 3)):
        raw = os.getenv(name, str(maximum)).strip()
        if not re.fullmatch(r"[1-9][0-9]{0,2}", raw) or not 1 <= int(raw) <= maximum:
            raise HTTPException(503, "组织容量配置不可用，请联系服务管理员")
        values[name] = int(raw)
    return values["SAAS_MAX_ORGANIZATIONS"], values["SAAS_MAX_OWNED_ORGANIZATIONS"]


def _owned_organization_capacity(db, user_id, maximum):
    owned = db.query(SaaSMembership).filter(SaaSMembership.user_id == user_id,
        SaaSMembership.role == "owner", SaaSMembership.is_active.is_(True)).count()
    if owned >= maximum:
        raise HTTPException(429, f"每个账号最多拥有 {maximum} 个组织，请使用现有组织或联系服务管理员")


def _organization_capacity(db, user_id=None):
    maximum, owned_maximum = organization_limits()
    if db.query(SaaSOrganization).count() >= maximum:
        raise HTTPException(429, "本实例的组织名额已满，请联系服务管理员；已有组织仍可正常使用")
    if user_id is not None:
        _owned_organization_capacity(db, user_id, owned_maximum)


def _member_capacity(db, organization_id):
    if importlib.util.find_spec("app.saas.commerce") is not None:
        from app.saas.commerce import get_plan_limits
        limit = int(get_plan_limits(organization_id, db=db)["members"])
    else:
        limit = 3  # Standalone control-plane tests; commerce supplies runtime limits.
    count = db.query(SaaSMembership).filter(SaaSMembership.organization_id == organization_id,
                                           SaaSMembership.is_active.is_(True)).count()
    if count >= limit:
        raise HTTPException(409, "组织成员名额已满，请联系所有者调整方案")


def _invitation(db, token, email):
    invite = db.query(SaaSInvite).filter(SaaSInvite.token_hash == auth.token_hash(token)).first()
    if (not invite or invite.accepted_at is not None or invite.expires_at <= auth.now()
            or invite.email != email or invite.role not in {"editor", "reviewer", "viewer"}):
        raise HTTPException(400, "邀请无效、已使用、已过期或与当前邮箱不匹配")
    # A removed/demoted inviter cannot keep admitting members with old links.
    owner = db.query(SaaSMembership).filter(SaaSMembership.organization_id == invite.organization_id,
        SaaSMembership.user_id == invite.created_by, SaaSMembership.is_active.is_(True), SaaSMembership.role == "owner").first()
    if owner is None:
        raise HTTPException(400, "邀请已失效，请向组织所有者重新申请")
    return invite


def _accept(db, invite, user):
    membership = db.query(SaaSMembership).filter(SaaSMembership.organization_id == invite.organization_id,
                                               SaaSMembership.user_id == user.id).first()
    if membership is not None and membership.is_active:
        # Invitations never change an existing active member's role.
        raise HTTPException(409, "你已经是该组织成员，角色调整需由所有者操作")
    _member_capacity(db, invite.organization_id)
    if membership is None:
        membership = SaaSMembership(organization_id=invite.organization_id, user_id=user.id,
            role=invite.role, is_active=True, created_at=auth.now())
        db.add(membership)
    else:
        membership.is_active, membership.role = True, invite.role
    invite.accepted_at, invite.accepted_by = auth.now(), user.id
    db.flush()
    organization = db.get(SaaSOrganization, invite.organization_id)
    ctx = TenantContext(organization_id=organization.id, user_id=user.id, role=membership.role, organization_name=organization.name)
    auth.audit_in_transaction(db, ctx, "invite.accepted", f"invite:{invite.id}", {"role": membership.role})
    return {"id": organization.id, "name": organization.name, "role": membership.role}


def _member_out(member, user):
    return {"id": member.id, "user_id": user.id, "email": user.email, "name": user.name,
            "role": member.role, "is_active": member.is_active}


@router.get("/session")
def session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not is_saas_mode():
        return auth.session_payload(request, None)
    with session_factory() as db:
        return auth.session_payload(request, db)


@router.post("/auth/register", status_code=201)
def register(body: Register, request: Request, response: Response):
    _require_mode()
    auth.check_auth_rate_limit(request, body.email)
    if not auth.allow_registration() and not body.invite_token:
        raise HTTPException(403, "公开注册已关闭，请使用组织邀请注册")
    password = body.password.get_secret_value()
    if not body.name or not 12 <= len(password) <= 256:
        raise HTTPException(422, "姓名不能为空，密码需为 12 至 256 个字符")
    if not body.invite_token and not body.organization_name:
        raise HTTPException(422, "请填写组织名称")
    encoded = auth.hash_password(password)
    with CONTROL_LOCK, session_factory() as db:
        _begin(db)
        if db.query(SaaSUser.id).filter(SaaSUser.email == body.email).first():
            raise HTTPException(409, "无法创建此账号，请检查信息或尝试登录")
        invitation = _invitation(db, body.invite_token, body.email) if body.invite_token else None
        if invitation is None:
            _organization_capacity(db)
        user = SaaSUser(email=body.email, name=body.name, password_hash=encoded, is_active=True, created_at=auth.now())
        db.add(user)
        db.flush()
        if invitation:
            org = _accept(db, invitation, user)
        else:
            organization = SaaSOrganization(name=body.organization_name, created_at=auth.now())
            db.add(organization)
            db.flush()
            db.add(SaaSMembership(user_id=user.id, organization_id=organization.id, role="owner", is_active=True, created_at=auth.now()))
            db.flush()
            org = {"id": organization.id, "name": organization.name, "role": "owner"}
        ctx = TenantContext(organization_id=org["id"], user_id=user.id, role=org["role"], organization_name=org["name"])
        auth.audit_in_transaction(db, ctx, "account.registered", f"user:{user.id}")
        result = auth.issue_session(db, request, response, user, org["id"])
        db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response):
    _require_mode()
    auth.check_auth_rate_limit(request, body.email)
    password = body.password.get_secret_value()
    if not 1 <= len(password) <= 256:
        raise HTTPException(401, "邮箱或密码不正确")
    with session_factory() as db:
        user = db.query(SaaSUser).filter(SaaSUser.email == body.email).first()
        valid = auth.verify_password(password, user.password_hash) if user else (auth.dummy_password_check(password) or False)
        user_id = user.id if user else None
        if not valid or not user or not user.is_active:
            raise HTTPException(401, "邮箱或密码不正确")
    with CONTROL_LOCK, session_factory() as db:
        _begin(db)
        user = db.get(SaaSUser, user_id)
        if not user or not user.is_active:
            raise HTTPException(401, "邮箱或密码不正确")
        organizations = auth.user_organizations(db, user.id)
        org = organizations[0] if organizations else None
        result = auth.issue_session(db, request, response, user, org["id"] if org else None)
        ctx = TenantContext(organization_id=org["id"], user_id=user.id, role=org["role"], organization_name=org["name"]) if org else None
        auth.audit_in_transaction(db, ctx, "session.created", f"user:{user.id}")
        db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    _write(request)
    with CONTROL_LOCK, session_factory() as db:
        _begin(db)
        session, user, _ = auth.read_session(db, request)
        session.revoked_at = auth.now()
        organizations = auth.user_organizations(db, user.id)
        # Logging out must still work after the selected membership was revoked.
        org = next((item for item in organizations if item["id"] == session.active_organization_id),
                   organizations[0] if organizations else None)
        ctx = TenantContext(organization_id=org["id"], user_id=user.id, role=org["role"], organization_name=org["name"]) if org else None
        auth.audit_in_transaction(db, ctx, "session.revoked", f"user:{user.id}")
        db.commit()
    response.delete_cookie(auth.SESSION_COOKIE, path="/", httponly=True, secure=auth.env_bool("SAAS_SECURE_COOKIE", True), samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return {"logged_out": True}


@router.get("/organizations")
def organizations(request: Request):
    _require_mode()
    with session_factory() as db:
        _, user, _ = auth.read_session(db, request)
        return {"items": auth.user_organizations(db, user.id)}


@router.post("/organizations", status_code=201)
def create_organization(body: OrganizationCreate, request: Request):
    _write(request)
    with CONTROL_LOCK, session_factory() as db:
        _begin(db)
        session, user, _ = auth.read_session(db, request)
        _organization_capacity(db, user.id)
        org = SaaSOrganization(name=body.name, created_at=auth.now())
        db.add(org)
        db.flush()
        db.add(SaaSMembership(organization_id=org.id, user_id=user.id, role="owner", is_active=True, created_at=auth.now()))
        session.active_organization_id = org.id
        ctx = TenantContext(organization_id=org.id, user_id=user.id, role="owner", organization_name=org.name)
        auth.audit_in_transaction(db, ctx, "organization.created", f"organization:{org.id}")
        db.commit()
        return {"id": org.id, "name": org.name, "role": "owner"}


@router.get("/members")
def members(request: Request):
    _require_mode()
    with session_factory() as db:
        ctx = _context(db, request)
        rows = db.query(SaaSMembership, SaaSUser).join(SaaSUser, SaaSUser.id == SaaSMembership.user_id).filter(
            SaaSMembership.organization_id == ctx.organization_id).order_by(SaaSMembership.created_at).all()
        return {"items": [_member_out(member, user) for member, user in rows]}


@router.patch("/members/{member_id}")
def update_member(member_id: str, body: MemberUpdate, request: Request):
    _write(request)
    with CONTROL_LOCK, session_factory() as db:
        _begin(db)
        ctx = _context(db, request, owner=True)
        member = db.query(SaaSMembership).filter(SaaSMembership.id == member_id, SaaSMembership.organization_id == ctx.organization_id).first()
        if member is None:
            raise HTTPException(404, "成员不存在")
        role = body.role if body.role is not None else member.role
        active = body.is_active if body.is_active is not None else member.is_active
        if member.is_active and member.role == "owner" and (role != "owner" or not active):
            owners = db.query(SaaSMembership).filter(SaaSMembership.organization_id == ctx.organization_id,
                SaaSMembership.is_active.is_(True), SaaSMembership.role == "owner").count()
            if owners <= 1:
                raise HTTPException(409, "不能撤销或降级组织的最后一位所有者")
        if active and not member.is_active:
            _member_capacity(db, ctx.organization_id)
        if active and role == "owner" and not (member.is_active and member.role == "owner"):
            _, owned_maximum = organization_limits()
            _owned_organization_capacity(db, member.user_id, owned_maximum)
        member.role, member.is_active = role, active
        auth.audit_in_transaction(db, ctx, "membership.updated", f"membership:{member.id}", {"role": role, "is_active": active})
        db.commit()
        return _member_out(member, db.get(SaaSUser, member.user_id))


@router.post("/invites", status_code=201)
def create_invite(body: InviteCreate, request: Request):
    _write(request)
    with CONTROL_LOCK, session_factory() as db:
        _begin(db)
        ctx = _context(db, request, owner=True)
        token = secrets.token_urlsafe(32)
        invite = SaaSInvite(organization_id=ctx.organization_id, email=body.email, role=body.role,
            token_hash=auth.token_hash(token), created_by=ctx.user_id, created_at=auth.now(),
            expires_at=auth.now() + timedelta(days=3))
        db.add(invite)
        db.flush()
        auth.audit_in_transaction(db, ctx, "invite.created", f"invite:{invite.id}", {"role": body.role})
        db.commit()
        return {"id": invite.id, "email": invite.email, "role": invite.role, "expires_at": auth.iso(invite.expires_at),
                "token": token, "invite_url": f"/?invite={token}"}


@router.post("/invites/accept")
def accept_invite(body: InviteAccept, request: Request):
    _write(request)
    with CONTROL_LOCK, session_factory() as db:
        _begin(db)
        session, user, _ = auth.read_session(db, request)
        invite = _invitation(db, body.token, user.email)
        organization = _accept(db, invite, user)
        session.active_organization_id = organization["id"]
        db.commit()
        return {"organization": organization}


@router.get("/audit")
def audit(request: Request, limit: int = Query(50, ge=1, le=100)):
    _require_mode()
    with session_factory() as db:
        ctx = _context(db, request, owner=True)
        rows = db.query(SaaSAuditLog).filter(SaaSAuditLog.organization_id == ctx.organization_id).order_by(
            SaaSAuditLog.created_at.desc(), SaaSAuditLog.id.desc()).limit(limit).all()
        return {"items": [{"id": row.id, "action": row.action, "resource": row.resource,
                           "actor_user_id": row.actor_user_id, "details": row.details_json,
                           "created_at": auth.iso(row.created_at)} for row in rows]}
