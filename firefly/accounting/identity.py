"""Server-derived identities. Authorization is checked on every operation."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import re
from sqlmodel import select


@dataclass(frozen=True)
class Actor:
    kind: str
    identifier: str
    platform: str
    group_id: str = ""
    bot_id: str = ""

    @property
    def key(self):
        return f"{self.kind}:{self.identifier}"

    def snapshot(self):
        return {**asdict(self), "key": self.key}


current_actor = ContextVar(
    "firefly_actor", default=Actor("system", "scheduler", "system")
)


@contextmanager
def acting_as(actor):
    token = current_actor.set(actor)
    try:
        yield actor
    finally:
        current_actor.reset(token)


def qq_number(value):
    value = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]{4,19}", value):
        from .domain import AccountingError

        raise AccountingError("QQ_INVALID", "QQ 账号必须为 5–20 位数字")
    return value


async def access(session, actor=None):
    from .models import AccessControl, QQGrant

    actor = actor or current_actor.get()
    control = await session.get(AccessControl, 1)
    admin = actor.kind == "admin" or (
        actor.kind == "qq" and control and control.admin_qq == actor.identifier
    )
    rights = dict(create=False, read=False, update=False, delete=False)
    if admin:
        rights = dict.fromkeys(rights, True)
    elif actor.kind == "qq":
        grant = (
            await session.exec(select(QQGrant).where(QQGrant.qq == actor.identifier))
        ).first()
        if grant and grant.enabled:
            rights = {k: getattr(grant, "can_" + k) for k in rights}
    return {"actor": actor.snapshot(), "is_admin": bool(admin), "permissions": rights}


async def require(session, permission):
    result = await access(session)
    if permission == "admin":
        allowed = result["is_admin"]
    else:
        allowed = result["permissions"].get(permission, False)
    if not allowed:
        from .domain import AccountingError

        raise AccountingError("FORBIDDEN", "没有执行此操作的权限", 403)
    return result
