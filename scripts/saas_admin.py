#!/usr/bin/env python3
"""Local operator tools: explicit entitlements, no checkout or payment gateway."""
import argparse
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", help="Explicit SaaS data directory; never the demo database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-orgs")
    plan = commands.add_parser("set-plan")
    plan.add_argument("--organization", required=True)
    plan.add_argument("--plan", choices=("trial", "team"), required=True)
    for metric in ("ai-requests", "documents", "members"):
        plan.add_argument(f"--{metric}", type=int)
    audit = commands.add_parser("reserved-usage")
    audit.add_argument("--organization", required=True)
    args = parser.parse_args(argv)
    from dotenv import dotenv_values
    env_file = Path(os.getenv("SAAS_ENV_FILE", str(ROOT / ".env.saas")))
    if env_file.exists():
        for name, value in dotenv_values(env_file).items():
            if value is not None:
                os.environ.setdefault(name, value)
    directory = Path(args.data_dir or os.getenv("SAAS_DATA_DIR", str(ROOT / ".data" / "saas"))).expanduser().absolute()
    if directory.is_symlink() or (directory / "control.db").is_symlink() or not (directory / "control.db").is_file():
        parser.error("SaaS 控制库不存在或路径不安全；先启动 SaaS 并创建组织，不会自动创建空数据库。")
    os.environ["SAAS_MODE"], os.environ["SAAS_DATA_DIR"] = "true", str(directory)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    from sqlalchemy import func, select
    from sqlalchemy.exc import SQLAlchemyError
    from app.saas import store
    from app.saas.commerce import CommerceError, get_plan_limits, provision_plan
    from app.saas.commerce_models import OrganizationPlan, UsageEvent
    from app.saas.models import Membership, Organization
    try:
        if args.command == "list-orgs":
            with store.session_factory() as db:
                result = []
                for org in db.scalars(select(Organization).order_by(Organization.created_at)).all():
                    current = db.get(OrganizationPlan, org.id)
                    members = db.scalar(select(func.count()).select_from(Membership).where(
                        Membership.organization_id == org.id, Membership.is_active.is_(True)))
                    result.append({"id": org.id, "name": org.name, "plan": current.code if current else "trial",
                                   "limits": get_plan_limits(org.id, db=db), "active_members": members})
        else:
            if not re.fullmatch(r"[a-f0-9]{32}", args.organization):
                parser.error("组织标识无效。")
            with store.session_factory() as db:
                if not db.get(Organization, args.organization):
                    parser.error("组织不存在。")
            if args.command == "set-plan":
                limits = {name: getattr(args, name) for name in ("ai_requests", "documents", "members")}
                if args.plan == "team" and any(value is None or value < 0 for value in limits.values()):
                    parser.error("团队套餐必须显式提供三个非负整数额度。")
                if args.plan == "trial" and any(value is not None for value in limits.values()):
                    parser.error("试用套餐使用固定额度，请不要附带自定义额度。")
                result = provision_plan(args.organization, code=args.plan, limits=limits if args.plan == "team" else None)
                result["notice"] = "仅修改服务权益；未执行支付、扣费或开票。"
            else:
                with store.session_factory() as db:
                    events = db.scalars(select(UsageEvent).where(UsageEvent.organization_id == args.organization,
                        UsageEvent.status == "reserved").order_by(UsageEvent.created_at).limit(200)).all()
                    result = {"organization_id": args.organization, "limit": 200, "reservations": [
                        {"request_ref": event.request_hash[:12], "period": event.period, "amount": event.amount,
                         "created_at": event.created_at.isoformat()} for event in events],
                        "notice": "只读核对；不能仅根据时间自动退款。未打印内部结算 token。"}
    except CommerceError as exc:
        parser.exit(2, f"操作未完成：{exc}\n")
    except SQLAlchemyError:
        parser.exit(2, "操作未完成：控制库不可用，请检查版本、完整性与目录权限。\n")
    finally:
        store.close_control_db()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
