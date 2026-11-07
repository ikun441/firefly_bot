"""The one configured web administrator is the only web login identity."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from ..api.router import _should_protect
from ..api.deps import get_token_from_request
from ..config import get_config
from ..services.auth import verify_token
from .identity import Actor, acting_as
from .api import transaction
from .models import Book


class AccountingIdentityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not _should_protect(request.url.path):
            return await call_next(request)
        cfg = get_config()
        payload = verify_token(get_token_from_request(request))
        if not payload or payload.get("user") != cfg.auth.username:
            return JSONResponse(
                {
                    "success": False,
                    "code": "UNAUTHENTICATED",
                    "message": "请使用唯一管理员账号登录",
                },
                status_code=401,
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse(
                    {"success": False, "code": "ORIGIN_FORBIDDEN"}, status_code=403
                )
        with acting_as(Actor("admin", cfg.auth.username, "web")):
            quiet = {
                "/api/health",
                "/api/meta",
                "/api/auth/me",
                "/api/v2/access",
                "/api/bot/status",
                "/api/push/status",
                "/api/v2/acceptance/status",
            }
            if request.url.path not in quiet:
                from .service import event

                path = request.url.path
                area = next(
                    (
                        label
                        for part, label in [
                            ("operation-events", "操作日志"),
                            ("permissions", "权限设置"),
                            ("expense-categories", "预算费用类别"),
                            ("expense-budgets", "费用预算"),
                            ("chart-accounts", "会计科目"),
                            ("vouchers", "会计凭证"),
                            ("business-drafts", "业务草稿"),
                            ("reconciliations", "资金对账"),
                            ("periods", "期间结账"),
                            ("reports", "财务报表"),
                            ("backup", "数据备份"),
                            ("books", "账套管理"),
                            ("settings", "系统设置"),
                            ("auth", "登录账户"),
                        ]
                        if part in path
                    ),
                    "业务资料",
                )
                verb = {
                    "GET": "查看",
                    "POST": "提交",
                    "PUT": "保存",
                    "PATCH": "修改",
                    "DELETE": "删除",
                }.get(request.method, "操作")
                async with transaction() as audit_session:
                    event(audit_session, "web_request", operation_name=verb + area)
            # Old write paths cannot bypass the new immutable ledger once a book exists.
            path = request.url.path
            guarded = (
                "/api/transactions",
                "/api/accounts",
                "/api/categories",
                "/api/import",
                "/api/subscriptions",
                "/api/ai/nl-confirm",
                "/api/ai/natural",
            )
            if request.method not in {"GET", "HEAD", "OPTIONS"} and path.startswith(
                guarded
            ):
                from sqlmodel import select

                async with transaction() as s:
                    book = (await s.exec(select(Book))).first()
                if book:
                    return JSONResponse(
                        {
                            "success": False,
                            "code": "LEGACY_READ_ONLY",
                            "message": "会计账套已启用，请在凭证中心或业务草稿中处理；历史流水只读",
                        },
                        status_code=409,
                    )
            if path.startswith("/api/backup/restore") or path == "/api/system/update":
                from sqlmodel import select

                async with transaction() as s:
                    book = (await s.exec(select(Book))).first()
                if book:
                    return JSONResponse(
                        {
                            "success": False,
                            "code": "ACCOUNTING_MAINTENANCE_REQUIRED",
                            "message": "已有会计账套，旧版还原/一键升级不可使用；请先备份并使用受控迁移",
                        },
                        status_code=409,
                    )
            return await call_next(request)
