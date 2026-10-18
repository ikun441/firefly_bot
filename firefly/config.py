"""流萤 Firefly · 配置加载模块

职责：
    1. 读取根目录 config.yaml 作为全局默认配置
    2. 读取 .env 中的敏感值（API Key / Token）
    3. 合并为单一的 Config 对象供全局使用
    4. 支持运行时回写 config.yaml（供前端「设置」页调用）

优先级：真实环境变量 > .env 文件 > config.yaml > 代码内置默认值
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# ------------------------------------------------------------------------------
# 路径常量
# ------------------------------------------------------------------------------
# firefly/config.py -> firefly/ -> 项目根目录
BASE_DIR: Path = Path(__file__).resolve().parent.parent
CONFIG_PATH: Path = BASE_DIR / "config.yaml"
ENV_PATH: Path = BASE_DIR / ".env"
DATA_DIR: Path = BASE_DIR / "data"
INDEX_DIR: Path = BASE_DIR / "index"


# ------------------------------------------------------------------------------
# 各配置分组的 Pydantic 模型
# ------------------------------------------------------------------------------
class AppConfig(BaseModel):
    name: str = "流萤 Firefly"
    subtitle: str = "智能财报总结与财税优化助手"
    version: str = "1.0.3"
    debug: bool = False
    timezone: str = "Asia/Shanghai"


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 2525
    show_welcome: bool = True


class DatabaseConfig(BaseModel):
    url: str = "sqlite+aiosqlite:///./data/firefly.db"
    echo: bool = False


class LLMConfig(BaseModel):
    enabled: bool = True
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout: int = 60
    max_retries: int = 2
    api_key_env: str = "LLM_API_KEY"
    # YAML is supported; environment variables override it. Display APIs always redact it.
    api_key: str = ""

    @property
    def is_ready(self) -> bool:
        """LLM 是否可真正调用（开关打开且密钥存在）"""
        return self.enabled and bool(self.api_key)


class LedgerConfig(BaseModel):
    currency: str = "CNY"
    currency_symbol: str = "¥"
    default_period: str = "monthly"
    custom_period_days: List[int] = Field(default_factory=lambda: [7, 14, 30, 60, 90, 180, 365])
    large_amount_threshold: float = 5000.0
    duplicate_window_minutes: int = 10
    auto_confirm_hours: int = 0


class CollectorConfig(BaseModel):
    allow_custom_category: bool = True
    quick_amounts: List[float] = Field(default_factory=lambda: [10, 50, 100, 200, 500, 1000])
    memo_required: bool = False


class AnomalyConfig(BaseModel):
    enabled: bool = True
    method: str = "zscore"
    zscore_threshold: float = 2.5
    min_samples: int = 10
    risk_keywords_weight: float = 0.6


class TaxCheckConfig(BaseModel):
    enabled: bool = True
    taxpayer_type: str = "small_scale"
    vat_rate_small_scale: float = 1.0
    vat_rate_general: float = 6.0
    vat_threshold_monthly: float = 100000.0
    tax_burden_low: float = 1.0
    tax_burden_high: float = 8.0
    category_concentration_warn: float = 40.0
    disclaimer: str = (
        "本结果由系统依据公开政策规则自动生成，仅供参考，不构成专业税务意见，"
        "请以主管税务机关口径为准。"
    )


class BudgetConfig(BaseModel):
    enabled: bool = True
    warn_ratio: float = 0.8
    over_ratio: float = 1.0


class AlertConfig(BaseModel):
    enabled: bool = True
    daily_expense_limit: float = 3000.0
    low_balance_threshold: float = 1000.0


class PushConfig(BaseModel):
    enabled: bool = True
    target_type: str = "private"
    target_id: str = ""
    schedule_times: List[str] = Field(default_factory=lambda: ["09:00"])
    send_image: bool = True
    send_summary_text: bool = True
    report_period: str = "monthly"
    image_width: int = 800
    image_scale: int = 3

    # 图片最大高度（像素）。QQ 对超长图会直接拒收，
    # 而失败只在协议端侧发生、HTTP 仍是 200，非常难排查。
    # 报告图动辄 7000+ px，所以这里主动压缩。
    image_max_height: int = 6000

    # 定时推送的默认内容形态：text（纯文字）/ image（图片）/ both（两者都发）
    content_type: str = "both"

    # 定时推送是否附带 AI 总结
    ai_summary: bool = False
    ai_temperature: float = 0.5
    ai_max_tokens: int = 800


class SubscriptionConfig(BaseModel):
    enabled: bool = True
    run_day: int = 1
    run_time: str = "08:00"


class UIConfig(BaseModel):
    theme: str = "firefly-dark"
    accent_color: str = "#5eead4"
    particle_enabled: bool = True
    report_animation: bool = True
    counter_duration: int = 1200
    page_size: int = 20


class RAGConfig(BaseModel):
    enabled: bool = True
    persist_dir: str = "./data/chroma"
    collection_name: str = "tax_policy"
    top_k: int = 3
    score_threshold: float = 0.3


class AuditConfig(BaseModel):
    enabled: bool = True
    track_adoption: bool = True


class AuthConfig(BaseModel):
    """登录鉴权与风险控制配置。

    安全设计说明（答辩可引用）：

    1. **口令绝不存明文**。``password`` 只在首次启动时读入内存，用于校验；
       真正落盘比对的是 PBKDF2-HMAC-SHA256 派生值（``password_hash`` +
       每条独立的随机 ``password_salt``）。即使 ``config.yaml`` 泄漏，
       攻击者也无法直接得到口令。

    2. **默认关闭**。``enabled`` 默认为 ``False``，因为本项目是单机演示，
       强制登录会挡住答辩现场的正常演示；用户显式开启后才生效。
       但一旦 ``host`` 绑定到 0.0.0.0（对外暴露），启动时会强制告警。

    3. **失败锁定**。连续失败 ``max_attempts`` 次锁定 ``lockout_minutes`` 分钟，
       防止在线暴力破解。

    4. **会话令牌无状态校验**。令牌是 HMAC 签名的，服务端不存 session，
       重启即全部失效（对单机演示足够，且省去一张会话表）。
    """

    enabled: bool = False
    username: str = "admin"
    password: str = "firefly2026"
    # 以下两项由系统自动维护，不应手工填写
    password_hash: str = ""
    password_salt: str = ""

    # ---- 会话 ----
    secret_key: str = ""
    """HMAC 签名密钥。留空时首次启动自动生成并写回 config.yaml。
    一旦生成请勿随意修改，改了会让所有已登录会话立即失效。"""

    session_hours: int = 12
    """登录有效期（小时）。"""

    # ---- 风险控制 ----
    max_attempts: int = 5
    """账号 + IP 维度允许的连续失败次数。"""

    lockout_minutes: int = 15
    """触发锁定后的冷却时长（分钟）。"""

    attempt_window_minutes: int = 10
    """失败计数的时间窗口（分钟），窗口外的旧失败记录自动失效。"""

    require_strong_password: bool = True
    """是否强制口令强度校验（长度 / 字符种类）。"""

    min_password_length: int = 8
    """口令最小长度。"""

    allow_default_password: bool = True
    """是否允许使用内置默认口令。设为 false 时，若仍是默认口令则拒绝登录，
    强制用户先改口令——对外部署时建议开启。"""


class OneBotConfig(BaseModel):
    """协议端连接配置，主要来自 .env"""

    access_token: str = ""
    ws_url: str = ""
    ws_path: str = "/onebot/v11/ws"
    superusers: List[str] = Field(default_factory=list)


# ------------------------------------------------------------------------------
# 顶层配置对象
# ------------------------------------------------------------------------------
class Config(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    ledger: LedgerConfig = Field(default_factory=LedgerConfig)
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    anomaly: AnomalyConfig = Field(default_factory=AnomalyConfig)
    tax_check: TaxCheckConfig = Field(default_factory=TaxCheckConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    alert: AlertConfig = Field(default_factory=AlertConfig)
    push: PushConfig = Field(default_factory=PushConfig)
    subscription: SubscriptionConfig = Field(default_factory=SubscriptionConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    onebot: OneBotConfig = Field(default_factory=OneBotConfig)

    def model_dump_safe(self) -> Dict[str, Any]:
        """导出为前端可安全展示的字典。

        敏感字段（API Key / Access Token）不会出现在结果中，只以布尔状态披露，
        避免密钥经由 ``/api/settings`` 泄漏到浏览器或审计日志。
        """
        data = self.model_dump()
        for path_key in SENSITIVE_DISPLAY_FIELDS:
            group, field = path_key.split(".", 1)
            group_data = data.get(group)
            if isinstance(group_data, dict) and field in group_data:
                group_data[field] = "******" if group_data[field] else ""
        if isinstance(data.get("llm"), dict):
            data["llm"]["api_key"] = "******" if self.llm.api_key else ""
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.llm.api_key_env or ""):
                data["llm"]["api_key_env"] = "LLM_API_KEY"
        return data


# ------------------------------------------------------------------------------
# 前端设置页允许修改的字段白名单
# ------------------------------------------------------------------------------
# 安全考虑：只允许前端修改这些路径，避免误改数据库路径、时区等关键项导致服务不可用。
# 格式为 "分组.字段"
EDITABLE_FIELDS: set[str] = {
    "app.subtitle",
    "app.debug",
    "server.port",
    "server.show_welcome",
    "llm.enabled",
    "llm.base_url",
    "llm.model",
    "llm.temperature",
    "llm.max_tokens",
    "ledger.currency_symbol",
    "ledger.default_period",
    "ledger.large_amount_threshold",
    "collector.allow_custom_category",
    "collector.quick_amounts",
    "collector.memo_required",
    "anomaly.enabled",
    "anomaly.method",
    "anomaly.zscore_threshold",
    "anomaly.min_samples",
    "tax_check.enabled",
    "tax_check.taxpayer_type",
    "tax_check.vat_rate_small_scale",
    "tax_check.vat_rate_general",
    "tax_check.vat_threshold_monthly",
    "tax_check.tax_burden_low",
    "tax_check.tax_burden_high",
    "tax_check.category_concentration_warn",
    "budget.enabled",
    "budget.warn_ratio",
    "alert.enabled",
    "alert.daily_expense_limit",
    "alert.low_balance_threshold",
    "push.enabled",
    "push.target_type",
    "push.target_id",
    "push.schedule_times",
    "push.send_image",
    "push.send_summary_text",
    "push.report_period",
    "push.image_width",
    "push.image_scale",
    "push.image_max_height",
    "push.content_type",
    "push.ai_summary",
    "push.ai_temperature",
    "push.ai_max_tokens",
    "subscription.enabled",
    "subscription.run_day",
    "subscription.run_time",
    "ui.theme",
    "ui.accent_color",
    "ui.particle_enabled",
    "ui.report_animation",
    "ui.counter_duration",
    "ui.page_size",
    "rag.enabled",
    "rag.top_k",
    "rag.score_threshold",
    "audit.enabled",
    "audit.track_adoption",
    # ---- 鉴权：只开放「策略类」字段 ----
    # 刻意**不开放** password / password_hash / password_salt / secret_key：
    # 这几个字段不允许通过 /api/settings 通用接口修改。
    # 原因：通用接口只做白名单过滤，不做强度校验，会把弱口令直接写进配置；
    # 改口令必须走 /api/auth/change-password（校验原口令 + 强度 + 恒定时间比对）。
    # 而 secret_key 若被前端覆盖，会导致所有会话莫名失效。
    "auth.enabled",
    "auth.username",
    "auth.session_hours",
    "auth.max_attempts",
    "auth.lockout_minutes",
    "auth.attempt_window_minutes",
    "auth.require_strong_password",
    "auth.min_password_length",
    "auth.allow_default_password",
}

# 前端设置页中标记为「只读」的敏感字段（会返回给前端展示状态，但不接受写入）
SENSITIVE_DISPLAY_FIELDS: set[str] = {
    "llm.api_key",
    "onebot.access_token",
    # 鉴权相关：这些值**绝不能**出现在 /api/settings 的响应里。
    # password 是明文口令；hash/salt 泄漏虽不能直接反推口令，
    # 但给了离线爆破的素材；secret_key 泄漏等同于可以伪造任意登录令牌。
    "auth.password",
    "auth.password_hash",
    "auth.password_salt",
    "auth.secret_key",
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并两个字典，override 中的值优先"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def read_yaml_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """读取 config.yaml，文件不存在时返回空字典"""
    target = path or CONFIG_PATH
    if not target.exists():
        return {}
    with target.open("r", encoding="utf-8") as fp:
        loaded = yaml.safe_load(fp) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"config.yaml 顶层必须是字典结构，实际为 {type(loaded).__name__}")
    return loaded


def write_yaml_config(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    """将配置写回 config.yaml，保留中文可读性"""
    target = path or CONFIG_PATH
    with target.open("w", encoding="utf-8") as fp:
        yaml.safe_dump(
            data,
            fp,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
        )


# 已被 firefly 接管的 OneBot 变量：先从环境里读出来留档，再从 os.environ 删除，
# 避免 NoneBot 的 pydantic-settings 二次解析（见上）。
_TAKEOVER_ENV_KEYS = ("SUPERUSERS", "ONEBOT_ACCESS_TOKEN", "ONEBOT_WS_URL", "ONEBOT_WS_PATH")
_takeover_values: Dict[str, str] = {}


def _load_env() -> None:
    """加载 .env 文件（若存在）。

    注意：NoneBot 自己会用 pydantic-settings 读取名为 ``SUPERUSERS`` 等大写环境变量，
    且期望它们是 **JSON 格式**（如 ``["123456"]``）。若把 .env 模板里那种
    ``SUPERUSERS=``（空值）或 ``SUPERUSERS=123,456``（逗号分隔）原样留在环境变量里，
    nonebot.init() 会抛 ``SettingsError: error parsing env var "superusers"``。

    因此这里先把本项目自己消费的这些 OneBot 变量读进来留档，再从 os.environ 中删除，
    由 firefly 自己解析后通过 nonebot.init(superusers=...) 显式传入。
    """
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=ENV_PATH, override=False)

    for key in _TAKEOVER_ENV_KEYS:
        value = os.environ.pop(key, None)
        if value is not None:
            _takeover_values[key] = value


def _takeover(key: str, default: str = "") -> str:
    """读取被 firefly 接管的 OneBot 环境变量（已从 os.environ 摘除）。"""
    return _takeover_values.get(key) or default


def _build_onebot_config() -> OneBotConfig:
    """从被接管的 .env 变量构建 OneBot 连接配置。

    支持 ``SUPERUSERS=123,456`` 与 ``SUPERUSERS=["123","456"]`` 两种写法：
    后者是 NoneBot 原生要求的 JSON 格式，用户照文档写就也能跑通。
    """
    superusers_raw = _takeover("SUPERUSERS").strip()
    superusers: List[str] = []

    if superusers_raw:
        if superusers_raw.startswith("["):
            # NoneBot 原生 JSON 写法
            try:
                parsed = json.loads(superusers_raw)
                if isinstance(parsed, list):
                    superusers = [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                superusers = []
        if not superusers:
            # 逗号分隔写法
            superusers = [s.strip() for s in superusers_raw.split(",") if s.strip()]

    return OneBotConfig(
        access_token=_takeover("ONEBOT_ACCESS_TOKEN"),
        ws_url=_takeover("ONEBOT_WS_URL"),
        ws_path=_takeover("ONEBOT_WS_PATH", "/onebot/v11/ws") or "/onebot/v11/ws",
        superusers=superusers,
    )


def load_config(reload: bool = False) -> Config:
    """加载并校验全局配置

    Args:
        reload: 是否强制重新读取（跳过缓存）

    Returns:
        校验通过的 Config 对象
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not reload:
        return _CONFIG_CACHE

    _load_env()
    raw = read_yaml_config()

    # 环境变量覆盖数据库地址（.env 优先于 config.yaml）
    env_db_url = os.getenv("DATABASE_URL")
    if env_db_url:
        raw = _deep_merge(raw, {"database": {"url": env_db_url}})

    config = Config.model_validate(raw)

    # Environment → YAML api_key → legacy key accidentally stored in api_key_env.
    env_name = (config.llm.api_key_env or "LLM_API_KEY").strip()
    valid_env_name = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name))
    legacy_key = env_name if not valid_env_name and env_name.startswith(("sk-", "AIza")) else ""
    config.llm.api_key_env = env_name if valid_env_name else "LLM_API_KEY"
    config.llm.api_key = (os.getenv(config.llm.api_key_env) or os.getenv("LLM_API_KEY") or config.llm.api_key or legacy_key).strip()
    if os.getenv("LLM_BASE_URL"):
        config.llm.base_url = os.getenv("LLM_BASE_URL", "") or config.llm.base_url
    if os.getenv("LLM_MODEL"):
        config.llm.model = os.getenv("LLM_MODEL", "") or config.llm.model

    config.onebot = _build_onebot_config()

    _CONFIG_CACHE = config
    return config


