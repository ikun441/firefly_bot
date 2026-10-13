"""流萤 Firefly · 全量接口测试

本文件对后端全部 REST 接口做一次端到端验收，覆盖：
    1. 免鉴权接口与鉴权守卫（未登录必须 401）
    2. 参数校验与边界值（越界、类型错误、非法枚举）
    3. 业务闭环（记账 → 统计 → 报告 → 税检 → 审计）
    4. 副作用接口的**数据回收**（跑完把库恢复成原样）

设计原则
--------
**全部写操作都在「沙箱」里进行，结束时精确回收。**

具体做法不是「删库重来」，而是：
    - 开跑前读取全部 13 张表的主键集合，形成快照；
    - 跑测时记录本进程新建的每一个主键；
    - 结束时只删除「快照之后新增」的行，并还原被改动的行。

之所以不用「跑完直接清空数据库」：本项目是毕设演示系统，
用户很可能已经录入了真实的演示数据。测试必须**可反复运行且不破坏现场**，
否则每次跑测试都要重新录一遍数据，这在答辩前一天是灾难。

用法
----
    # 服务已在 2525 端口运行
    ./.venv/bin/python tests/test_api.py

    # 指定地址 / 口令
    ./.venv/bin/python tests/test_api.py --base-url http://127.0.0.1:2525 \
        --username admin --password Firefly2026

    # 跳过需要真实 LLM 的用例（不消耗 API 额度）
    ./.venv/bin/python tests/test_api.py --no-llm

退出码：0 表示全部通过，1 表示存在失败用例。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

# 把项目根塞进 sys.path。
#
# 为什么必须手动做：本脚本既走 HTTP 打接口，又**在进程内** import firefly
# 干两件关键的事 —— take_snapshot() 建回收基线、restore() 删测试数据
# （另有 3 个用例直接读 config 断言脱敏）。
#
# 而 `./.venv/bin/python tests/test_api.py` 这种跑法，Python 只会把
# **tests/** 放进 sys.path，项目根并不在其中，于是 `import firefly` 全线
# ImportError。症状极具欺骗性：接口用例全绿、只有回收阶段打印一行
# `[回收] 失败：No module named 'firefly'`，很容易被当成无害噪音滑过去，
# 实际后果是测试数据**从未被回收过**，一次次堆在库里，
# 最终表现为总览与报告页面凭空多出支出项。
#
# 放在这里（而非靠调用方 cd）是为了让脚本在任意工作目录下跑都成立。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ============================================================ 测试框架

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
WARN = "WARN"


@dataclass
class CaseResult:
    """单条用例的结果。"""

    group: str
    name: str
    status: str
    detail: str = ""
    elapsed_ms: float = 0.0


@dataclass
class Runner:
    """极简测试运行器。

    没有引入 pytest 是有意为之：本项目要在答辩现场用一条命令跑通，
    且需要把结果渲染成一份可提交的报告，自带运行器反而更可控
    （pytest 的夹具与插件在陌生机器上更容易出问题）。
    """

    results: List[CaseResult] = field(default_factory=list)
    current_group: str = "通用"

    def group(self, name: str) -> None:
        self.current_group = name

    async def case(self, name: str, coro) -> Any:
        """执行一个用例，捕获异常并记录耗时。"""
        start = time.perf_counter()
        try:
            result = await coro
            elapsed = (time.perf_counter() - start) * 1000
            if isinstance(result, tuple) and len(result) == 2:
                ok, detail = result
                self.results.append(
                    CaseResult(
                        self.current_group,
                        name,
                        PASS if ok else FAIL,
                        str(detail),
                        elapsed,
                    )
                )
                return ok
            self.results.append(
                CaseResult(self.current_group, name, PASS, str(result or ""), elapsed)
            )
            return True
        except AssertionError as exc:
            elapsed = (time.perf_counter() - start) * 1000
            self.results.append(
                CaseResult(self.current_group, name, FAIL, str(exc), elapsed)
            )
            return False
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.perf_counter() - start) * 1000
            self.results.append(
                CaseResult(
                    self.current_group,
                    name,
                    FAIL,
                    f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}",
                    elapsed,
                )
            )
            return False

    def skip(self, name: str, reason: str) -> None:
        self.results.append(CaseResult(self.current_group, name, SKIP, reason))

    def warn(self, name: str, reason: str) -> None:
        self.results.append(CaseResult(self.current_group, name, WARN, reason))

    # -- 汇总 ------------------------------------------------------

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == PASS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == SKIP)

    @property
    def warned(self) -> int:
        return sum(1 for r in self.results if r.status == WARN)


# ============================================================ 断言助手


def expect(cond: bool, message: str) -> Tuple[bool, str]:
    """返回 (通过?, 说明)，交给 Runner 统一记录。"""
    return (True, "符合预期") if cond else (False, message)


def envelope_ok(payload: Any) -> bool:
    """校验统一响应信封 { success: bool, ... }。"""
    return isinstance(payload, dict) and "success" in payload


def data_of(payload: Dict[str, Any]) -> Any:
    return (payload or {}).get("data")


def txn_of(payload: Dict[str, Any]) -> Dict[str, Any]:
    """从创建/更新流水的响应里取出流水实体。

    ``POST/PATCH /api/transactions`` 返回的是
    ``{transaction, duplicate, analysis}`` —— 不是扁平的流水对象。
    ``duplicate`` 是重复检测结果（可能为 None），``analysis`` 是异常检测结果。
    把实体单独拎出来，避免每个用例都写一遍 ``data["transaction"]``。
    """
    data = data_of(payload) or {}
    if isinstance(data, dict) and "transaction" in data:
        return data.get("transaction") or {}
    return data if isinstance(data, dict) else {}


# ============================================================ 数据快照 / 回收

# 与 firefly/db/models.py 的 table=True 一一对应。
# 顺序是「先删子表再删主表」，避免将来加外键约束时删不动。
# 各表的主键列名。绝大多数是 id，键值表 ff_system_state 例外。
PRIMARY_KEYS: Dict[str, str] = {"ff_system_state": "key"}

TABLE_ORDER: List[str] = [
    "ff_transaction",
    "ff_tax_check",
    "ff_suggestion",
    "ff_audit_log",
    "ff_import_batch",
    "ff_budget",
    "ff_subscription",
    "ff_report",
    "ff_category",
    "ff_account",
    "ff_policy_doc",
    "ff_system_state",
    "ff_user",
]

# 「按标记兜底清扫」的 (表, 可能含测试前缀的列) 清单。
# 用途见 restore()：即便某个用例漏登记主键，也能靠 __测试 前缀把残留捞回来。
# 只列那些测试真的会写入 __测试 前缀的表；ff_user 等不参与，避免误删。
TEST_MARKERS: List[Tuple[str, str]] = [
    ("ff_transaction", "counterparty"),
    ("ff_transaction", "memo"),
    ("ff_category", "name"),
    ("ff_subscription", "name"),
    ("ff_account", "name"),
    ("ff_report", "title"),
    ("ff_import_batch", "filename"),
]


@dataclass
class Snapshot:
    """数据库主键快照，用于精确回收测试产生的数据。"""

    existing: Dict[str, set] = field(default_factory=dict)
    created: Dict[str, List[int]] = field(default_factory=dict)
    tracked: bool = False

    def note(self, table: str, pk: Any) -> None:
        """登记一个由本进程新建的主键（ff_system_state 的键是字符串）。"""
        if not self.tracked or table not in self.existing:
            return
        if pk is None or pk in self.existing[table]:
            return
        bucket = self.created.setdefault(table, [])
        if pk not in bucket:
            bucket.append(pk)

    @property
    def new_count(self) -> int:
        return sum(len(v) for v in self.created.values())


# ============================================================ 主测试类


class ApiTester:
    """封装 HTTP 客户端与所有用例。"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        use_llm: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.use_llm = use_llm
        self.timeout = timeout
        self.runner = Runner()
        self.token: Optional[str] = None
        self.snapshot = Snapshot()
        # 保存原始口令，用于最后把改口令用例造成的影响还原
        self._original_password = password
        self._dirty_fixtures: List[Dict[str, Any]] = []

    # -------------------------------------------------- HTTP 封装

    async def req(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Any = None,
        data: Any = None,
        files: Any = None,
        auth: bool = True,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with httpx.AsyncClient(
            base_url=self.base, timeout=self.timeout, follow_redirects=follow_redirects
        ) as client:
            return await client.request(
                method,
                path,
                headers=headers,
                json=json_body,
                params=params,
                data=data,
                files=files,
            )

    async def get(self, path: str, **kw) -> httpx.Response:
        return await self.req("GET", path, **kw)

    async def post(self, path: str, **kw) -> httpx.Response:
        return await self.req("POST", path, **kw)

    async def put(self, path: str, **kw) -> httpx.Response:
        return await self.req("PUT", path, **kw)

    async def patch(self, path: str, **kw) -> httpx.Response:
        return await self.req("PATCH", path, **kw)

    async def delete(self, path: str, **kw) -> httpx.Response:
        return await self.req("DELETE", path, **kw)

    # -------------------------------------------------- 数据快照

    async def take_snapshot(self) -> Dict[str, int]:
        """读取各表已有主键，作为回收基准。

        ``ff_system_state`` 的主键是 ``key`` 而不是 ``id``（它是键值表），
        对所有表一律 ``SELECT id`` 会在这里抛
        ``no such column: id`` 并**中断整个快照流程** ——
        快照一旦失败就没有任何基准，后续回收全部失效，
        测试数据便一次次堆积，进而让「重复导入应被跳过」这类用例
        因为撞上上次残留而误判。所以按表取主键列名，不写死 ``id``。
        """
        from firefly.db.session import session_scope
        from sqlalchemy import text

        counts: Dict[str, int] = {}
        async with session_scope() as session:
            for table in TABLE_ORDER:
                pk_col = PRIMARY_KEYS.get(table, "id")
                rows = (
                    await session.execute(text(f"SELECT {pk_col} FROM {table}"))
                ).all()
                self.snapshot.existing[table] = {r[0] for r in rows}
                counts[table] = len(self.snapshot.existing[table])
        return counts

    async def restore(self) -> Dict[str, int]:
        """删除测试新增的行，并把改口令用例还原。"""
        from firefly.db.session import session_scope
        from sqlalchemy import text

        removed: Dict[str, int] = {}

        # 先把上次运行残留的测试数据清掉（用可识别的标记），
        # 再删除本进程记录的增量。两步结合可覆盖「上次跑崩了」的情况。
        #
        # 标记清扫这一步是**必须的**，不能只依赖 self.snapshot.created：
        # 登记是调用方在每个用例里手工 note 的，只要有一处漏写（或进程
        # 中途崩溃没走到这里），那条数据就永远留在库里。历史事故就是：
        # 某次导入用例只登记了断言要用的那一条，同批其余流水全部泄漏，
        # 最终表现为总览与报告页面凭空多出一笔 1299 支出。
        #
        # 因此这里按「测试专用前缀」兜底清扫。前缀统一用 __测试 开头，
        # 业务数据不会长这样，误删风险可忽略。
        async with session_scope() as session:
            for table, column in TEST_MARKERS:
                result = await session.execute(
                    text(f"DELETE FROM {table} WHERE {column} LIKE :marker"),
                    {"marker": "%__测试%"},
                )
                hit = result.rowcount or 0
                if hit:
                    key = f"__swept__{table}"
                    removed[key] = removed.get(key, 0) + hit

        async with session_scope() as session:
            for table in TABLE_ORDER:
                pks = self.snapshot.created.get(table) or []
                if not pks:
                    continue
                pk_col = PRIMARY_KEYS.get(table, "id")
                placeholders = ",".join(f":p{i}" for i in range(len(pks)))
                values = {f"p{i}": v for i, v in enumerate(pks)}
                result = await session.execute(
                    text(f"DELETE FROM {table} WHERE {pk_col} IN ({placeholders})"),
                    values,
                )
                removed[table] = result.rowcount or 0

        # 还原口令（若测试过程中改过）
        try:
            from firefly.services import auth as auth_service
            from firefly.config import get_config, load_config

            cfg = get_config()
            if not auth_service.verify_password(
                self._original_password, cfg.auth.password_salt, cfg.auth.password_hash
            ):
                auth_service.reset_password(self._original_password)
                removed["__password_restored__"] = 1
        except Exception:  # noqa: BLE001
            pass

        return removed

    # -------------------------------------------------- 登录

    async def login(self) -> Tuple[bool, str]:
        resp = await self.post(
            "/api/auth/login",
            json_body={"username": self.username, "password": self.password},
            auth=False,
        )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        payload = resp.json()
        if not payload.get("success"):
            return False, payload.get("message", "登录失败")
        self.token = data_of(payload).get("token")
        return bool(self.token), "已获取令牌"

    # ================================================== 用例分组

    async def group_public(self) -> None:
        """免鉴权接口：健康检查、引导信息、鉴权状态。"""
        r = self.runner
        r.group("一、免鉴权接口")

        async def _health():
            resp = await self.get("/api/health", auth=False)
            body = resp.json()
            ok = (
                resp.status_code == 200
                and body.get("success") is True
                and data_of(body).get("status") == "ok"
            )
            return expect(ok, f"HTTP {resp.status_code} body={body}")

        await r.case("GET /api/health 返回 ok", _health())

        async def _bootstrap():
            resp = await self.get("/api/bootstrap", auth=False)
            body = resp.json()
            d = data_of(body) or {}
            # 安全约束：引导接口绝不能泄漏用户、税率、推送目标等业务信息
            leaked = [k for k in ("user", "tax", "push", "llm") if k in d]
            ok = (
                resp.status_code == 200
                and body.get("success") is True
                and "debug" in d
                and not leaked
            )
            return expect(ok, f"leaked={leaked} keys={list(d)}")

        await r.case("GET /api/bootstrap 免鉴权且不泄漏业务字段", _bootstrap())

        async def _auth_status():
            resp = await self.get("/api/auth/status", auth=False)
            d = data_of(resp.json()) or {}
            ok = resp.status_code == 200 and "enabled" in d and "username" in d
            # 口令/哈希/盐一律不得出现
            # 这些是**策略名**（口令强度要求），不是口令/哈希/盐本身，属可公开项
            policy_keys = {"using_default_password", "require_strong_password",
                           "min_password_length"}
            bad = [k for k in d if "password" in k.lower() and k not in policy_keys]
            return expect(ok and not bad, f"敏感键={bad}")

        await r.case("GET /api/auth/status 不泄漏口令字段", _auth_status())

        async def _docs():
            resp = await self.get("/docs", auth=False)
            return expect(resp.status_code == 200, f"HTTP {resp.status_code}")

        await r.case("GET /docs 接口文档可访问", _docs())

        async def _openapi():
            resp = await self.get("/api/openapi.json", auth=False)
            spec = resp.json()
            n = len(spec.get("paths", {}))
            return expect(resp.status_code == 200 and n > 40, f"路径数={n}")

        await r.case("GET /api/openapi.json 可解析且包含全部路由", _openapi())

    async def group_guard(self) -> None:
        """鉴权守卫：未登录访问受保护接口必须 401。"""
        r = self.runner
        r.group("二、鉴权守卫")

        saved, self.token = self.token, None
        protected = [
            "/api/meta",
            "/api/transactions",
            "/api/categories",
            "/api/stats",
            "/api/reports",
            "/api/budgets",
            "/api/settings",
            "/api/audit/logs",
            "/api/tax-check/checks",
            "/api/subscriptions",
        ]

        async def _blocked():
            bad = []
            for path in protected:
                resp = await self.get(path, auth=False)
                if resp.status_code != 401:
                    bad.append(f"{path}→{resp.status_code}")
            return expect(not bad, f"未拦截: {bad}")

        await r.case("未登录访问 10 个受保护接口全部 401", _blocked())

        async def _forged():
            async with httpx.AsyncClient(base_url=self.base, timeout=self.timeout) as c:
                resp = await c.get(
                    "/api/meta", headers={"Authorization": "Bearer fake.token.value"}
                )
            return expect(resp.status_code == 401, f"HTTP {resp.status_code}")

        await r.case("伪造令牌被拒绝", _forged())

        async def _wrong_pwd():
            resp = await self.post(
                "/api/auth/login",
                json_body={"username": self.username, "password": "definitely-wrong-pwd"},
                auth=False,
            )
            body = resp.json()
            return expect(
                resp.status_code == 200 and body.get("success") is False,
                f"HTTP {resp.status_code} success={body.get('success')}",
            )

        await r.case("错误口令登录失败（返回 200 + success:false）", _wrong_pwd())

        async def _enum():
            """防账号枚举：不存在的用户名与错误口令必须返回同一文案。

            两个坑：
            1. 锁定是**按 (用户名, IP)** 计的，前面几个用例已经在真实用户名上
               累计了失败次数，再来几次就会触发锁定，此时返回的是
               「失败次数过多」而非「用户名或密码错误」—— 这不是枚举防护失效。
               所以先用两个**都不存在**的用户名对比，锁定的只会是那两个虚名。
            2. 对比必须同一次交互内完成，避免中间被其它失败请求插队。
            """
            a = await self.post(
                "/api/auth/login",
                json_body={"username": "no_such_user_aaa", "password": "x-wrong"},
                auth=False,
            )
            b = await self.post(
                "/api/auth/login",
                json_body={"username": "no_such_user_zzz", "password": "x-wrong"},
                auth=False,
            )
            ma, mb = a.json().get("message"), b.json().get("message")
            return expect(ma == mb, f"用户A='{ma}' vs 用户B='{mb}'")

        await r.case("防账号枚举：两种失败文案一致", _enum())

        self.token = saved

    async def group_auth(self) -> None:
        """登录态下的身份与口令接口。"""
        r = self.runner
        r.group("三、登录与身份")

        async def _login():
            return (True, "已在初始化阶段登录成功") if self.token else (False, "无令牌")

        await r.case("POST /api/auth/login 获取令牌", _login())

        async def _me():
            resp = await self.get("/api/auth/me")
            d = data_of(resp.json()) or {}
            ok = resp.status_code == 200 and d.get("username") == self.username
            return expect(ok, f"HTTP {resp.status_code} d={d}")

        await r.case("GET /api/auth/me 返回当前用户", _me())

        async def _cookie_login():
            """Cookie 方式也应可用（前端实际走这条路径）。"""
            async with httpx.AsyncClient(base_url=self.base, timeout=self.timeout) as c:
                resp = await c.post(
                    "/api/auth/login",
                    json={"username": self.username, "password": self.password},
                )
                if resp.status_code != 200:
                    return expect(False, f"登录 HTTP {resp.status_code}")
                me = await c.get("/api/auth/me")
            return expect(me.status_code == 200, f"/api/auth/me → {me.status_code}")

        await r.case("Cookie 会话可访问受保护接口", _cookie_login())

    async def group_meta(self) -> None:
        r = self.runner
        r.group("四、元信息与配置")

        async def _meta():
            resp = await self.get("/api/meta")
            d = data_of(resp.json()) or {}
            need = ["app_name", "version", "port", "currency_symbol", "llm", "ui"]
            missing = [k for k in need if k not in d]
            ok = resp.status_code == 200 and not missing
            # 令牌/密钥不得出现在元信息里
            raw = json.dumps(d, ensure_ascii=False)
            leak = "secret_key" in raw or (d.get("llm", {}).get("api_key") not in (None, ""))
            return expect(ok and not leak, f"缺失={missing} 泄漏={leak}")

        await r.case("GET /api/meta 字段完整且无密钥", _meta())

        async def _settings():
            resp = await self.get("/api/settings")
            d = data_of(resp.json()) or {}
            auth_cfg = d.get("auth", {})
            masked = all(
                auth_cfg.get(k) == "******"
                for k in ("password", "password_hash", "password_salt", "secret_key")
            )
            raw = json.dumps(d, ensure_ascii=False)
            # 真实密钥绝不能在响应正文里出现
            from firefly.config import get_config, load_config

            cfg = get_config()
            hard = cfg.auth.secret_key and cfg.auth.secret_key in raw
            return expect(masked and not hard, f"脱敏={masked} 泄漏真实key={hard}")

        await r.case("GET /api/settings 敏感字段全部脱敏", _settings())

        async def _schema():
            resp = await self.get("/api/settings/schema")
            d = data_of(resp.json()) or {}
            ok = "editable" in d and isinstance(d["editable"], (list, dict))
            return expect(ok, f"keys={list(d)}")

        await r.case("GET /api/settings/schema 返回可编辑字段", _schema())

        # ---- 安全回归：系统托管字段不可经 API 篡改 ----
        async def _protect_secret():
            before = None
            from firefly.config import get_config, load_config

            before = get_config().auth.secret_key
            resp = await self.put(
                "/api/settings",
                json_body={"auth.secret_key": "forged-by-test", "auth.password_hash": "x"},
            )
            body = resp.json()
            rejected = set((data_of(body) or {}).get("rejected") or [])
            after = load_config(reload=True).auth.secret_key
            ok = (
                "auth.secret_key" in rejected
                and "auth.password_hash" in rejected
                and after == before
            )
            return expect(
                ok, f"rejected={sorted(rejected)} secret_key被改={after != before}"
            )

        await r.case("系统托管字段（secret_key/口令哈希）拒绝经 API 写入", _protect_secret())

        async def _protect_nested():
            """嵌套写法也必须被拦住（历史漏洞所在）。"""
            from firefly.config import get_config, load_config

            before = get_config().auth.secret_key
            resp = await self.put(
                "/api/settings",
                json_body={"auth": {"secret_key": "forged-nested", "password_hash": "y"}},
            )
            rejected = set((data_of(resp.json()) or {}).get("rejected") or [])
            after = load_config(reload=True).auth.secret_key
            return expect(
                "auth.secret_key" in rejected and after == before,
                f"rejected={sorted(rejected)} 被改={after != before}",
            )

        await r.case("嵌套写法同样被拦截（历史漏洞回归）", _protect_nested())

        async def _editable_roundtrip():
            """可编辑字段应能真实写入并读回。"""
            orig = data_of((await self.get("/api/settings")).json())["ui"]
            old = orig.get("counter_duration", 1200)
            new = 777 if old != 777 else 888
            try:
                resp = await self.put(
                    "/api/settings", json_body={"ui.counter_duration": new}
                )
                updated = set((data_of(resp.json()) or {}).get("updated") or [])
                readback = data_of((await self.get("/api/settings")).json())["ui"][
                    "counter_duration"
                ]
                return expect(
                    "ui.counter_duration" in updated and readback == new,
                    f"updated={sorted(updated)} 读回={readback}",
                )
            finally:
                await self.put("/api/settings", json_body={"ui.counter_duration": old})

        await r.case("可编辑字段 PUT 后能读回（并还原）", _editable_roundtrip())

    async def group_category(self) -> None:
        r = self.runner
        r.group("五、类别管理")

        async def _list():
            resp = await self.get("/api/categories")
            d = data_of(resp.json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            # 全新部署会自动播种 18 个默认类别
            ok = resp.status_code == 200 and len(items) >= 18
            return expect(ok, f"类别数={len(items)}")

        await r.case("GET /api/categories 至少 18 个默认类别", _list())

        async def _tax_fields():
            """每个默认类别都应带税务口径，税检才能直接 join。"""
            d = data_of((await self.get("/api/categories")).json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            with_tax = [c for c in items if c.get("tax_account")]
            return expect(
                len(with_tax) >= 8,
                f"带税务口径的类别 {len(with_tax)}/{len(items)}",
            )

        await r.case("默认类别携带 tax_account 税务口径", _tax_fields())

        async def _create_delete():
            name = f"__测试类别_{int(time.time())}"
            resp = await self.post(
                "/api/categories",
                json_body={"name": name, "direction": "expense", "deductible": True},
            )
            assert resp.status_code == 200, f"创建 HTTP {resp.status_code} {resp.text[:200]}"
            cid = data_of(resp.json()).get("id")
            self.snapshot.note("ff_category", cid)

            lst = data_of((await self.get("/api/categories")).json())
            items = lst if isinstance(lst, list) else (lst or {}).get("items", [])
            found = any(c.get("id") == cid for c in items)

            d = await self.delete(f"/api/categories/{cid}")
            return expect(
                cid and found and d.status_code == 200,
                f"id={cid} 出现在列表={found} 删除={d.status_code}",
            )

        await r.case("POST/DELETE /api/categories 增删闭环", _create_delete())

        async def _delete_system():
            """系统类别不允许删除，否则会破坏历史流水归类。"""
            d = data_of((await self.get("/api/categories")).json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            syscat = next((c for c in items if c.get("is_system")), None)
            if not syscat:
                return expect(False, "未找到系统类别")
            resp = await self.delete(f"/api/categories/{syscat['id']}")
            body = resp.json()
            return expect(
                body.get("success") is False or resp.status_code >= 400,
                f"HTTP {resp.status_code} success={body.get('success')}",
            )

        await r.case("系统类别拒绝删除", _delete_system())

    async def group_transaction(self) -> None:
        r = self.runner
        r.group("六、流水 CRUD")

        created: List[int] = []

        async def _create():
            resp = await self.post(
                "/api/transactions",
                json_body={
                    "amount": 88.8,
                    "direction": "expense",
                    "memo": "__测试流水_奶茶",
                    "counterparty": "测试商户",
                    "trade_time": "2026-10-12 12:30",
                },
            )
            assert resp.status_code == 200, f"HTTP {resp.status_code} {resp.text[:300]}"
            d = txn_of(resp.json())
            tid = d.get("id")
            created.append(tid)
            self.snapshot.note("ff_transaction", tid)
            return expect(bool(tid) and d.get("amount") == 88.8, f"id={tid} d={d}")

        await r.case("POST /api/transactions 创建流水", _create())

        async def _amount_positive():
            """金额必须为正数，方向另存 —— 负数必须被拒。"""
            resp = await self.post(
                "/api/transactions",
                json_body={"amount": -50, "direction": "expense", "memo": "负数测试"},
            )
            return expect(resp.status_code == 422, f"HTTP {resp.status_code}")

        await r.case("金额为负被拒（422）", _amount_positive())

        async def _zero():
            resp = await self.post(
                "/api/transactions", json_body={"amount": 0, "memo": "零"}
            )
            return expect(resp.status_code == 422, f"HTTP {resp.status_code}")

        await r.case("金额为 0 被拒（422）", _zero())

        async def _get():
            if not created:
                return expect(False, "前置创建失败")
            resp = await self.get(f"/api/transactions/{created[0]}")
            d = data_of(resp.json()) or {}
            return expect(
                resp.status_code == 200 and d.get("id") == created[0],
                f"HTTP {resp.status_code}",
            )

        await r.case("GET /api/transactions/{id} 读取单条", _get())

        async def _patch():
            if not created:
                return expect(False, "前置创建失败")
            resp = await self.patch(
                f"/api/transactions/{created[0]}",
                json_body={"amount": 99.9, "memo": "__测试流水_已改"},
            )
            d = data_of(resp.json()) or {}
            return expect(
                resp.status_code == 200 and d.get("amount") == 99.9,
                f"HTTP {resp.status_code} amount={d.get('amount')}",
            )

        await r.case("PATCH /api/transactions/{id} 更新", _patch())

        async def _confirm():
            if not created:
                return expect(False, "前置创建失败")
            resp = await self.post(f"/api/transactions/{created[0]}/confirm")
            d = data_of(resp.json()) or {}
            return expect(
                resp.status_code == 200 and d.get("is_confirmed") is True,
                f"HTTP {resp.status_code} confirmed={d.get('is_confirmed')}",
            )

        await r.case("POST /api/transactions/{id}/confirm 确认", _confirm())

        async def _list_filters():
            """过滤条件必须与列表一致，否则分页会算出错误页数。"""
            results = {}
            for q, path in [
                ("direction", "/api/transactions?direction=income"),
                ("keyword", "/api/transactions?keyword=__测试"),
                ("limit", "/api/transactions?limit=2"),
                ("only_pending", "/api/transactions?only_pending=true"),
            ]:
                resp = await self.get(path)
                results[q] = resp.status_code
            ok = all(v == 200 for v in results.values())
            return expect(ok, str(results))

        await r.case("GET /api/transactions 过滤参数可用", _list_filters())

        async def _filters_effective():
            """direction 过滤必须真的生效（不能只是不报错）。"""
            inc = data_of((await self.get("/api/transactions?direction=income")).json())
            exp = data_of((await self.get("/api/transactions?direction=expense")).json())
            bad = [t for t in (inc or []) if t.get("direction") != "income"]
            bad += [t for t in (exp or []) if t.get("direction") != "expense"]
            return expect(not bad, f"串味的记录 {len(bad)} 条")

        await r.case("direction 过滤真实生效", _filters_effective())

        async def _bad_limit():
            resp = await self.get("/api/transactions?limit=99999")
            return expect(resp.status_code == 422, f"HTTP {resp.status_code}")

        await r.case("limit 越界被拒（422）", _bad_limit())

        async def _not_found():
            resp = await self.get("/api/transactions/99999999")
            body = resp.json()
            ok = resp.status_code == 404 or body.get("success") is False
            return expect(ok, f"HTTP {resp.status_code} success={body.get('success')}")

        await r.case("不存在的流水返回 404 / success:false", _not_found())

        async def _dup():
            """相同金额 + 相近时间 + 同对手方应被标记为疑似重复。"""
            body = {
                "amount": 66.66,
                "direction": "expense",
                "memo": "__测试重复",
                "counterparty": "重复测试商户",
                "trade_time": "2026-10-12 15:00",
            }
            a = await self.post("/api/transactions", json_body=body)
            b = await self.post("/api/transactions", json_body=body)
            ida = txn_of(a.json()).get("id")
            idb = txn_of(b.json()).get("id")
            self.snapshot.note("ff_transaction", ida)
            self.snapshot.note("ff_transaction", idb)
            rb = data_of((await self.get(f"/api/transactions/{idb}")).json()) or {}
            return expect(
                rb.get("is_duplicate") is True,
                f"第二条 is_duplicate={rb.get('is_duplicate')}",
            )

        await r.case("重复记账被标记 is_duplicate", _dup())

        async def _delete():
            ok_all = True
            detail = []
            for tid in created:
                resp = await self.delete(f"/api/transactions/{tid}")
                detail.append(f"{tid}→{resp.status_code}")
                if resp.status_code != 200:
                    ok_all = False
            return expect(ok_all, " ".join(detail))

        await r.case("DELETE /api/transactions/{id} 删除", _delete())

    async def group_stats(self) -> None:
        r = self.runner
        r.group("七、统计与看板")

        # 先造一笔可预测的收入与支出，便于核对口径
        seed_ids: List[int] = []

        async def _seed():
            inc = await self.post(
                "/api/transactions",
                json_body={
                    "amount": 1000,
                    "direction": "income",
                    "memo": "__统计种子_收入",
                    "trade_time": "2026-10-05 10:00",
                },
            )
            exp = await self.post(
                "/api/transactions",
                json_body={
                    "amount": 400,
                    "direction": "expense",
                    "memo": "__统计种子_支出",
                    "trade_time": "2026-10-06 10:00",
                },
            )
            for resp in (inc, exp):
                tid = txn_of(resp.json()).get("id")
                seed_ids.append(tid)
                self.snapshot.note("ff_transaction", tid)
            return expect(len(seed_ids) == 2, f"seed={seed_ids}")

        await r.case("准备统计种子数据", _seed())

        async def _stats():
            resp = await self.get("/api/stats?period=monthly")
            d = data_of(resp.json()) or {}
            need = ["summary", "by_category", "by_day", "trend", "accounts"]
            missing = [k for k in need if k not in d]
            s = d.get("summary", {})
            ok = (
                resp.status_code == 200
                and not missing
                and "income" in s
                and "expense" in s
                and "net" in s
            )
            return expect(ok, f"缺失={missing} summary键={list(s)}")

        await r.case("GET /api/stats 结构完整", _stats())

        async def _net_math():
            """结余 = 收入 - 支出，这个恒等式必须成立。"""
            d = data_of((await self.get("/api/stats?period=monthly")).json()) or {}
            s = d.get("summary", {})
            inc, exp, net = s.get("income", 0), s.get("expense", 0), s.get("net", 0)
            return expect(
                abs((inc - exp) - net) < 0.01,
                f"{inc} - {exp} = {round(inc-exp,2)} vs net={net}",
            )

        await r.case("恒等式 net = income - expense 成立", _net_math())

        async def _compare():
            d = data_of((await self.get("/api/stats?period=monthly")).json()) or {}
            c = d.get("compare") or {}
            ok = all(k in c for k in ("income", "expense", "net"))
            has_dir = all(
                "direction" in (c.get(k) or {}) for k in ("income", "expense", "net")
            )
            return expect(ok and has_dir, f"compare键={list(c)}")

        await r.case("环比数据 compare 含 direction", _compare())

        async def _anomalies():
            for mode in ("all", "anomaly", "personal"):
                resp = await self.get(f"/api/anomalies?mode={mode}")
                if resp.status_code != 200:
                    return expect(False, f"mode={mode} HTTP {resp.status_code}")
            return expect(True, "三种 mode 均 200")

        await r.case("GET /api/anomalies 三种模式可用", _anomalies())

        async def _bad_mode():
            """非法 mode 不应 500（优雅降级或 422 均可接受）。"""
            resp = await self.get("/api/anomalies?mode=not-a-real-mode")
            return expect(
                resp.status_code < 500, f"HTTP {resp.status_code}（500 属缺陷）"
            )

        await r.case("非法 mode 不触发 500", _bad_mode())

        async def _budget_status():
            resp = await self.get("/api/budget-status")
            d = data_of(resp.json()) or {}
            ok = resp.status_code == 200 and "items" in d
            return expect(ok, f"keys={list(d)}")

        await r.case("GET /api/budget-status 可用", _budget_status())

        async def _periods():
            """所有受支持的周期都必须能算出统计。"""
            bad = []
            for p in ("daily", "weekly", "monthly", "quarterly", "yearly", "custom"):
                resp = await self.get(f"/api/stats?period={p}&custom_days=30")
                if resp.status_code != 200 or resp.json().get("success") is not True:
                    bad.append(f"{p}→{resp.status_code}")
            return expect(not bad, f"失败周期: {bad}")

        await r.case("六种周期均可统计（含 custom）", _periods())

        # 清理种子
        async def _cleanup():
            for tid in seed_ids:
                await self.delete(f"/api/transactions/{tid}")
            return expect(True, f"已清理 {len(seed_ids)} 条种子")

        await r.case("清理统计种子", _cleanup())

    async def group_budget(self) -> None:
        r = self.runner
        r.group("八、预算管理")

        async def _create():
            resp = await self.post(
                "/api/budgets",
                json_body={"amount": 5000, "period": "monthly", "alert_enabled": True},
            )
            assert resp.status_code == 200, f"HTTP {resp.status_code} {resp.text[:200]}"
            bid = data_of(resp.json()).get("id")
            self.snapshot.note("ff_budget", bid)
            return expect(bool(bid), f"id={bid}")

        await r.case("POST /api/budgets 创建总预算", _create())

        async def _list():
            """``GET /api/budgets`` 的 ``data`` 是**列表**，``summary`` 在**顶层**。

            即 ``{success, data:[...], summary:{...}}`` —— 不是把两者都塞进 data。
            这里按真实契约断言，避免「看着通过、实际字段根本不存在」。
            """
            resp = await self.get("/api/budgets")
            body = resp.json()
            d = data_of(body)
            ok = isinstance(d, list) and isinstance(body.get("summary"), dict)
            return expect(ok, f"data={type(d).__name__}, summary={'summary' in body}")

        await r.case("GET /api/budgets 返回列表 + 顶层 summary", _list())

        async def _status_shape():
            d = data_of((await self.get("/api/budget-status")).json()) or {}
            need = ["total_budget", "total_actual", "total_usage_ratio", "items"]
            missing = [k for k in need if k not in d]
            return expect(not missing, f"缺失={missing}")

        await r.case("预算状态含执行率字段", _status_shape())

        async def _over_budget():
            """造一个必然超支的类别预算，state 应为 over。"""
            cats = data_of((await self.get("/api/categories")).json())
            items = cats if isinstance(cats, list) else (cats or {}).get("items", [])
            cat = next((c for c in items if c.get("direction") == "expense"), None)
            if not cat:
                return expect(False, "无可用支出类别")

            # 预算 1 元 + 一笔 500 元支出 → 必然 over
            b = await self.post(
                "/api/budgets",
                json_body={
                    "category_id": str(cat["id"]),
                    "amount": 1,
                    "period": "monthly",
                },
            )
            bid = data_of(b.json()).get("id")
            self.snapshot.note("ff_budget", bid)

            t = await self.post(
                "/api/transactions",
                json_body={
                    "amount": 500,
                    "direction": "expense",
                    "category_id": cat["id"],
                    "memo": "__预算超支测试",
                    "trade_time": "2026-10-07 10:00",
                },
            )
            tid = txn_of(t.json()).get("id")
            self.snapshot.note("ff_transaction", tid)

            st = data_of((await self.get("/api/budget-status")).json()) or {}
            hit = next(
                (i for i in st.get("items", []) if i.get("category_id") == cat["id"]), None
            )
            state = (hit or {}).get("state")
            ok = state == "over"

            # 清理
            await self.delete(f"/api/budgets/{bid}")
            await self.delete(f"/api/transactions/{tid}")
            return expect(ok, f"类别={cat['name']} state={state}（期望 over）")

        await r.case("超支类别 state 判定为 over", _over_budget())

        async def _delete():
            # data 是列表，直接迭代；不要写成 data["items"]（那是报告类接口的形状）
            lst = data_of((await self.get("/api/budgets")).json()) or []
            if not isinstance(lst, list):
                lst = []
            removed, errs = 0, []
            for item in lst:
                bid = item.get("budget_id") or item.get("id")
                if not bid:
                    continue
                resp = await self.delete(f"/api/budgets/{bid}")
                if resp.status_code == 200:
                    removed += 1
                else:
                    errs.append(f"{bid}→{resp.status_code}")
            return expect(not errs, f"已删 {removed} 个 错误={errs}")

        await r.case("DELETE /api/budgets/{id} 清理全部测试预算", _delete())

        async def _neg_amount():
            resp = await self.post(
                "/api/budgets", json_body={"amount": -100, "period": "monthly"}
            )
            body = resp.json()
            # 后端对**业务规则**错误统一用 HTTP 200 + success:false + code 表达，
            # 只有 pydantic 字段校验失败才是 422。
            # 断言只认「被拒」这一实质：success 为 False 且带明确文案。
            ok = body.get("success") is False and bool(body.get("message"))
            return expect(ok, f"HTTP {resp.status_code} msg={body.get('message')}")

        await r.case("预算金额为负被拒（success:false）", _neg_amount())

    async def group_subscription(self) -> None:
        r = self.runner
        r.group("九、订阅与周期记账")

        async def _create():
            resp = await self.post(
                "/api/subscriptions",
                json_body={
                    "name": "__测试订阅_房租",
                    "amount": 3000,
                    "period": "monthly",
                    "day_of_period": 1,
                    "start_date": "2026-10-01",
                },
            )
            assert resp.status_code == 200, f"HTTP {resp.status_code} {resp.text[:200]}"
            sid = data_of(resp.json()).get("id")
            self.snapshot.note("ff_subscription", sid)
            return expect(bool(sid), f"id={sid}")

        await r.case("POST /api/subscriptions 创建订阅", _create())

        async def _list():
            resp = await self.get("/api/subscriptions")
            d = data_of(resp.json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            have = any(s.get("name") == "__测试订阅_房租" for s in items)
            return expect(resp.status_code == 200 and have, f"共 {len(items)} 条")

        await r.case("GET /api/subscriptions 能查到新建订阅", _list())

        async def _status_get():
            resp = await self.get("/api/subscription/status")
            d = data_of(resp.json()) or {}
            return expect(resp.status_code == 200 and d, f"keys={list(d)}")

        await r.case("GET /api/subscription/status 可读", _status_get())

        async def _status_put():
            """开关写回 config 后必须能读回原值（避免误改用户配置）。

            注意读写字段名**不对称**：
              PUT 收的是 ``enabled``，写的是 ``push.enabled``；
              GET 返回的是 ``push_enabled``。
            照名字猜 ``after["enabled"]`` 会永远拿到 None。
            """
            before = data_of((await self.get("/api/subscription/status")).json()) or {}
            old = before.get("push_enabled")
            if not isinstance(old, bool):
                return expect(False, f"读不到 push_enabled，keys={list(before)}")
            try:
                resp = await self.put(
                    "/api/subscription/status", json_body={"enabled": not old}
                )
                d = data_of(resp.json()) or {}
                updated = set(d.get("updated") or [])
                after = data_of((await self.get("/api/subscription/status")).json()) or {}
                ok = (
                    resp.status_code == 200
                    and "push.enabled" in updated
                    and after.get("push_enabled") is (not old)
                )
            finally:
                await self.put("/api/subscription/status", json_body={"enabled": old})
            return expect(ok, f"原值={old} 新值={after.get('push_enabled')}")

        await r.case("PUT /api/subscription/status 可写且能还原", _status_put())

        async def _delete():
            sid = None
            d = data_of((await self.get("/api/subscriptions")).json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            for s in items:
                if s.get("name") == "__测试订阅_房租":
                    sid = s.get("id")
                    break
            if not sid:
                return expect(False, "未找到测试订阅")
            resp = await self.delete(f"/api/subscriptions/{sid}")
            return expect(resp.status_code == 200, f"HTTP {resp.status_code}")

        await r.case("DELETE /api/subscriptions/{id} 删除", _delete())

    async def group_report(self) -> None:
        r = self.runner
        r.group("十、报告生成")

        report_id: Optional[str] = None

        async def _generate():
            nonlocal report_id
            t0 = time.perf_counter()
            resp = await self.post(
                "/api/reports/generate", json_body={"period": "monthly"}
            )
            elapsed = (time.perf_counter() - t0) * 1000
            body = resp.json()
            if not body.get("success"):
                return expect(False, f"生成失败：{body.get('message')}")
            d = data_of(body) or {}
            report_id = d.get("id")
            self.snapshot.note("ff_report", report_id)
            payload = d.get("payload") or {}
            modules = sorted(payload.keys())
            need = {"meta", "overview", "structure", "tax", "optimization", "highlights"}
            missing = need - set(modules)
            return expect(
                not missing,
                f"id={report_id} elapsed={elapsed:.0f}ms 缺失模块={missing}",
            )

        await r.case("POST /api/reports/generate 生成成功", _generate())

        async def _modules():
            """payload 是前端与出图共用的唯一数据源，模块必须齐全。"""
            if not report_id:
                return expect(False, "前置生成失败")
            d = data_of((await self.get(f"/api/reports/{report_id}")).json()) or {}
            payload = d.get("payload") or {}
            need = {
                "meta",
                "overview",
                "structure",
                "trend",
                "attribution",
                "tax",
                "optimization",
                "budget",
                "anomaly",
                "highlights",
                "accounts",
            }
            missing = need - set(payload.keys())
            return expect(not missing, f"缺失模块={sorted(missing)}")

        await r.case("报告 payload 含 11 个核心模块", _modules())

        async def _overview_math():
            if not report_id:
                return expect(False, "前置生成失败")
            d = data_of((await self.get(f"/api/reports/{report_id}")).json()) or {}
            o = (d.get("payload") or {}).get("overview", {})
            inc, exp = o.get("income", 0), o.get("expense", 0)
            net = o.get("net")
            ok = net is not None and abs((inc - exp) - net) < 0.01
            return expect(ok, f"{inc} - {exp} = {round(inc-exp,2)} vs net={net}")

        await r.case("报告 overview 收支恒等式成立", _overview_math())

        async def _latest():
            resp = await self.get("/api/reports/latest")
            d = data_of(resp.json()) or {}
            ok = resp.status_code == 200 and (d.get("id") or d.get("payload"))
            return expect(ok, f"keys={list(d)[:6]}")

        await r.case("GET /api/reports/latest 可用", _latest())

        async def _archive():
            resp = await self.get("/api/reports?limit=5")
            d = data_of(resp.json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            # 归档列表应剔除大字段，避免列表页拖慢
            heavy = [i for i in items if "payload" in i or "summary_text" in i]
            return expect(not heavy, f"归档列表携带大字段 {len(heavy)} 条")

        await r.case("GET /api/reports 归档列表剔除大字段", _archive())

        async def _html():
            if not report_id:
                return expect(False, "前置生成失败")
            resp = await self.get(f"/api/reports/{report_id}/html")
            ok = resp.status_code == 200 and len(resp.text) > 5000
            return expect(ok, f"HTTP {resp.status_code} 长度={len(resp.text)}")

        await r.case("GET /api/reports/{id}/html 渲染 HTML", _html())

        async def _preview():
            if not report_id:
                return expect(False, "前置生成失败")
            resp = await self.get(f"/api/reports/{report_id}/preview")
            d = data_of(resp.json()) or {}
            ok = resp.status_code == 200 and ("url" in d or "html_path" in d)
            return expect(ok, f"keys={list(d)}")

        await r.case("GET /api/reports/{id}/preview 返回预览地址", _preview())

        async def _custom_period():
            resp = await self.post(
                "/api/reports/generate",
                json_body={"period": "custom", "custom_days": 14},
            )
            body = resp.json()
            if body.get("success"):
                self.snapshot.note("ff_report", data_of(body).get("id"))
            return expect(body.get("success") is True, f"message={body.get('message')}")

        await r.case("自定义周期（14 天）报告可生成", _custom_period())

        async def _bad_period():
            resp = await self.post(
                "/api/reports/generate", json_body={"period": "not-a-period"}
            )
            body = resp.json()
            ok = resp.status_code < 500 and (
                resp.status_code == 422 or body.get("success") is False
            )
            return expect(ok, f"HTTP {resp.status_code} success={body.get('success')}")

        await r.case("非法周期被拒且不 500", _bad_period())

    async def group_taxcheck(self) -> None:
        r = self.runner
        r.group("十一、税务自检")

        async def _run():
            resp = await self.post("/api/tax-check", json_body={"period": "monthly"})
            body = resp.json()
            if not body.get("success"):
                return expect(False, f"失败：{body.get('message')}")
            d = data_of(body) or {}
            checks = d.get("checks") or []
            # 8 项里有 6 项是**条件性**的：数据不足时返回 None 并被跳过
            # （例如没有任何支出类别时，结构集中度检查无从谈起）。
            # 所以不能断言「恒等于 8」，只断言：
            #   1) 两项必检项（增值税起征点、税负率）一定在；
            #   2) 每项结论都带风险等级与政策依据。
            keys = {c.get("check_key") for c in checks}
            mandatory = {"vat_threshold", "tax_burden"} <= keys
            shaped = all(
                c.get("title") and c.get("level") and "policy_basis" in c
                for c in checks
            )
            ok = mandatory and len(checks) >= 2 and shaped and "disclaimer" in d
            return expect(
                ok,
                f"检查项={len(checks)} keys={sorted(k for k in keys if k)} "
                f"必检={mandatory} 完整={shaped}",
            )

        await r.case("POST /api/tax-check 返回必检项且结论结构完整", _run())

        async def _policy_basis():
            """每条结论都要能追溯到政策依据，否则答辩无法解释口径。

            断言刻意写成「每条都有」而不是「至少 N 条有」：
            ``run_tax_check`` 里有 6 项是**条件性**检查（如无任何支出类别时
            结构集中度无从谈起），它们返回 None 被跳过，所以条数会随
            账号里的数据变化。写死条数会让这个用例在清理过数据后
            莫名失败 —— 而失败原因与「政策依据」这个被测行为无关。
            """
            d = data_of((await self.post("/api/tax-check", json_body={})).json()) or {}
            checks = d.get("checks") or []
            with_basis = [c for c in checks if c.get("policy_basis")]
            return expect(
                len(checks) > 0 and len(with_basis) == len(checks),
                f"带政策依据 {len(with_basis)}/{len(checks)}",
            )

        await r.case("税检结论携带 policy_basis 政策依据", _policy_basis())

        async def _disclaimer():
            d = data_of((await self.post("/api/tax-check", json_body={})).json()) or {}
            text = str(d.get("disclaimer") or "")
            return expect(
                "参考" in text or "不构成" in text,
                f"免责声明='{text[:60]}'",
            )

        await r.case("税检结果带免责声明", _disclaimer())

        async def _checks_list():
            resp = await self.get("/api/tax-check/checks?limit=10")
            d = data_of(resp.json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            return expect(resp.status_code == 200, f"返回 {len(items)} 条历史结论")

        await r.case("GET /api/tax-check/checks 历史列表", _checks_list())

        async def _policy_api():
            resp = await self.get("/api/tax-check/policy")
            d = data_of(resp.json()) or {}
            return expect(resp.status_code == 200 and len(d) >= 6, f"政策条目={len(d)}")

        await r.case("GET /api/tax-check/policy 政策依据字典", _policy_api())

    async def group_ai(self) -> None:
        r = self.runner
        r.group("十二、AI 能力")

        async def _status():
            resp = await self.get("/api/ai/status")
            d = data_of(resp.json()) or {}
            return expect(resp.status_code == 200 and "ready" in d, f"keys={list(d)}")

        await r.case("GET /api/ai/status 可用", _status())

        ai_ready = data_of((await self.get("/api/ai/status")).json() or {}).get("ready")

        if not self.use_llm:
            r.skip("POST /api/ai/natural 自然语言抽取", "--no-llm 已跳过")
        elif not ai_ready:
            r.warn("POST /api/ai/natural 自然语言抽取", "LLM 未就绪，走规则引擎")
            resp = await self.post(
                "/api/ai/natural", json_body={"text": "昨天打车38，请客户吃饭260"}
            )
            body = resp.json()
            n = len(data_of(body).get("items") or []) if body.get("success") else 0
            r.results[-1].status = PASS if n >= 2 else FAIL
            r.results[-1].detail = f"规则引擎抽取 {n} 条"
        else:
            async def _natural():
                resp = await self.post(
                    "/api/ai/natural",
                    json_body={"text": "昨天打车38，请客户吃饭260"},
                )
                body = resp.json()
                if not body.get("success"):
                    return expect(False, f"失败：{body.get('message')}")
                d = data_of(body) or {}
                items = d.get("items") or []
                ok = len(items) >= 2 and all(
                    i.get("amount") and i.get("direction") for i in items
                )
                return expect(ok, f"抽取 {len(items)} 条，引擎={d.get('engine')}")

            await r.case("POST /api/ai/natural 自然语言抽取", _natural())

        # 分类：即使 LLM 未就绪也应有规则兜底
        async def _classify():
            resp = await self.post(
                "/api/ai/classify", json_body={"memo": "打车费", "amount": 45.5}
            )
            body = resp.json()
            if resp.status_code >= 500:
                return expect(False, f"HTTP {resp.status_code} {resp.text[:200]}")
            return expect(body.get("success") is True, f"body={str(body)[:160]}")

        await r.case("POST /api/ai/classify 分类可用", _classify())

        async def _chat():
            resp = await self.post(
                "/api/ai/chat", json_body={"question": "本月餐饮花了多少"}
            )
            body = resp.json()
            if not body.get("success"):
                return expect(False, f"失败：{body.get('message')}")
            d = data_of(body) or {}
            ok = bool(d.get("answer")) and d.get("intent")
            return expect(ok, f"intent={d.get('intent')}")

        await r.case("POST /api/ai/chat 对话式查询", _chat())

        async def _suggest():
            resp = await self.get("/api/ai/categories/suggest?name=服务器租赁费")
            body = resp.json()
            if resp.status_code >= 500:
                return expect(False, f"HTTP {resp.status_code}")
            return expect(body.get("success") is True, f"body={str(body)[:160]}")

        await r.case("GET /api/ai/categories/suggest 类别自演化建议", _suggest())

        # nl-confirm：两步式的第二步，抽取结果确认后才落库
        async def _nl_confirm():
            resp = await self.post(
                "/api/ai/nl-confirm",
                json_body={
                    "items": [
                        {
                            "amount": 123.45,
                            "direction": "expense",
                            "memo": "__NL确认测试",
                            "is_confirmed": True,
                        }
                    ]
                },
            )
            body = resp.json()
            if resp.status_code >= 500:
                return expect(False, f"HTTP {resp.status_code} {resp.text[:200]}")
            for item in (data_of(body) or {}).get("created") or []:
                self.snapshot.note("ff_transaction", item.get("id"))
            return expect(body.get("success") is True, f"body={str(body)[:200]}")

        await r.case("POST /api/ai/nl-confirm 确认落库", _nl_confirm())

    async def group_import(self) -> None:
        r = self.runner
        r.group("十三、账单导入")

        import random as _random
        import string as _string
        import time as _time

        _seq = [0]

        def build_csv() -> bytes:
            """每次调用都生成**不同的**对手方。

            这套用例里 dry_run / 真实入库 / 方向识别会反复导同一份账单，
            而「重复导入应跳过」依赖金额 + 时间 + 对手方模糊匹配。
            对手方固定时，第二次起就会全部命中去重，
            用例会退化成在验证去重逻辑，而不是验证导入本身 ——
            表现为「入库 0 条（重复 3）」这种看起来像后端坏了的假故障。

            为什么 tag 不能只是一串递增数字：
                原先 tag 是「毫秒时间戳 % 1e8」拼上递增序号，两次调用若
                挨得近，对手方只差末尾一两个字符，
                fuzz.token_set_ratio 会给出 **81.25** —— 刚好越过
                find_duplicates 的 80 阈值，于是 _direction 刚入的账
                把 _real_import 的账判成重复，用例随机翻车。
                阈值附近的相似度不可依赖，tag 必须让两份账单的对手方
                **整体形态就不同**，把相似度稳稳定在 80 以下。

                注意：单纯加长数字或换前缀都不够 ——
                token_set_ratio 会抓住公共的 ``__测试`` 前缀与相同后缀，
                实测仍高达 85.7。因此这里改用**随机字母主体**，
                实测相似度落在 46~53，留足了安全边际。
                前缀保留 ``__测试``，让 restore() 的标记兜底清扫仍能命中。
            """
            _seq[0] += 1
            body = "".join(_random.choice(_string.ascii_uppercase) for _ in range(8))
            tag = f"{_seq[0]:03d}{body}"
            return (
                "微信支付账单明细,,,,,,,\n"
                "微信昵称：[示例用户],,,,,,,\n"
                "导出时间：2026-10-12,,,,,,,\n"
                ",,,,,,,\n"
                "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态\n"
                f"2026-10-08 12:30:15,商户消费,__测试咖世家{tag},拿铁咖啡,支出,¥38.00,零钱,支付成功\n"
                f"2026-10-11 14:00:00,转账,__测试科技公司{tag},项目款,收入,¥26000.00,零钱,已入账\n"
                f"2026-10-12 10:00:00,商户消费,__测试京东{tag},办公用品采购,支出,¥1,299.00,零钱,支付成功\n"
                "2026-10-12 11:00:00,零钱提现,/,提现到银行卡,不计收支,¥500.00,零钱,提现已到账\n"
            ).encode("utf-8")

        CSV = build_csv()

        async def _dry_run():
            """dry_run 只解析不入库 —— 导错账要一条条删，预览成本远低于回滚。"""
            files = {"file": ("__测试bill.csv", io.BytesIO(build_csv()), "text/csv")}
            resp = await self.post(
                "/api/import/csv?dry_run=true&auto_confirm=false", files=files
            )
            body = resp.json()
            if not body.get("success"):
                return expect(False, f"失败：{body.get('message')}")
            d = data_of(body) or {}
            # dry_run 返回 {total, imported, duplicates, failed, meta, message}
            # 「不计收支」那一条会被跳过，所以 total 应为 3
            total = d.get("total")
            ok = total == 3
            return expect(ok, f"解析 {total} 条（期望 3），keys={list(d)}")

        await r.case("POST /api/import/csv dry_run 只解析不入库", _dry_run())

        async def _thousand_sep():
            """未加引号的千分位逗号会把钱记错，必须有修复逻辑。

            dry_run 的返回是 ``{dry_run, total, preview, meta, message}`` ——
            ``preview`` 才是解析出的条目，且**未入库所以没有 id**。
            真正的 ``imported`` / ``duplicates`` 只在非 dry_run 时出现，
            且元素是 ``{id, amount, memo}`` 的摘要投影（无 counterparty）。
            对着错误字段做断言会永远拿不到值。
            """
            files = {"file": ("__测试bill.csv", io.BytesIO(build_csv()), "text/csv")}
            resp = await self.post(
                "/api/import/csv?dry_run=true&auto_confirm=false", files=files
            )
            d = data_of(resp.json()) or {}
            items = d.get("preview") or []
            jd = next(
                (i for i in items if "京东" in str(i.get("counterparty") or "")), None
            )
            if jd is None:
                return expect(False, f"preview 里找不到京东那条，共 {len(items)} 条")
            amt = jd.get("amount")
            return expect(amt == 1299.0, f"京东金额={amt}（期望 1299.0）")

        await r.case("千分位金额 ¥1,299.00 解析正确", _thousand_sep())

        async def _direction():
            """收入不能被误判为支出（方向列选错是历史 bug）。

            注意：导入响应的 ``imported`` / ``duplicates`` 只是
            ``{id, amount, memo}`` 的**摘要投影**，不含 direction。
            要看方向必须回查流水本身，否则断言永远拿不到值。
            """
            files = {"file": ("__测试bill.csv", io.BytesIO(build_csv()), "text/csv")}
            resp = await self.post(
                "/api/import/csv?dry_run=false&auto_confirm=true", files=files
            )
            d = data_of(resp.json()) or {}
            # 本次真实入库了 3 条（咖世家 38 / 科技公司 26000 / 京东 1299），
            # **必须整批登记**。曾经这里只登记了下面断言要用的 26000 那一条，
            # 于是同批的 38 与 1299 两条从未进入回收名单，跑完测试永久留在库里，
            # 直接污染总览与月度报告（表现为页面上凭空多出一笔 1299 支出）。
            for item in d.get("imported") or []:
                self.snapshot.note("ff_transaction", item.get("id"))
            self.snapshot.note("ff_import_batch", d.get("batch_id"))
            # 26000 那条必然是收入
            hit = next(
                (i for i in (d.get("imported") or [])
                 if abs(float(i.get("amount") or 0) - 26000.0) < 0.01),
                None,
            )
            if not hit:
                return expect(False, f"未入库 26000 那条，imported={d.get('imported')}")
            t = data_of((await self.get(f"/api/transactions/{hit['id']}")).json()) or {}
            return expect(
                t.get("direction") == "income",
                f"项目款 direction={t.get('direction')}",
            )

        await r.case("收入方向识别为 income", _direction())

        # 幂等用例必须复用**同一份** CSV：重新 build 会生成不同的对手方，
        # 那样验证的是"新文件能导入"，而不是"重复文件被跳过"。
        shared_csv = build_csv()

        async def _real_import():
            files = {"file": ("__测试bill.csv", io.BytesIO(shared_csv), "text/csv")}
            resp = await self.post(
                "/api/import/csv?dry_run=false&auto_confirm=true", files=files
            )
            body = resp.json()
            if not body.get("success"):
                return expect(False, f"失败：{body.get('message')}")
            d = data_of(body) or {}
            self.snapshot.note("ff_import_batch", d.get("batch_id"))
            # 入库明细在 imported 列表里，逐条登记以便收尾回收
            for item in d.get("imported") or []:
                self.snapshot.note("ff_transaction", item.get("id"))
            got = d.get("imported_count", 0)
            return expect(
                got >= 1,
                f"入库 {got} 条（重复 {d.get('duplicate_count')}）",
            )

        await r.case("POST /api/import/csv 真实入库", _real_import())

        async def _idempotent():
            """同一文件再导一次，应全部识别为重复 —— 去重比入库更重要。"""
            files = {"file": ("__测试bill.csv", io.BytesIO(shared_csv), "text/csv")}
            resp = await self.post(
                "/api/import/csv?dry_run=false&auto_confirm=true", files=files
            )
            d = data_of(resp.json()) or {}
            self.snapshot.note("ff_import_batch", d.get("batch_id"))
            for item in d.get("imported") or []:
                self.snapshot.note("ff_transaction", item.get("id"))
            dup = d.get("duplicate_count", 0)
            return expect(dup >= 1, f"重复识别 {dup} 条")

        await r.case("重复导入被识别为 duplicate（幂等）", _idempotent())

        async def _reparse():
            resp = await self.post(
                "/api/import/reparse",
                json_body={"text": "2026-10-08 星巴克咖啡 拿铁 38.00"},
            )
            body = resp.json()
            if resp.status_code >= 500:
                return expect(False, f"HTTP {resp.status_code}")
            return expect(body.get("success") is True, f"body={str(body)[:160]}")

        await r.case("POST /api/import/reparse 解析文本", _reparse())

        async def _batches():
            resp = await self.get("/api/import/batches")
            d = data_of(resp.json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            return expect(resp.status_code == 200, f"历史批次 {len(items)} 个")

        await r.case("GET /api/import/batches 批次历史", _batches())

        async def _bad_ext():
            files = {"file": ("x.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
            resp = await self.post("/api/import/ocr", files=files)
            body = resp.json()
            ok = body.get("success") is False or resp.status_code >= 400
            return expect(ok, f"HTTP {resp.status_code} success={body.get('success')}")

        await r.case("POST /api/import/ocr 拒绝非图片扩展名", _bad_ext())

    async def group_audit(self) -> None:
        r = self.runner
        r.group("十四、审计与建议采纳率")

        async def _logs():
            resp = await self.get("/api/audit/logs?limit=10")
            d = data_of(resp.json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            # 前面的写操作都应留下审计记录
            ok = resp.status_code == 200 and len(items) > 0
            return expect(ok, f"{len(items)} 条日志")

        await r.case("GET /api/audit/logs 有审计记录", _logs())

        async def _logs_filter():
            for q in ("?action=create_transaction", "?channel=web", "?limit=5&offset=0"):
                resp = await self.get(f"/api/audit/logs{q}")
                if resp.status_code != 200:
                    return expect(False, f"{q} → HTTP {resp.status_code}")
            return expect(True, "过滤参数均可用")

        await r.case("审计日志过滤参数可用", _logs_filter())

        async def _stats():
            resp = await self.get("/api/audit/stats")
            d = data_of(resp.json()) or {}
            need = ["total", "by_state", "adoption_rate"]
            missing = [k for k in need if k not in d]
            return expect(not missing, f"缺失={missing}")

        await r.case("GET /api/audit/stats 采纳率结构完整", _stats())

        async def _rate_math():
            """采纳率 = adopted / (adopted + rejected)，ignored 不进分母。"""
            d = data_of((await self.get("/api/audit/stats")).json()) or {}
            by = d.get("by_state") or {}
            adopted = by.get("adopted", 0)
            rejected = by.get("rejected", 0)
            decided = adopted + rejected
            expect_rate = round(adopted / decided * 100, 2) if decided else 0.0
            actual = d.get("adoption_rate", 0)
            return expect(
                abs(actual - expect_rate) < 0.05,
                f"实际={actual} 手算={expect_rate} (adopted={adopted} rejected={rejected})",
            )

        await r.case("采纳率口径 adopted/(adopted+rejected) 正确", _rate_math())

        async def _suggestions():
            resp = await self.get("/api/audit/suggestions?limit=10")
            d = data_of(resp.json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            return expect(resp.status_code == 200, f"{len(items)} 条建议")

        await r.case("GET /api/audit/suggestions 可用", _suggestions())

        async def _resolve():
            """采纳/拒绝一条建议，验证采纳率统计真的会动。"""
            d = data_of((await self.get("/api/audit/suggestions?state=pending&limit=1")).json())
            items = d if isinstance(d, list) else (d or {}).get("items", [])
            if not items:
                return expect(True, "暂无 pending 建议，跳过实际操作")
            sid = items[0].get("id")

            before = data_of((await self.get("/api/audit/stats")).json()) or {}
            resp = await self.post(
                f"/api/audit/suggestions/{sid}/resolve", json_body={"adopted": True}
            )
            after = data_of((await self.get("/api/audit/stats")).json()) or {}

            adopted_before = (before.get("by_state") or {}).get("adopted", 0)
            adopted_after = (after.get("by_state") or {}).get("adopted", 0)
            return expect(
                resp.status_code == 200 and adopted_after == adopted_before + 1,
                f"adopted {adopted_before} → {adopted_after}",
            )

        await r.case("POST /api/audit/suggestions/{id}/resolve 计入采纳率", _resolve())

        async def _not_found():
            resp = await self.post(
                "/api/audit/suggestions/99999999/resolve", json_body={"adopted": True}
            )
            body = resp.json()
            ok = resp.status_code == 404 or body.get("success") is False
            return expect(ok, f"HTTP {resp.status_code}")

        await r.case("不存在的建议返回失败而非 500", _not_found())

    async def group_push(self) -> None:
        r = self.runner
        r.group("十五、协议端推送")

        async def _status():
            resp = await self.get("/api/push/status")
            d = data_of(resp.json()) or {}
            return expect(resp.status_code == 200 and "bot" in d, f"keys={list(d)}")

        await r.case("GET /api/push/status 返回机器人状态", _status())

        async def _test_no_target():
            """没配推送目标时应给明确错误，而不是抛异常。"""
            resp = await self.post("/api/push/test", json_body={})
            body = resp.json()
            ok = resp.status_code < 500
            return expect(ok, f"HTTP {resp.status_code} msg={str(body.get('message'))[:60]}")

        await r.case("POST /api/push/test 未配目标时优雅失败", _test_no_target())

        async def _latest_no_target():
            resp = await self.post("/api/push/latest", json_body={})
            body = resp.json()
            return expect(resp.status_code < 500, f"HTTP {resp.status_code}")

        await r.case("POST /api/push/latest 不抛异常", _latest_no_target())

    async def group_errors(self) -> None:
        r = self.runner
        r.group("十六、错误处理与健壮性")

        async def _unknown():
            resp = await self.get("/api/no-such-endpoint")
            return expect(resp.status_code == 404, f"HTTP {resp.status_code}")

        await r.case("未知接口返回 404", _unknown())

        async def _bad_json():
            async with httpx.AsyncClient(base_url=self.base, timeout=self.timeout) as c:
                resp = await c.post(
                    "/api/transactions",
                    content=b"{not valid json",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.token}",
                    },
                )
            return expect(resp.status_code == 422, f"HTTP {resp.status_code}")

        await r.case("畸形 JSON 返回 422", _bad_json())

        async def _missing_required():
            resp = await self.post("/api/transactions", json_body={"memo": "没有金额"})
            return expect(resp.status_code == 422, f"HTTP {resp.status_code}")

        await r.case("缺少必填字段返回 422", _missing_required())

        async def _bad_type():
            """查询参数不会被强制转换时，系统应安静处理而非 500。"""
            cases = [
                "/api/stats?custom_days=abc",
                "/api/transactions?limit=abc",
                "/api/audit/logs?limit=abc",
                "/api/import/batches?limit=abc",
                "/api/tax-check/checks?limit=abc",
            ]
            bad = []
            for path in cases:
                resp = await self.get(path)
                if resp.status_code >= 500:
                    bad.append(f"{path}→{resp.status_code}")
            return expect(not bad, f"触发 500 的路径: {bad}")

        await r.case("非法查询参数不触发 500", _bad_type())

        async def _envelope():
            """统一响应信封：除文档类接口外都应含 success。"""
            bad = []
            for path in ("/api/health", "/api/meta", "/api/stats", "/api/categories"):
                body = (await self.get(path)).json()
                if not envelope_ok(body):
                    bad.append(path)
            return expect(not bad, f"未遵循信封: {bad}")

        await r.case("统一响应信封 {success, data}", _envelope())

        async def _static():
            """前端资源必须能取到，否则同端口挂载失效。"""
            bad = []
            for path in (
                "/",
                "/js/api.js",
                "/js/app.js",
                "/js/auth.js",
                "/css/main.css",
                "/index.html",
            ):
                resp = await self.get(path, auth=False)
                if resp.status_code != 200:
                    bad.append(f"{path}→{resp.status_code}")
            return expect(not bad, f"失败: {bad}")

        await r.case("前端静态资源可访问", _static())

    # ================================================== 主流程

    async def run(self) -> Runner:
        r = self.runner

        print("=" * 68)
        print("  流萤 Firefly · 全量接口测试")
        print("=" * 68)

        # 0) 快照
        try:
            counts = await self.take_snapshot()
            self.snapshot.tracked = True
            total = sum(counts.values())
            print(f"\n[准备] 已记录基线：{len(counts)} 张表 / {total} 行")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[警告] 基线快照失败：{exc}")
            print("       测试仍会运行，但数据回收可能不完整。")

        # 1) 登录
        ok, msg = await self.login()
        if not ok:
            print(f"\n[致命] 登录失败：{msg}")
            print("       请确认 config.yaml 的 auth.username / auth.password。")
            return r
        print(f"[准备] 登录成功：{self.username}")

        # 2) 用例
        for fn in (
            self.group_public,
            self.group_guard,
            self.group_auth,
            self.group_meta,
            self.group_category,
            self.group_transaction,
            self.group_stats,
            self.group_budget,
            self.group_subscription,
            self.group_report,
            self.group_taxcheck,
            self.group_ai,
            self.group_import,
            self.group_audit,
            self.group_push,
            self.group_errors,
        ):
            try:
                await fn()
            except Exception as exc:  # noqa: BLE001
                r.results.append(
                    CaseResult(
                        r.current_group,
                        f"分组 <{fn.__name__}> 执行异常",
                        FAIL,
                        f"{type(exc).__name__}: {exc}",
                    )
                )

        # 3) 回收
        print("\n[回收] 正在清理测试数据…")
        try:
            removed = await self.restore()
            detail = ", ".join(f"{k}×{v}" for k, v in removed.items() if v)
            print(f"[回收] 已删除 {self.snapshot.new_count} 条测试新增记录")
            if detail:
                print(f"       明细：{detail}")
        except Exception as exc:  # noqa: BLE001
            print(f"[回收] 失败：{exc}")

        return r


# ============================================================ 输出


def print_report(r: Runner) -> None:
    groups: Dict[str, List[CaseResult]] = {}
    for item in r.results:
        groups.setdefault(item.group, []).append(item)

    icon = {PASS: "✅", FAIL: "❌", SKIP: "⏭️ ", WARN: "⚠️ "}

    for gname, items in groups.items():
        print(f"\n【{gname}】")
        for it in items:
            name = it.name if len(it.name) <= 52 else it.name[:49] + "…"
            print(f"  {icon[it.status]} {name}")
            if it.status in (FAIL, WARN) and it.detail:
                first = it.detail.strip().splitlines()[0]
                print(f"       └─ {first[:150]}")

    total = len(r.results)
    print("\n" + "=" * 68)
    print(
        f"  合计 {total} 项 ｜ 通过 {r.passed} ｜ 失败 {r.failed}"
        f" ｜ 警告 {r.warned} ｜ 跳过 {r.skipped}"
    )
    rate = (r.passed / total * 100) if total else 0
    print(f"  通过率：{rate:.1f}%")
    print("=" * 68)

    if r.failed:
        print("\n失败明细：")
        for it in r.results:
            if it.status == FAIL:
                print(f"\n  ❌ {it.group} / {it.name}")
                print(f"     {it.detail[:600]}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="流萤 Firefly 全量接口测试")
    parser.add_argument("--base-url", default="http://127.0.0.1:2525")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="Firefly2026")
    parser.add_argument(
        "--no-llm", action="store_true", help="跳过消耗 API 额度的用例"
    )
    parser.add_argument("--no-restore", action="store_true", help="不回收测试数据")
    parser.add_argument("--json", default=None, help="把结果写入 JSON 文件")
    args = parser.parse_args()

    tester = ApiTester(
        args.base_url,
        args.username,
        args.password,
        use_llm=not args.no_llm,
    )

    if args.no_restore:
        # 打补丁禁用回收，便于调试
        async def _noop() -> Dict[str, int]:
            return {}

        tester.restore = _noop  # type: ignore[assignment]

    r = await tester.run()
    print_report(r)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump(
                {
                    "total": len(r.results),
                    "passed": r.passed,
                    "failed": r.failed,
                    "warned": r.warned,
                    "skipped": r.skipped,
                    "results": [it.__dict__ for it in r.results],
                },
                fp,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n结果已写入 {args.json}")

    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
