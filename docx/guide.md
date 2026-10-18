# 流萤 Firefly · 部署与开发参考

本文保留详细架构、部署与排查资料；当前版本与配置约定以 README 和 api.md 为准。

## 部署与启动

### 一键启动脚本详解

项目根目录提供 `start.sh`（macOS / Linux）与 `start.cmd`（Windows）。
实测暴露过的三个问题与修法见 [debug.md](./docx/debug.md)（Python 探测漏 conda、
版本上界缺失、虚拟环境名与源码目录同名互相覆盖）。

| 步骤 | 具体行为 |
| --- | --- |
| **Python 探测** | 按 `3.11 → 3.12 → 3.10 → 其它` 的顺序查找，并校验版本落在 **3.10 ~ 3.12** 区间内。<br>PATH 找不到时会继续扫描 `~/miniforge3`、`~/miniconda3`、`~/anaconda3`、`/opt/homebrew`、`/usr/local` 及其 `envs/*` 子环境 |
| **虚拟环境** | 在项目根创建 `firefly_env/`。已存在则跳过创建 |
| **依赖安装** | 用 `requirements.txt` 的 SHA-256 做标记，**文件未改动就跳过整个安装**；<br>优先走清华镜像，失败自动回退官方源；<br>额外用 `import nonebot, fastapi, sqlmodel, uvicorn` 兜底校验，防止依赖被手动删过 |
| **配置检查** | `.env` 缺失时从 `.env.example` 复制；<br>自动注释掉会导致启动崩溃的空 `SUPERUSERS=` |
| **启动服务** | 先检查端口 `2525`，被占用时打印占用进程 PID；<br>然后前台启动并输出访问地址 |

> ⚠️ **虚拟环境目录名不能叫 `firefly`** —— 项目自身的 Python 包目录就是 `./firefly/`，
> 同名会**覆盖源码目录**且报错是 `No module named firefly.app`，很难定位。
> 脚本已固定使用 `firefly_env` 并在启动前做了同名冲突检测，请勿手动改名。

### Conda 部署（推荐）

```bash
conda create -n firefly python=3.11 -y
conda activate firefly
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
playwright install chromium      # 图片推送必需；仅用网页与 API 可跳过
cp .env.example .env             # 填入 LLM_API_KEY
python -m firefly.app
```

