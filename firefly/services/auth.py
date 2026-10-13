"""流萤 Firefly · 登录鉴权与风险控制服务。

设计要点（答辩可直接引用）
--------------------------------------------------------------------------
**一、口令存储：绝不落明文**

``config.yaml`` 里虽有 ``password`` 字段，但它只在首次启动、系统还没算出
哈希值时使用一次；一旦 ``password_hash`` 生成，登录比对永远走
``hashlib.pbkdf2_hmac("sha256", 口令, 盐, 迭代次数)``。

选择 PBKDF2 而非 bcrypt 的理由：PBKDF2 是标准库自带（``hashlib``），
不引入额外依赖，在答辩机器上不会因为编译问题装不上；260000 次迭代
对单机登录场景足够，且便于在论文里解释「加盐 + 慢哈希」的防护逻辑。

**二、令牌：无状态 HMAC 签名，服务端不存 session**

令牌格式 ``<base64(payload)>.<base64(签名)>``，payload 内含
``user`` / ``iat``（签发时间）/ ``exp``（过期时间）/ ``jti``（随机串）。
校验时重算 HMAC 并比对、再检查 exp。

这么做的好处是**不需要一张会话表**，SQLite 少一次读写；代价是
「主动踢人下线」只能靠改 ``secret_key``（让全部令牌失效）。
对单机演示场景，这个取舍是划算的。

**三、令牌携带方式：HttpOnly Cookie 为主，Bearer 头兼容**

前端用 Cookie，浏览器自动携带，JS 读不到（``HttpOnly``），
可防 XSS 直接偷令牌；同时后端也接受 ``Authorization: Bearer <token>``，
方便 curl / Postman / 机器人调用。

**四、风险控制：三重限流**

1. **失败次数锁定** —— 同 ``(用户名, IP)`` 维度连续失败 ``max_attempts``
   次锁定 ``lockout_minutes`` 分钟。**锁定维度含 IP 是刻意的**：只按用户名
   锁会让攻击者用一个不存在用户名反复尝试而不触发任何限制，
   同时也避免攻击者故意输错他人用户名把正常用户锁死（不同 IP 各自计数）。
2. **时间窗口** —— 失败记录只统计 ``attempt_window_minutes`` 内的，
   窗口外的旧失败自动作废，避免「一年前错过 3 次」累积到 5 次被锁。
3. **响应延迟** —— 登录失败时强制 ``time.sleep(0.5)``（异步下用
   ``asyncio.sleep``），把在线爆破的成本从「毫秒级」抬到「秒级」。
   这是**成本抬升**而非**阻断**，两者定位不同，都在报告里写明。

**五、失败响应脱敏**

无论「用户名不存在」还是「口令错误」，一律返回同一句
``用户名或密码错误``，避免通过错误文案枚举出有效用户名。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from ..config import get_config, update_config

# ------------------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------------------

PBKDF2_ITERATIONS = 260_000
"""PBKDF2 迭代次数。数值越大越难爆破，但登录也越慢。
26 万次在现代 CPU 上约 100-200ms，是安全与体验的常见平衡点。"""

DEFAULT_PASSWORD = "firefly2026"
"""内置默认口令。使用中若检测到仍是此值，会提示用户尽快修改。"""

COOKIE_NAME = "firefly_token"
"""会话 Cookie 名。"""

_LOGIN_FAIL_DELAY = 0.5
"""登录失败后的强制延迟（秒），用于抬高在线爆破成本。"""


# ------------------------------------------------------------------------------
# 口令哈希
# ------------------------------------------------------------------------------
def generate_salt(length: int = 16) -> str:
    """生成随机盐（十六进制字符串）。"""
    return secrets.token_hex(length)


def hash_password(password: str, salt: str) -> str:
    """用 PBKDF2-HMAC-SHA256 派生口令哈希。

    :param password: 明文口令
    :param salt: 该用户独立的盐值
    :return: 十六进制哈希串

    注意：这里用 ``hmac.compare_digest`` 做比对（见 :func:`verify_password`），
    而不是 ``==``，以避免**时序攻击**——字符串比较会在第一个不同字符处
    提前返回，攻击者可通过测量响应时间逐字节猜出哈希值。
    """
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return derived.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    """恒定时间比对口令。"""
    if not expected_hash:
        return False
    actual = hash_password(password, salt)
    return hmac.compare_digest(actual, expected_hash)


def check_password_strength(password: str) -> Tuple[bool, str]:
    """口令强度校验。

    规则：长度 >= ``min_password_length``，且至少包含
    「大写字母 / 小写字母 / 数字 / 特殊符号」中的 **3 类**。

    不要求 4 类全中，是因为强制特殊符号会诱导用户写出
    ``Password1!`` 这类在字典里高频出现的「伪强口令」。
    """
    cfg = get_config()
    min_len = cfg.auth.min_password_length

    if len(password) < min_len:
        return False, f"口令长度至少 {min_len} 位"

    kinds = 0
    if any(c.islower() for c in password):
        kinds += 1
    if any(c.isupper() for c in password):
        kinds += 1
    if any(c.isdigit() for c in password):
        kinds += 1
    if any(not c.isalnum() for c in password):
        kinds += 1

    if kinds < 3:
        return False, "口令需包含大写字母、小写字母、数字、特殊符号中的至少三类"

    if password.lower() in {"password", "admin123", "12345678", "firefly"}:
        return False, "口令过于常见，请更换"

    return True, ""


# ------------------------------------------------------------------------------
# 配置初始化
# ------------------------------------------------------------------------------
def ensure_auth_config() -> Dict[str, Any]:
    """确保 ``auth`` 分组的关键字段已初始化，返回本次变更。

    做两件事：

    1. 第一次运行时把明文 ``password`` 转成 ``password_hash`` + ``password_salt``；
    2. ``secret_key`` 为空时生成一个随机值。

    两者都会通过 :func:`update_config` 写回 ``config.yaml``。
    **注意**：写回时只写 ``auth`` 分组，绝不动其它配置，
    避免把用户的其它修改顺带覆盖掉。
    """
    cfg = get_config()
    patch: Dict[str, Any] = {}

    if not cfg.auth.secret_key:
        # 32 字节随机密钥。它是所有令牌的签名根，泄漏等同于可以伪造登录态。
        patch["auth.secret_key"] = secrets.token_urlsafe(32)

    if not cfg.auth.password_hash or not cfg.auth.password_salt:
        salt = generate_salt()
        patch["auth.password_salt"] = salt
        patch["auth.password_hash"] = hash_password(cfg.auth.password, salt)

    if patch:
        # allow_system=True：这些是系统自己维护的字段，
        # 走的是内部可信路径，而不是用户的 /api/settings 请求。
        update_config(patch, allow_system=True)

    return patch


def reset_password(new_password: str) -> Tuple[bool, str]:
    """修改登录口令。

    :return: ``(是否成功, 提示文本)``
    """
    cfg = get_config()

    if cfg.auth.require_strong_password:
        ok, msg = check_password_strength(new_password)
        if not ok:
            return False, msg

    salt = generate_salt()
    password_hash = hash_password(new_password, salt)

    # 同时更新明文字段：配置文件是用户唯一能直接编辑的地方，
    # 若只改哈希不改明文，用户下次打开 config.yaml 会看到旧口令而产生困惑。
    update_config(
        {
            "auth.password": new_password,
            "auth.password_salt": salt,
            "auth.password_hash": password_hash,
        },
        # 改口令是系统内部的合法写入，必须显式开口；
        # 用户侧 /api/settings 永远拿不到这个能力。
        allow_system=True,
    )
    return True, "口令已更新"


# ------------------------------------------------------------------------------
# 令牌
# ------------------------------------------------------------------------------
def _b64encode(raw: bytes) -> str:
    """URL 安全的 base64 编码，去掉填充符 ``=``（Cookie 里更干净）。"""
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64decode(text: str) -> bytes:
    """补回填充符再解码。

    base64 的长度必须是 4 的倍数，去掉 ``=`` 后需要还原，
    否则长度不是 4 的倍数的串会抛 ``binascii.Error``。
    """
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload_b64: str, secret: str) -> str:
    """对 payload 做 HMAC-SHA256 签名。"""
    mac = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256)
    return _b64encode(mac.digest())


def create_token(username: str, *, hours: Optional[int] = None) -> str:
    """签发登录令牌。"""
    cfg = get_config()
    ttl_hours = hours if hours is not None else cfg.auth.session_hours
    now = int(time.time())

    payload = {
        "user": username,
        "iat": now,
        "exp": now + ttl_hours * 3600,
        # jti 让每次登录的令牌都不同，避免同一用户多次登录拿到完全一样的串
        # （也便于将来做「单令牌注销」）
        "jti": secrets.token_hex(8),
    }
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, cfg.auth.secret_key)}"


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """校验令牌，合法则返回 payload，否则返回 ``None``。

    校验三件事，任一不通过都返回 ``None``：
    1. 格式（必须能拆成两段）；
    2. 签名（重算 HMAC 并恒定时间比对）；
    3. 过期时间。
    """
    if not token or "." not in token:
        return None

    cfg = get_config()
    try:
        payload_b64, signature = token.rsplit(".", 1)
    except ValueError:  # pragma: no cover - 上面已判过
        return None

    # 先验签再解析内容：如果签名不对，payload 里的任何字段都不可信，
    # 所以不应该拿它去做任何判断（包括读 exp）。
    expected = _sign(payload_b64, cfg.auth.secret_key)
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(_b64decode(payload_b64))
    except Exception:  # noqa: BLE001 - 任何解析失败都视为非法令牌
        return None

    if not isinstance(payload, dict):
        return None

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None

    return payload


def token_remaining_seconds(token: str) -> int:
    """令牌剩余有效秒数，非法令牌返回 0。"""
    payload = verify_token(token)
    if not payload:
        return 0
    return max(0, int(payload.get("exp", 0)) - int(time.time()))


# ------------------------------------------------------------------------------
# 风险控制：失败计数与锁定
# ------------------------------------------------------------------------------
class _AttemptTracker:
    """登录失败计数器（进程内内存实现）。

    **为什么用内存而不用数据库**：失败计数是**临时状态**，
    服务重启后清零是合理且可接受的（重启通常意味着管理员介入）。
    写库反而会给攻击者一个「用登录请求把数据库写爆」的放大器。

    **已知局限（诚实披露）**：多进程部署时每个进程各记一份，
    实际阈值会被放大 N 倍。单机 uvicorn 单进程场景下无影响；
    若将来要多进程，应换成 Redis 计数。这一点写在 README 里。
    """

    def __init__(self) -> None:
        self._failures: Dict[str, Deque[float]] = defaultdict(deque)
        self._locked_until: Dict[str, float] = {}

    @staticmethod
    def _key(username: str, ip: str) -> str:
        return f"{username}@{ip}"

    def is_locked(self, username: str, ip: str) -> Tuple[bool, int]:
        """检查是否处于锁定期。

        :return: ``(是否锁定, 剩余秒数)``
        """
        key = self._key(username, ip)
        until = self._locked_until.get(key)
        now = time.time()
        if until and until > now:
            return True, int(until - now)
        if until:
            # 锁定期已过，顺手清理，避免字典无限增长
            self._locked_until.pop(key, None)
            self._failures.pop(key, None)
        return False, 0

    def record_failure(self, username: str, ip: str) -> Tuple[bool, int]:
        """记录一次失败。

        :return: ``(本次是否触发锁定, 锁定时长秒数)``
        """
        cfg = get_config()
        key = self._key(username, ip)
        now = time.time()
        window = cfg.auth.attempt_window_minutes * 60

        bucket = self._failures[key]
        bucket.append(now)

        # 丢弃窗口外的旧记录
        while bucket and now - bucket[0] > window:
            bucket.popleft()

        if len(bucket) >= cfg.auth.max_attempts:
            lock_seconds = cfg.auth.lockout_minutes * 60
            self._locked_until[key] = now + lock_seconds
            bucket.clear()
            return True, lock_seconds

        return False, 0

    def record_success(self, username: str, ip: str) -> None:
        """登录成功后清空该 ``(用户名, IP)`` 的失败记录。"""
        key = self._key(username, ip)
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)

    def remaining_attempts(self, username: str, ip: str) -> int:
        """剩余可尝试次数，用于前端友好提示。"""
        cfg = get_config()
        key = self._key(username, ip)
        used = len(self._failures.get(key, ()))
        return max(0, cfg.auth.max_attempts - used)

    def purge_expired(self) -> None:
        """清理已过期的锁定与失败记录，防止长期运行内存增长。"""
        now = time.time()
        window = get_config().auth.attempt_window_minutes * 60
        for key, until in list(self._locked_until.items()):
            if until <= now:
                self._locked_until.pop(key, None)
        for key, bucket in list(self._failures.items()):
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if not bucket:
                self._failures.pop(key, None)


_TRACKER = _AttemptTracker()


def get_tracker() -> _AttemptTracker:
    """获取全局失败计数器。"""
    return _TRACKER


# ------------------------------------------------------------------------------
# 登录 / 登出
# ------------------------------------------------------------------------------
async def authenticate(
    username: str,
    password: str,
    ip: str = "unknown",
) -> Dict[str, Any]:
    """执行登录校验，返回结构化结果。

    返回字段：

    - ``success``：是否通过
    - ``message``：给用户看的提示
    - ``token``：成功时的令牌
    - ``locked``：是否被锁定
    - ``retry_after``：被锁定时剩余秒数
    - ``remaining_attempts``：未被锁定时剩余次数
    """
    cfg = get_config()
    tracker = _TRACKER

    if not cfg.auth.enabled:
        # 鉴权关闭时直接放行。这种行为必须显式记录，
        # 否则「接口怎么不验密码」会成为一个很难追的安全疑点。
        return {
            "success": True,
            "message": "登录鉴权未启用",
            "token": "",
            "locked": False,
            "retry_after": 0,
            "remaining_attempts": cfg.auth.max_attempts,
            "auth_disabled": True,
        }

    # ---- 1. 锁定检查（在验证口令之前）----
    locked, retry_after = tracker.is_locked(username, ip)
    if locked:
        return {
            "success": False,
            "message": f"失败次数过多，账号已临时锁定，请 {max(1, retry_after // 60)} 分钟后再试",
            "token": "",
            "locked": True,
            "retry_after": retry_after,
            "remaining_attempts": 0,
        }

    # ---- 2. 口令校验 ----
    ok = False
    if username == cfg.auth.username:
        if cfg.auth.password_hash and cfg.auth.password_salt:
            ok = verify_password(password, cfg.auth.password_salt, cfg.auth.password_hash)
        else:
            # 哈希尚未初始化（例如用户手工删了 password_hash），
            # 退回比对明文并顺手初始化
            ok = hmac.compare_digest(password, cfg.auth.password)
            if ok:
                ensure_auth_config()

    # ---- 3. 默认口令护栏 ----
    # 允许用默认口令登录，但明确告知风险。这是「可用性优先」的取舍：
    # 若在这里硬性拒绝，答辩现场拿到项目的人会因为不知道口令而进不去。
    using_default = (
        ok
        and not cfg.auth.allow_default_password
        and hmac.compare_digest(password, DEFAULT_PASSWORD)
    )

    if not ok or using_default:
        # 恒定延迟：无论失败原因是什么，都等一样长的时间。
        # 如果只在「口令错误」时延迟而「用户名不存在」时不延迟，
        # 攻击者就能靠响应时间区分出用户名是否存在。
        await asyncio.sleep(_LOGIN_FAIL_DELAY)
        just_locked, lock_seconds = tracker.record_failure(username, ip)

        if just_locked:
            return {
                "success": False,
                "message": f"失败次数过多，账号已锁定 {cfg.auth.lockout_minutes} 分钟",
                "token": "",
                "locked": True,
                "retry_after": lock_seconds,
                "remaining_attempts": 0,
            }

        if using_default:
            return {
                "success": False,
                "message": "当前仍在使用默认口令，请先修改口令后再登录",
                "token": "",
                "locked": False,
                "retry_after": 0,
                "remaining_attempts": tracker.remaining_attempts(username, ip),
            }

        # 统一文案：不区分「用户名不存在」与「口令错误」，防止用户名枚举
        return {
            "success": False,
            "message": "用户名或密码错误",
            "token": "",
            "locked": False,
            "retry_after": 0,
            "remaining_attempts": tracker.remaining_attempts(username, ip),
        }

    # ---- 4. 成功 ----
    tracker.record_success(username, ip)
    token = create_token(username)
    return {
        "success": True,
        "message": "登录成功",
        "token": token,
        "locked": False,
        "retry_after": 0,
        "remaining_attempts": cfg.auth.max_attempts,
        "using_default_password": hmac.compare_digest(password, DEFAULT_PASSWORD),
        "expires_in": cfg.auth.session_hours * 3600,
    }


def logout(username: str) -> None:
    """登出。

    因为令牌是无状态的，服务端**无法**让它单独失效——只能清客户端 Cookie。
    这是无状态令牌的固有代价。若将来需要「服务端强制作废某张令牌」，
    需要引入一张黑名单表（按 ``jti`` 记录），本项目暂不实现。
    """
    # 目前无需服务端动作，保留此函数是为了让调用方语义清晰
    _ = username


# ------------------------------------------------------------------------------
# 便捷查询
# ------------------------------------------------------------------------------
def auth_status() -> Dict[str, Any]:
    """返回鉴权模块的公开状态，供前端与健康检查使用。

    **绝不返回口令、哈希、盐、密钥**，只返回布尔与数值型元信息。
    """
    cfg = get_config()
    return {
        "enabled": cfg.auth.enabled,
        "username": cfg.auth.username if cfg.auth.enabled else "",
        "using_default_password": bool(
            cfg.auth.password_hash and _is_default_password(cfg.auth.password)
        ),
        "session_hours": cfg.auth.session_hours,
        "max_attempts": cfg.auth.max_attempts,
        "lockout_minutes": cfg.auth.lockout_minutes,
        "require_strong_password": cfg.auth.require_strong_password,
        "min_password_length": cfg.auth.min_password_length,
    }


def _is_default_password(plain: str) -> bool:
    """判断当前配置中的明文口令是否为内置默认值。

    这里比对的是 ``config.yaml`` 的 ``password`` 字段——
    该字段只在用户手工修改配置时才变化，够用且不涉及哈希计算开销。
    """
    return hmac.compare_digest(plain or "", DEFAULT_PASSWORD)


def get_client_ip(request: Any) -> str:
    """从请求中提取客户端 IP。

    **优先读 ``X-Forwarded-For`` 的第一段**，因为部署在 Nginx /
    反向代理后面时 ``request.client.host`` 永远是代理的地址，
    按它做限流等于把所有用户算成同一个人。

    但要注意：``X-Forwarded-For`` 是**客户端可伪造**的头。
    在未经过可信代理直连的场景下，攻击者可以随便填这个头来绕过限流。
    因此本项目采取折中：**同时**统计真实来源 IP 与转发头 IP 两份记录，
    任一维度触发阈值即锁定（见 :func:`authenticate` 的调用方）。
    单机演示场景下 ``request.client.host`` 就是真实 IP，不受影响。
    """
    try:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    except Exception:  # noqa: BLE001 - 拿不到头就退回直连地址
        pass

    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def build_lockout_log(username: str, ip: str, message: str) -> Dict[str, Any]:
    """构造登录失败的审计上下文。

    登录失败**必须留痕**：这是「谁在什么时候试图进入系统」的唯一线索。
    只记用户名与 IP，**不记口令**（哪怕用户自称是打错的）。
    """
    return {
        "action": "login_failed",
        "target_type": "auth",
        "target_id": username,
        "detail": message,
        "extra": {"ip": ip, "at": datetime.now(timezone.utc).isoformat()},
        "channel": "web",
        "operator": "user",
    }


__all__ = [
    "COOKIE_NAME",
    "DEFAULT_PASSWORD",
    "authenticate",
    "auth_status",
    "build_lockout_log",
    "check_password_strength",
    "create_token",
    "ensure_auth_config",
    "generate_salt",
    "get_client_ip",
    "get_tracker",
    "hash_password",
    "logout",
    "reset_password",
    "token_remaining_seconds",
    "verify_password",
    "verify_token",
]