_CONFIG_CACHE: Optional[Config] = None


def get_config() -> Config:
    """获取全局配置（带缓存）"""
    return load_config(reload=False)


SYSTEM_MANAGED_FIELDS: set[str] = {
    # 这些字段由程序自动维护，**不允许用户经 /api/settings 修改**，
    # 但系统自身（ensure_auth_config / reset_password）必须能写。
    # 把两者分开，是为了让「谁有权改这个字段」这件事在代码层面显式可见。
    "auth.password",
    "auth.password_hash",
    "auth.password_salt",
    "auth.secret_key",
}
"""系统内部维护的字段，绕过 :data:`EDITABLE_FIELDS` 白名单，但仍可被系统写入。"""


def update_config(patch: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    """按白名单更新配置并写回 config.yaml

    Args:
        patch: 支持两种写法，混用也可以 ——
            - 扁平点号：``{"ui.counter_duration": 1500}``（前端设置页默认走这个）
            - 嵌套字典：``{"ui": {"counter_duration": 1500}}``
        allow_system: 仅限内部调用。为 ``True`` 时才允许写
            :data:`SYSTEM_MANAGED_FIELDS`（口令哈希、盐、令牌密钥）。
            **从 API 进来的请求必须保持默认的 False**，
            否则攻击者可以通过 ``/api/settings`` 覆盖 ``secret_key`` 伪造登录态。

    Returns:
        {"updated": [...], "rejected": [...]} 更新与被拒绝的字段列表
    """
    raw = read_yaml_config()
    updated: List[str] = []
    rejected: List[str] = []

    # 本次调用是否允许写系统字段。默认**不允许** —— 这样从
    # ``/api/settings`` 进来的任何请求都无法触碰它们，
    # 无论它用的是扁平写法还是嵌套写法。系统自身通过
    # ``update_config(patch, allow_system=True)`` 显式开口。
    #
    # 踩过的坑：最初只在扁平分支判白名单，嵌套写法 {"auth": {"secret_key": ...}}
    # 会绕过检查直接写进去，实测真的把 secret_key 覆盖成了攻击者指定的值。
    # 教训是「白名单校验必须覆盖所有输入语法形态」，漏一种就是敞口。
    allow_system = bool(kwargs.get("allow_system"))

    def _apply(path_key: str, value: Any) -> None:
        """校验并写入单个字段"""
        if path_key in SYSTEM_MANAGED_FIELDS and not allow_system:
            rejected.append(path_key)
            return
        if path_key not in EDITABLE_FIELDS and path_key not in SYSTEM_MANAGED_FIELDS:
            rejected.append(path_key)
            return
        group, _, field = path_key.partition(".")
        raw.setdefault(group, {})
        if not isinstance(raw[group], dict):
            raw[group] = {}
        raw[group][field] = value
        updated.append(path_key)

    for key, value in patch.items():
        if isinstance(value, dict):
            # 嵌套写法：key 是分组名，递归下钻到叶子字段。
            # 这里不提前拒绝整个分组 —— 分组里可能既有可编辑字段
            # 也有系统字段（如 auth），要交给 _apply 逐个判定，
            # 否则「只改 auth.enabled」会被整组拒掉。
            for field, sub_value in value.items():
                _apply(f"{key}.{field}", sub_value)
        else:
            # 扁平写法：key 本身就是 "分组.字段"
            _apply(key, value)

    if updated:
        write_yaml_config(raw)
        load_config(reload=True)

    return {"updated": updated, "rejected": rejected}


def get_editable_schema() -> Dict[str, Any]:
    """返回前端「设置」页需要的字段元信息（当前值 + 是否可编辑）"""
    config = get_config()
    data = config.model_dump()

    groups: Dict[str, Any] = {}
    for path_key in sorted(EDITABLE_FIELDS):
        group, field = path_key.split(".", 1)
        group_value = data.get(group, {})
        if not isinstance(group_value, dict) or field not in group_value:
            continue
        groups.setdefault(group, {})[field] = group_value[field]

    return {
        "editable": groups,
        "readonly": {
            "llm_api_key_configured": bool(config.llm.api_key),
            "llm_ready": config.llm.is_ready,
            # 「协议端是否已配置」不能用 ws_url 判断：
            # 本项目走反向 WS，ws_url 本来就该留空，
            # 拿它判断会把正确配置误报成「未配置」。
            # 真正的判据是「反向 WS 的监听路径 + 是否设了令牌」。
            "onebot_connected_config": bool(config.onebot.ws_path),
            "onebot_mode": "forward" if config.onebot.ws_url else "reverse",
            "onebot_ws_path": config.onebot.ws_path,
        },
    }


def ensure_dirs() -> None:
    """确保运行时目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "chroma").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "reports").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "exports").mkdir(parents=True, exist_ok=True)


__all__ = [
    "BASE_DIR",
    "CONFIG_PATH",
    "ENV_PATH",
    "DATA_DIR",
    "INDEX_DIR",
    "EDITABLE_FIELDS",
    "Config",
    "load_config",
    "get_config",
    "update_config",
    "get_editable_schema",
    "read_yaml_config",
    "write_yaml_config",
    "ensure_dirs",
]