### venv 部署

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
python -m firefly.app
```

### 生产环境部署

**反向代理（Nginx）** —— 注意必须放行 WebSocket 升级头（协议端连接必需）：

```nginx
server {
    listen 80;
    server_name firefly.example.com;
    location / {
        proxy_pass http://127.0.0.1:2525;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_ip;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_proto;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**后台常驻**：

```bash
nohup python -m firefly.app > firefly.log 2>&1 &
# 或 systemd：WorkingDirectory=/opt/firefly_bot，ExecStart=.venv/bin/python -m firefly.app
```

**更换为 PostgreSQL**：`pip install asyncpg` 后改 `.env` 的
`DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/firefly`，
重启后自动建表，无需手动迁移。

---

## 项目简介

**流萤 Firefly** 是一个面向**小微企业**的智能财税助手，把传统的「个人记账」升级为
「记账 → 分析 → 优化 → 推送」的完整闭环。

| 困境 | 方案 |
| --- | --- |
| **记账门槛高** | 自然语言记账（「昨天打车 38」）、账单 CSV/截图批量导入、周期订阅自动入账 |
| **数据变不成洞察** | 自定义周期报告，涵盖收支结构、储蓄率、异常识别、归因瀑布图、预算执行 |
| **税务风险看不见** | 8 项税务自检（每条附政策依据）+ 按优先级排序的优化行动项 |
| **结论送不到手上** | 同一份数据同时渲染到网页（动画）与聊天窗口（静态长图，定时推送） |

**核心亮点**：

- **一份数据源，两种呈现**：报告 payload 是唯一真值，网页动画与推送图片消费同一份数据；
- **建议采纳率**：系统记录每一条 AI 建议及用户是否采纳，为论文提供可量化的硬指标；
- **可解释优先**：异常检测给出命中的关键词，税务自检给出政策原文依据，而非黑盒结论；
- **零构建前端**：原生 HTML/CSS/JS，答辩现场一条命令即可运行，无 npm 依赖风险。

> 完整的功能清单、模块明细与论文实验设计见 [version.md](./docx/version.md)。

---

## 架构设计

### 分层结构与依赖方向

```
┌──────────────────────────────────────────────────────────────────────────┐
│  接入层                                                                   │
│   index/  原生 HTML/CSS/JS          plugins/chat.py   OneBot v11 对话     │
│   （浏览器，动画渲染）               （QQ 群 / 私聊）                      │
└────────────┬─────────────────────────────────────┬───────────────────────┘
             │ HTTP /api/*                         │ 消息事件
             ▼                                     ▼
┌────────────────────────────┐        ┌───────────────────────────────────┐
│  api/     FastAPI 路由层    │        │  scheduler/tasks.py  APScheduler  │
│  参数校验 · 响应包装 · 依赖注入│        │  报告推送 / 订阅入账 / 阈值预警    │
└────────────┬───────────────┘        └───────────────────┬───────────────┘
             │                                            │
             └──────────────┬─────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  services/   业务逻辑层（纯 Python，不 import FastAPI / NoneBot）          │
│   ledger 记账底座 · report ★ 报告编排 · taxcheck 8 项税检 · anomaly 异常检测│
│   attribution 归因瀑布 · budget 预算 · audit ★ 建议采纳率 · importer 导入解析│
│   subscription 周期订阅自动入账                                            │
└────────────┬──────────────────────────────────────┬──────────────────────┘
             ▼                                      ▼
┌────────────────────────────┐        ┌───────────────────────────────────┐
│  ai/    AI 能力层           │        │  db/   数据访问层                  │
│   llm OpenAI 兼容客户端      │       │   models.py  SQLModel 表定义       │
│   natural 自然语言记账       │       │   session.py 异步引擎 / 会话管理   │
│   classifier 智能分类        │       │   → SQLite (aiosqlite)            │
│   assistant 对话式查询        │       └───────────────────────────────────┘
└────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  render/   输出层                                                         │
│   html.py  报告 HTML 模板  →  image.py  HTML→PNG (Playwright)  →          │
│   bot.py   OneBot v11 发送  →  push.py   推送编排                         │
└──────────────────────────────────────────────────────────────────────────┘
```

**依赖方向严格单向**：`接入层 → services → db / ai`，`services` 永不反向依赖 `api` 或 `plugins`。

### 关键设计决策与理由

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| **驱动器** | NoneBot2 + **FastAPI** | NoneBot 的 FastAPI driver 本身就是标准 ASGI app，可直接 `app.mount("/", StaticFiles(...))` 把前端挂到根路径，实现**前后端同端口**：无 CORS 配置、无第二个进程、演示时只有一个地址 |
| **协议** | **OneBot v11** | 生态最成熟，NapCat / Lagrange / go-cqhttp 都支持，且反向 WS 模式无需公网 IP |
| **数据库** | **SQLite + aiosqlite** | 毕设场景单机部署，SQLite 零运维、便于打包演示；SQLModel 让换 PostgreSQL 只需改 `database.url` 一行 |
| **ORM** | **SQLModel** | 与 FastAPI 同作者、与 Pydantic 同源，模型定义即校验规则，避免 ORM 与 schema 两套定义 |
| **LLM 接入** | **OpenAI 兼容协议** | 只依赖一个事实标准，改 `base_url` 即可在 DeepSeek / 通义 / 智谱 / Ollama 之间切换，代码零改动 |
| **前端** | **原生 HTML/CSS/JS** | 不引入构建工具链。答辩现场 `python -m firefly.app` 一条命令就跑，不存在 `node_modules` 装不上、产物没构建的风险 |
| **图表** | **Canvas 2D 自绘** | 避免引入 ECharts 等大体积库；自绘还能精确控制二次元风格的配色与动画节奏 |
| **定时任务** | **APScheduler** | 与 asyncio 原生集成，`replace_existing=True` 支持配置热更新后重新排程 |
| **图片渲染** | **htmlrender + Playwright** | 复用同一套 HTML 模板出图，样式与网页端一致，避免用 Pillow 手写排版 |

**刻意不做的三件事**：

- ❌ **本地部署大模型** —— 本机 M1 16G 跑 7B 量化模型，抽取准确率撑不住叙事，且现场首次加载慢；走 API 更快更准；
- ❌ **裸 NL2SQL** —— 让模型直接写 SQL 既不可控也不可解释。本项目用**函数调用**：模型只负责抽取意图与参数，实际查询由预先写好的服务函数执行，安全且可审计；
- ❌ **前端引入构建工具链** —— 见上表。

### 数据模型

13 张表，统一 `ff_` 前缀：

```
User ──┬── Account      资金账户（现金/银行卡/微信…）
       ├── Category     记账类别 ── tax_account ──→ 税务口径科目 ★
       ├── Transaction  流水（金额恒为正，方向由 direction 表达）★
       ├── Subscription 周期订阅（房租/宽带…自动入账）
       ├── Budget       预算（category_id 为空 = 总预算）
       ├── Report       报告（payload JSON 为唯一数据源）★
       ├── Suggestion   AI 建议（state 记录是否被采纳）★
       ├── AuditLog     审计日志（谁在什么渠道做了什么）
       ├── ImportBatch  导入批次记录
       └── SystemState  系统状态（如推送去重标记）
Report ── TaxCheckResult   税检结果（关联报告）
PolicyDoc                  政策知识库（供 RAG 使用）
```

**三个关键建模决策**：

**① `Transaction.amount` 恒为正数，方向由 `direction` 字段表达** ——
不采用「支出记负数」。聚合时反复出现「这个 sum 该不该取绝对值」的判断极易出错；
方向独立成枚举后，`sum(amount) where direction=expense` 语义明确。

**② `Category.tax_account` 把记账类别直接挂到税务口径** ——
税务自检可以直接 `join Category` 汇总「可税前扣除的支出」，
**不需要在业务代码里维护映射表**：

```python
{"name": "餐饮", "tax_account": "业务招待费", "deductible": True}
{"name": "交通", "tax_account": "差旅费",     "deductible": True}
{"name": "医疗", "tax_account": "",           "deductible": False}
```

**③ `Suggestion.state` 是「建议采纳率」的唯一来源** ——
状态机为 `pending → adopted / rejected / ignored`。超时未处理的标记为 `ignored`
并**单独披露**，避免把「用户没看见」算成「用户不接受」而导致指标失真。

### 报告数据契约

`GET /api/reports/{id}` 返回的 `payload` 是前端与出图层共同消费的契约：

```
payload
├── meta           周期标识、起止时间、生成时间、币种
├── overview       收支总览（income / expense / net / net_assets / balance /
│                  savings_rate / avg_daily_expense / compare.* 环比）
├── structure      支出 / 收入结构（分类占比、TOP 类别、TOP 对手方）
├── accounts[]     账户明细 {id, name, kind, balance, include_in_net}
├── trend          趋势（日趋势 / 近 6 期月度趋势）
├── anomaly        异常统计 {scanned_count, anomaly_count, personal_mixed_count, ...}
├── attribution    归因瀑布图（waterfall / contributions / delta_total / summary）
├── budget         预算执行 {total_budget, total_actual, items[], over_count}
├── tax            税务自检（checks[] 每项含 policy_basis ★、highest_level、optimizations[]）
├── optimization   优化方向（action_items[] 按 priority 排序）
└── highlights[]   前端顶部高亮卡 {label, value, text, tone}
```

> ⚠️ **契约变更须知**：`payload` 结构同时被 `index/js/report.js` 与
> `firefly/render/html.py` 消费。修改字段名必须**两处同步**，否则会出现
> 「网页正常但推送图片空白」这类静默故障。
> 前端 `index/js/views.js` 的 `normalizeReportPayload()` 做了一层兼容映射，
> 但那是防御手段，不应作为随意改字段的理由。

### 请求生命周期

以「生成一份报告」为例：

```
1. 浏览器  POST /api/transactions        （用户记一笔账）
      └─ 未指定类别 → 按备注匹配类别名
      ├─ ledger.find_duplicates()    金额+时间窗口+对手方模糊匹配
      ├─ anomaly.analyze_transaction()  z-score + 个人消费关键词
      └─ audit.log_action()
      └─ db/session.py get_session() 依赖 → 请求结束自动 commit ★

2. 浏览器  POST /api/reports/generate    {"period": "monthly"}
      └─ api/report.py → services/report.generate_report()
           ├─ ledger.period_bounds()          算出本期与上期区间
           ├─ sum_by_direction / aggregate_*  收支与分类聚合
           ├─ anomaly.scan_period()           全期异常扫描
           ├─ attribution.build_attribution() 归因瀑布图
           ├─ budget.get_budget_status()      预算执行
           ├─ taxcheck.run_tax_check()        8 项税检
           ├─ _build_action_items()           汇成优化行动项
           ├─ _build_highlights()             顶部高亮卡
           └─ 落库 Report（payload + elapsed_ms）
      └─ 前端 report.js 按 payload 逐块播放动画
      └─ 出图层 render/html.py 消费同一 payload → PNG
```

**关键点**：`db/session.py` 的 `get_session()` 在 `yield` 之后**显式 commit**，
异常时 rollback。若只写 `yield session`，所有写操作会在请求结束时被静默回滚 ——
接口返回 200 而库里什么都没有。

---

## 配置说明

项目采用**三级配置优先级**，后者覆盖前者：

```
真实环境变量  >  .env 文件  >  config.yaml 默认值
```

### `config.yaml` —— 全局设置（前端可改）

| 配置组 | 关键字段 | 说明 |
| --- | --- | --- |
| `app` | `name` `subtitle` `version` `debug` `timezone` | 应用元信息；**`debug` 为演示模式开关** |
| `server` | `host` `port` `show_welcome` | **端口默认 2525**，改这里即可换端口 |
| `database` | `url` `echo` | 默认 SQLite，换 PostgreSQL 只需改这一行 |
| `llm` | `enabled` `base_url` `model` `temperature` | **OpenAI 兼容协议**，可切 DeepSeek / 通义 / 智谱 / Ollama |
| `ledger` | `currency` `default_period` `custom_period_days` | 币种、默认周期、自定义周期候选值 |
| `anomaly` | `method` `zscore_threshold` `min_samples` | 异常检测灵敏度 |
| `tax_check` | `taxpayer_type` `vat_threshold_monthly` `tax_burden_low/high` | **税务口径**，直接影响自检结论 |
| `budget` | `warn_ratio` `over_ratio` | 预算预警与超支阈值 |
| `alert` | `daily_expense_limit` `low_balance_threshold` | 主动预警触发线 |
| `push` | `enabled` `target_type` `target_id` `schedule_times` `content_type` | **推送**目标、时间、内容形态 |
| `auth` | `enabled` `username` `session_hours` `max_attempts` | **登录鉴权** |
| `ui` | `theme` `accent_color` `particle_enabled` | 前端主题与动画 |

### 切换 LLM 供应商

只需修改 `config.yaml` 的 `llm.base_url` 与 `llm.model`，代码无需改动：

| 供应商 | `base_url` | `model` 示例 |
| --- | --- | --- |
| DeepSeek（默认） | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 阿里通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` |

### 登录鉴权

自带登录保护，**默认开启**（`auth.enabled: true`），默认账号 `admin` /
口令 `Firefly2026`。

**安全设计要点**（答辩可讲）：

- **令牌存 HttpOnly Cookie**，前端 JS 完全读不到 —— 即使发生 XSS 也偷不走登录态。
  同时兼容 `Authorization: Bearer <token>`，方便 curl / 脚本调用。
- **改口令必须验证原口令**。若只凭登录态就能改口令，一旦令牌被窃取，
  攻击者可以直接改掉口令把主人锁在门外。
- **登录失败提示不区分「用户不存在」与「口令错误」**，避免账号枚举。
- **口令比对用 `hmac.compare_digest` 恒定时间比较**，防止通过响应时间逐字节爆破哈希。
- **`password_hash` / `password_salt` / `secret_key` 不允许经 `/api/settings` 写入**。
  这里曾经有过一个真实漏洞：早期只对「扁平写法」做白名单校验，而「嵌套写法」
  会绕过检查直接写入，实测可伪造任意登录令牌。修法是让校验覆盖所有输入语法形态 ——
  **白名单漏一种写法就等于没有白名单**。

#### 修改口令

1. **前端「设置」页 → 修改登录口令**：需填原口令，带实时强度提示。
2. **直接编辑 `config.yaml`**：改 `auth.password` 后**删除 `password_hash` 与
   `password_salt`** 两行，重启服务会自动按新口令重建哈希。

> ⚠️ 只改 `auth.password` 而不删哈希是**无效**的 —— 登录只比对哈希。

### `.env` —— 敏感值（不进版本库）

```ini
LLM_API_KEY=sk-xxx          # 大模型密钥（留空则降级为规则引擎）
LLM_BASE_URL=               # 可选：覆盖供应商地址
LLM_MODEL=                  # 可选：覆盖模型名
ONEBOT_ACCESS_TOKEN=        # 协议端鉴权令牌
ONEBOT_WS_URL=              # 正向 WS 地址（留空则用反向 WS）
SUPERUSERS=                 # 超级用户 QQ 号，逗号分隔
```

---

## 项目结构

```
firefly_bot/
├── start.sh                 # ★ 一键启动（macOS / Linux）
├── start.cmd                # ★ 一键启动（Windows）
├── config.yaml              # 全局配置（前端可改）
├── .env                     # 敏感配置（不进版本库）
├── .env.example             # 敏感配置模板
├── requirements.txt         # Python 依赖清单
├── LICENSE                  # MIT
├── docx/                    # 文档（接口 / 版本功能 / 修复记录）
├── todo.md                  # 架构设计与路线图
│
├── firefly_env/             # 虚拟环境（由启动脚本创建，不进版本库）
│
├── firefly/                 # 后端主包
│   ├── app.py               # ★ 入口：初始化 NoneBot、挂载前端、打印横幅
│   ├── config.py            # 配置加载 / 校验 / 热更新
│   │
│   ├── db/                  # 数据层
│   │   ├── models.py        #   SQLModel 表定义（13 张表）
│   │   └── session.py       #   异步引擎与会话管理
│   │
│   ├── api/                 # Web API 层（FastAPI，71 个路由，详见 docx/api.md）
│   │   ├── router.py        #   路由聚合 + 免登录白名单
│   │   ├── deps.py          #   依赖注入（当前用户、统一响应体、MAX_AMOUNT）
│   │   ├── auth.py          #   登录 / 登出 / 改口令
│   │   ├── meta.py          #   元信息与健康检查
│   │   ├── transaction.py   #   流水增删改查
│   │   ├── category.py      #   类别管理
│   │   ├── account.py       #   资金账户（账本）
│   │   ├── stats.py         #   统计聚合
│   │   ├── report.py        #   报告生成与归档
│   │   ├── taxcheck.py      #   税务自检
│   │   ├── budget.py        #   预算
│   │   ├── ai.py            #   自然语言记账与对话
│   │   ├── audit.py         #   审计日志与建议采纳率
│   │   ├── push.py          #   推送控制
│   │   ├── subscription.py  #   周期订阅
│   │   ├── settings.py      #   配置读写
│   │   ├── importer.py      #   CSV / OCR 账单导入
│   │   └── backup.py        #   备份与还原
│   │
│   ├── services/            # 业务逻辑层（不含 Web 框架依赖，Web 与 Bot 共用）
│   │   ├── ledger.py        #   记账底座、周期计算、聚合
│   │   ├── report.py        #   ★ 报告编排（产出唯一数据源 payload）
│   │   ├── taxcheck.py      #   8 项税务自检 + 政策依据
│   │   ├── anomaly.py       #   异常检测与个人消费识别
│   │   ├── attribution.py   #   归因分析（瀑布图）
│   │   ├── budget.py        #   预算执行
│   │   ├── audit.py         #   ★ 建议采纳率统计
│   │   ├── subscription.py  #   周期订阅自动入账
│   │   ├── importer.py      #   账单解析与去重
│   │   ├── backup.py        #   全库备份与还原
│   │   └── auth.py          #   口令哈希 / 令牌 / 锁定策略
│   │
│   ├── ai/                  # AI 能力层
│   │   ├── llm.py           #   OpenAI 兼容客户端（缓存 / JSON 修复）
│   │   ├── natural.py       #   自然语言记账（LLM 优先 + 规则降级）
│   │   ├── classifier.py    #   智能分类
│   │   └── assistant.py     #   对话式查询（函数调用）
│   │
│   ├── render/              # 渲染与推送
│   │   ├── html.py          #   报告 HTML 模板
│   │   ├── image.py         #   HTML → PNG（Playwright）
│   │   ├── bot.py           #   OneBot v11 发送
│   │   └── push.py          #   推送编排
│   │
│   ├── scheduler/           # 定时任务（APScheduler）
│   │   └── tasks.py         #   报告推送 / 订阅入账 / 阈值预警
│   │
│   └── plugins/             # NoneBot 插件
│       └── chat.py          #   协议端对话（记账 / 报告 / 税检 / 查询）
│
├── index/                   # 前端（原生 HTML/CSS/JS，无构建工具，见 index/README.md）
│
├── data/                    # 运行时数据（不进版本库）
│   ├── firefly.db           #   SQLite 数据库
│   └── reports/             #   报告 HTML 与 PNG
│
└── tests/                   # 测试（98 项回归用例）
```

**架构要点**：`services/` 层完全不依赖 FastAPI，因此 Web 端与 Bot 端共享同一套业务逻辑，
不存在「网页能算、机器人算不出」的不一致。

---

## 协议端对接

本项目采用 **OneBot v11** 标准协议，可对接 [NapCat](https://github.com/NapNeko/NapCatQQ)、
[Lagrange.OneBot](https://github.com/LagrangeDev/Lagrange.Core)、[go-cqhttp](https://github.com/Mrs4s/go-cqhttp) 等实现。

### 反向 WebSocket（推荐）

由协议端主动连接本服务，无需在本项目配置地址：

1. 启动流萤，记下终端提示的 `http://127.0.0.1:2525/onebot/v11/ws`
2. 打开协议端配置，填入反向 WS 地址：

```json
{
  "reverse_ws": {
    "url": "ws://127.0.0.1:2525/onebot/v11/ws",
    "access_token": ""
  }
}
```

3. 在 `config.yaml` 中设置推送目标：

```yaml
push:
  enabled: true
  target_type: group      # group 群聊 / private 私聊
  target_id: "123456789"  # 群号或 QQ 号
  schedule_times: ["09:00"]
```

### 机器人命令

| 命令 | 别名 | 说明 |
| --- | --- | --- |
| `/记 <内容>` | `记账` `record` | 自然语言记账，例：`/记 昨天打车38` |
| `/报告 [周/月/季/年]` | `报表` `report` | 生成并推送报告图片 |
| `/税检` | `税务自检` `tax` | 执行税务自检清单 |
| `/明细` | `流水` `list` | 查看最近 10 笔流水 |
| `/预算` | `budget` | 查看预算执行情况 |
| `/帮助` | `help` `菜单` | 显示命令菜单 |

**免命令交互**：直接 @机器人 说话即可，既能记账也能提问：

```
昨天打车 38，请客户吃饭 260        → 自动识别为两笔支出并记账
上月餐饮花了多少？                  → 函数调用查询后回答
```

### 推送选项与出图清晰度

报告页的「📤 推送到协议端」按钮**先弹选项、再发送**：推送类型
（`text` / `image` / `both`）、AI 总结开关、温度、字数上限。定时推送则固定读取
`config.yaml` 的 `push.content_type` / `push.ai_summary`。

清晰度由 `push.image_width`（截图视口宽，默认 800）、`push.image_scale`
（设备像素比，默认 3）、`push.image_max_height`（缩放高度上限，默认 6000）
共同决定。一个反直觉但重要的结论：**高度上限是清晰度的主要瓶颈，而不是压缩算法** ——
报告整页约 7058px 高，若上限设成 2400，就等于把 14px 正文降采样到 4.8px。
实测 QQ 能正常接收 8000px 高的图，所以默认值放宽到了 6000。

> **规则摘要 + AI 解读，而不是让 AI 直接写摘要。** 数字由代码算、由代码排版，
> AI 只负责解释「为什么」和「怎么办」。让大模型复述金额，出现幻觉时读者
> 无法分辨哪句是真的 —— 财务场景下这个代价太大。

---

## 演示模式

`app.debug: true` 后前端**完全使用内置模拟数据**，不发任何业务请求，并且**跳过登录**。
每次刷新都会重新生成一组随机数据，顶栏会亮起「演示模式 · 模拟数据」徽标。
答辩现场网络异常或数据库为空时，把它打开即可稳定演示全部界面。

反之，`app.debug: false`（默认）时数据**一律来自后端**：接口失败就显示
错误提示，**绝不悄悄用假数据顶替** —— 否则用户会把自己记的账和演示数据混在
一起。这条约束由 `index/js/api.js` 的 `withFallback` 强制：
只有在演示模式下才允许返回模拟数据。

---

## 常见问题

<details>
<summary><b>安装时提示 <code>Could not find a version that satisfies nonebot2</code></b></summary>

Python 版本过低。NoneBot2 2.5 要求 `>=3.10,<4.0`。用 `python -V` 确认版本。
注意：在 3.9 上 pip 有时**不会报错**，而是静默降级到 nonebot2 2.4.4、pandas 2.x 等旧版本 ——
这种"装得上但版本不对"的情况更危险，请用 `pip list` 核对版本。
</details>

<details>
<summary><b>启动报错 <code>SettingsError: error parsing env var "superusers"</code></b></summary>

`.env` 里存在一个**空值**的 `SUPERUSERS=`。NoneBot 会在启动早期把它当作 JSON 数组解析，
空字符串会导致解析失败。**解决**：把该行注释掉，或填入真实 QQ 号（两种写法均可）：

```ini
# SUPERUSERS=
SUPERUSERS=123456789,987654321
# 或 NoneBot 原生 JSON 写法
# SUPERUSERS=["123456789","987654321"]
```
</details>

<details>
<summary><b>启动报错 <code>ValueError: NoneBot has not been initialized</code></b></summary>

这是**正常现象**，出现在单独导入 `nonebot_plugin_htmlrender` 或 `nonebot_plugin_apscheduler` 时。
它们是 NoneBot **插件**而非普通库，必须在 NoneBot 初始化后才能导入。
通过 `python -m firefly.app` 正常启动不会有此问题。
</details>

<details>
<summary><b>推送图片时报 Playwright 相关错误</b></summary>

未安装浏览器内核：`playwright install chromium`；Linux 若还缺系统库：
`playwright install-deps chromium`。
</details>

<details>
<summary><b>端口 2525 被占用</b></summary>

改 `config.yaml` 的 `server.port`，或前端「设置」页直接修改并保存。
</details>

<details>
<summary><b>终端被 ANSI 转义码刷屏，看不到访问地址</b></summary>

Console 适配器是交互式 TUI，会接管整个终端。本项目已默认**不启用**它，
仅在显式设置环境变量 `FIREFLY_ENABLE_CONSOLE_BOT=1` 且处于真实终端时才加载。
</details>

<details>
<summary><b>报告页数字全为 0</b></summary>

通常是数据库为空。先在前端「记账」页录入几笔流水，或使用 CSV 导入。
也可临时把 `config.yaml` 的 `app.debug` 设为 `true`，用模拟数据演示界面。
</details>

<details>
<summary><b>修改 config.yaml 后不生效</b></summary>

前端「设置」页保存会**立即生效**；手动编辑文件则需重启服务。
注意 YAML 对缩进敏感，缩进错位会导致配置解析失败。
</details>

<details>
<summary><b>浏览器控制台有一条 <code>401 Unauthorized</code>（<code>/api/auth/me</code>）</b></summary>

**这是预期行为，不是缺陷。** 未登录时前端需要确认「有没有有效会话」，
`/api/auth/me` 返回 `401` 正是这一判定的依据；浏览器会为任何非 2xx 响应
记一条 error，无法从 JS 侧消除。正确的验收方式是：**登录之后**再统计错误。
登录后遍历全部 6 个视图应为「0 JS 错误、0 个 HTTP ≥ 400」。
</details>

<details>
<summary><b>登录后顶栏看不到用户名 / 退出按钮</b></summary>

`index/js/auth.js` 的 `renderTopbarUser()` 依赖顶栏操作区的类名。
若以后调整 `index/index.html` 的类名（当前是 `.nav-actions`），
该函数找不到容器就会**静默返回**、按钮凭空消失。
函数里已保留 `.topbar-actions` 作为回退选择器，改版时请同步核对。
</details>

<details>
<summary><b>测试数据污染了正式库 / 报告数字对不上</b></summary>

测试脚本写入的流水与归档报告可能残留在正式库中，详见
[debug.md](./docx/debug.md) 第一节的完整分析与修法。
</details>

---

## 测试回归

```bash
python tests/test_api.py        # 期望：98 项通过 / 0 失败 / 0 跳过
```

覆盖 16 个分组：免鉴权接口 · 鉴权守卫 · 登录与身份 · 元信息与配置 · 类别管理 ·
流水 CRUD · 统计与看板 · 预算管理 · 订阅与周期记账 · 报告生成 · 税务自检 ·
AI 能力 · 账单导入 · 审计与建议采纳率 · 协议端推送 · 错误处理与健壮性。

---

## 技术栈

**后端**：NoneBot2 2.5 · FastAPI 0.143 · SQLModel 0.0.48 · SQLite/aiosqlite · APScheduler 3.11 ·
OpenAI SDK 3.29 · ChromaDB 1.5 · RapidFuzz · Pandas 3.0

**渲染**：nonebot-plugin-htmlrender 0.8 · Playwright 1.63 · Pillow

**前端**：原生 HTML5 / CSS3 / ES6+ · Canvas 2D 自绘图表 · **零构建工具、零 npm 依赖**

> 前端刻意不引入任何框架与打包器：答辩现场只需 `python -m firefly.app` 一条命令即可运行，
> 不存在 `node_modules` 装不上、构建产物缺失的风险。

---

## 加入社群
<img width="1284" height="2283" alt="微信图片_20261013231908_10_72" src="https://github.com/user-attachments/assets/9f12f4d7-0162-4fcc-98b8-2398ab4f1543" />

## 免责声明

本项目的税务自检结果依据公开政策规则自动生成，**仅供参考，不构成专业税务意见**。
实际申报请以主管税务机关的口径为准，并咨询专业税务顾问。

## 许可

MIT License · 仅用于学习与研究

<div align="center">

**流萤 Firefly** · 让每一笔账都有回响 ✨

</div>
