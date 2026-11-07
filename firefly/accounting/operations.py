"""One Chinese operation feed for accounting, permissions and legacy authentication."""

from datetime import datetime, time
from sqlalchemy import literal, union_all, func
from sqlmodel import select
from .models import AccountingEvent
from ..db.models import AuditLog
from .presentation import event_view, actor_text

LEGACY_ACTIONS = {
    "login": "登录成功",
    "login_failed": "登录失败",
    "logout": "退出登录",
    "change_password": "修改登录密码",
    "create_transaction": "新增历史流水",
    "update_transaction": "修改历史流水",
    "delete_transaction": "删除历史流水",
    "backup_create": "创建完整备份",
    "backup_restore": "恢复备份",
    "backup_delete": "删除备份",
    "settings_update": "修改系统设置",
}


async def feed(session, offset=0, limit=50, start=None, end=None):
    first = select(
        AccountingEvent.id.label("id"),
        literal("accounting").label("source"),
        AccountingEvent.created_at.label("created_at"),
    )
    second = select(
        AuditLog.id.label("id"),
        literal("legacy").label("source"),
        AuditLog.created_at.label("created_at"),
    )
    if start:
        bound = datetime.combine(start, time.min)
        first = first.where(AccountingEvent.created_at >= bound)
        second = second.where(AuditLog.created_at >= bound)
    if end:
        bound = datetime.combine(end, time.max)
        first = first.where(AccountingEvent.created_at <= bound)
        second = second.where(AuditLog.created_at <= bound)
    combined = union_all(first, second).subquery()
    total = (await session.exec(select(func.count()).select_from(combined))).one()
    keys = (
        (
            await session.execute(
                select(combined)
                .order_by(combined.c.created_at.desc(), combined.c.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    result = []
    for key in keys:
        if key["source"] == "accounting":
            row = await session.get(AccountingEvent, key["id"])
            item = event_view(row)
        else:
            row = await session.get(AuditLog, key["id"])
            item = legacy_view(row)
        result.append(
            {
                k: item.get(k)
                for k in [
                    "id",
                    "created_at",
                    "title",
                    "description",
                    "operator_name",
                    "platform_name",
                    "book_id",
                ]
            }
        )
    return {"items": result, "total": total, "offset": offset, "limit": limit}


def legacy_view(row):
    actor = (row.extra or {}).get("actor") or {}
    operator, platform = actor_text(actor)
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat(),
        "title": LEGACY_ACTIONS.get(row.action, "系统数据操作"),
        "description": row.detail or "操作已记录",
        "operator_name": operator,
        "platform_name": platform,
        "book_id": None,
    }
