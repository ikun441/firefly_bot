# 流萤 Firefly · API 接口文档

> 当前版本：v1.0.2。本文档描述业务接口的调用规范、参数约定与注意事项。
> 交互式文档（Swagger UI）：`http://127.0.0.1:2525/docs`
> OpenAPI 描述：`http://127.0.0.1:2525/api/openapi.json`

---

## 1. 通用约定

### 1.1 Base URL 与路由前缀

- 开发/演示默认地址：`http://127.0.0.1:2525`
- **所有业务接口挂在 `/api` 前缀下**，完整清单以 OpenAPI 描述为准。

### 1.2 统一响应体

**成功**：

```json
{ "success": true, "data": { } }
```

列表接口额外带 `total` 字段：

```json
{ "success": true, "data": [ ], "total": 42 }
```

**失败**（业务失败仍返回 HTTP 200，由前端判断 `success`）：

```json
{ "success": false, "message": "错误描述", "code": 400, "detail": null }
```

### 1.3 ⚠️ 错误处理约定（重要）

| HTTP 状态码 | 含义 | 响应格式 |
| --- | --- | --- |
| `200` | 业务失败 | `{ "success": false, "message": "...", "code": 400 }` |
| `400` | 参数非法（代码显式抛出） | 信封格式，带 `message` |
| `401` | 未登录 / 登录过期 | `{"detail": "未登录或登录已过期，请重新登录"}` |
| `404` | 资源不存在 | 信封格式，带 `message` |
| `422` | Pydantic 字段校验失败 | FastAPI 标准格式 `{"detail": [{"loc": ["body", "amount"], "msg": "..."}]}` |
| `500` | 服务端异常 | 检查终端日志，堆栈会完整打印 |

> **排查建议**：接口返回 200 但业务失败时，务必检查响应体的 `success` 字段 ——
> 本项目所有业务异常都以 `success: false` 返回，而非依赖 HTTP 状态码。
> 这是刻意设计：业务规则错误（如预算为负数、导入内容为空）返回 200 + 信封，
> 只有**字段校验错误**才返回 422。

### 1.4 时间格式

- 交换统一用 `YYYY-MM-DD HH:MM` 或 `YYYY-MM-DD`（也接受 ISO 8601，含 `Z` 后缀）。
- 后端解析为带时区的 datetime 并以 **UTC** 存储，展示时按 `app.timezone`（默认 `Asia/Shanghai`）呈现。

### 1.5 ID 字段的宽松传参

所有 `category_id` / `account_id` / `budget_id` 等可选 ID 字段均声明为
`Optional[Union[int, str]]`，空串 `""` 会被归一化为 `None`。
原因：前端 `<select>` 的 value 永远是字符串，只收 `int` 会让选择被静默丢弃。
直接调 API 时传数字或数字字符串均可。

### 1.6 金额字段边界

- 单笔金额上限 `MAX_AMOUNT = 1e8`（1 亿元），**唯一来源**为 `firefly/api/deps.py`。
- 上界由 Pydantic 强制（`le=MAX_AMOUNT`），超出返回 **422**。
- 交易金额另有下界 `gt=0`；**预算 / 订阅的下界由 service 层业务校验拦截**，
  返回 200 + `success:false` 信封而非 422（见 1.3）。

---

## 2. 鉴权

### 2.1 认证方式

除白名单接口外，所有接口都需要登录态。两种携带方式：

1. **Cookie `firefly_token`** —— 浏览器默认方式，HttpOnly，JS 读不到（防 XSS 窃取）。
2. **`Authorization: Bearer <token>`** —— 给 curl / Postman / 机器人脚本用。

> 服务端**先查 Cookie 再查 Header**，避免浏览器里残留的旧 Header 覆盖新 Cookie。

### 2.2 免登录白名单（PUBLIC_PATHS）

| 路径 | 为什么必须公开 |
| --- | --- |
| `GET /api/health` | 容器探活与前端连通性检测，登录前就要能调 |
| `GET /api/bootstrap` | 前端必须在登录**前**知道 `app.debug`（演示模式），否则无法决定是否请求真实数据 |
| `GET /api/auth/status` | 登录页需要知道「本部署要不要登录」 |
| `POST /api/auth/login` | 登录本身 |
| `GET /api/openapi.json` | 文档描述，不含业务数据 |

> 故意不放进来的：`/api/meta`（含用户名与业务配置）、`/api/settings`（配置全貌）、`/api/audit/*`（操作痕迹）。

### 2.3 curl 调用示例

```bash
# 1. 登录取令牌
TOKEN=$(curl -s -X POST http://127.0.0.1:2525/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Firefly2026"}' | jq -r '.data.token')

# 2. 带令牌调用
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:2525/api/stats
```

---

## 3. 接口总览

### 登录鉴权（5）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/auth/status` | 鉴权是否启用、是否仍用默认口令、会话与锁定策略（免登录） |
| POST | `/api/auth/login` | 登录；成功写 HttpOnly Cookie 并返回令牌 |
| GET | `/api/auth/me` | 当前登录用户（未登录返回 401） |
| POST | `/api/auth/logout` | 登出；清除 Cookie |
| POST | `/api/auth/change-password` | 修改口令；**必须提供原口令**，成功后换发新令牌 |

### 元信息（3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查；探测数据库连通性，返回 `llm_ready`（免登录） |
| GET | `/api/bootstrap` | 登录前引导信息：应用名、版本、主题、演示模式开关（免登录） |
| GET | `/api/meta` | 应用元信息（名称、版本、端口、币种、UI 配置、当前用户） |

### 流水（6）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/transactions` | 列表；支持起止时间、类别、方向、关键词、待确认、异常过滤 |
| POST | `/api/transactions` | 新增；自动去重 + 异常检测 + 审计日志 |
| GET | `/api/transactions/{txn_id}` | 详情 |
| PATCH | `/api/transactions/{txn_id}` | 局部更新（只改传进来的字段） |
| DELETE | `/api/transactions/{txn_id}` | 删除 |
| POST | `/api/transactions/{txn_id}/confirm` | 确认待入账记录 |

### 类别（3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/categories` | 列表；`?direction=expense` 可按方向过滤 |
| POST | `/api/categories` | 新增（可带 `tax_account` 与 `deductible` 挂接税务口径） |
| DELETE | `/api/categories/{category_id}` | 删除；系统内置类别与已被引用的类别拒绝删除 |

### 资金账户 / 账本（6）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/accounts` | 账户列表，附带每个账户的当前余额与流水笔数 |
| POST | `/api/accounts` | 新增账户 |
| GET | `/api/accounts/{account_id}` | 详情 |
| PATCH | `/api/accounts/{account_id}` | 更新 |
| DELETE | `/api/accounts/{account_id}` | 删除（规则保守，见注意事项） |
| GET | `/api/accounts/{account_id}/transactions` | 某账户的流水 |

### 统计（3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/stats` | 总览聚合：收支、环比、分类结构、日趋势、多期趋势、账户、异常统计 |
| GET | `/api/anomalies` | 异常流水；`?mode=all\|anomaly\|personal` |
| GET | `/api/budget-status` | 预算执行状态 |

### 报告（7）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/reports/generate` | 生成报告（核心接口） |
| GET | `/api/reports/latest` | 最新一份已完成报告；`?period=monthly` 可按周期过滤 |
| GET | `/api/reports` | 归档列表（不含 payload，减小体积） |
| GET | `/api/reports/{report_id}` | 报告详情（含完整 payload） |
| GET | `/api/reports/{report_id}/html` | 返回报告 HTML（可直接在浏览器看） |
| GET | `/api/reports/{report_id}/preview` | 返回 HTML 文件路径与访问 URL |
| POST | `/api/reports/{report_id}/push` | 手动推送到协议端 |

### 税务自检（3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/tax-check` | 执行税检（可传周期 `period` / 起止 `start` `end`） |
| GET | `/api/tax-check/checks` | 历史税检结论列表；`limit` 默认 20（按时间倒序的明细行，非最近一次快照） |
| GET | `/api/tax-check/policy` | 政策依据库：全部检查项对应的政策原文引用 |

### 预算（3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/budgets` | 预算列表 + 执行摘要 |
| POST | `/api/budgets` | 新增预算（同范围重复创建会覆盖旧记录） |
| DELETE | `/api/budgets/{budget_id}` | 删除 |

### AI 能力（6）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/ai/status` | AI 引擎状态（是否就绪、模型名） |
| POST | `/api/ai/natural` | 自然语言记账：文本 → 结构化条目（两步式第一步） |
| POST | `/api/ai/nl-confirm` | 确认并入库上一步抽出的条目（第二步） |
| POST | `/api/ai/chat` | 对话式查询（函数调用） |
| POST | `/api/ai/classify` | 对备注做智能分类 |
| GET | `/api/ai/categories/suggest` | 类别自演化：建议新类别归属的税务口径 |

### 审计与建议（4）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/audit/stats` | 建议采纳率统计（论文指标来源） |
| GET | `/api/audit/logs` | 操作日志；可按 `action` / `channel` / `target_type` / `target_id` 过滤 |
| GET | `/api/audit/suggestions` | 建议列表；可按 `state` / `kind` 过滤 |
| POST | `/api/audit/suggestions/{id}/resolve` | 标记建议为已采纳 / 已拒绝 |

### 推送（3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/push/status` | 推送配置 + 协议端连接状态 |
| POST | `/api/push/test` | 发送测试消息 |
| POST | `/api/push/latest` | 推送最新报告 |

### 订阅（5）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/subscriptions` | 订阅列表 |
| POST | `/api/subscriptions` | 新增订阅（房租/宽带等周期自动入账） |
| DELETE | `/api/subscriptions/{sub_id}` | 删除 |
| GET | `/api/subscription/status` | 订阅调度状态与下次执行时间 |
| PUT | `/api/subscription/status` | 开关订阅调度（写回配置并重新排程） |

### 设置（3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/settings` | 读取当前配置（敏感字段脱敏为 `******`） |
| GET | `/api/settings/schema` | 可编辑字段白名单 + 只读字段说明 |
| PUT | `/api/settings` | 更新配置；**同时支持扁平点号键与嵌套对象** |

### 账单导入（4）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/import/csv` | CSV 账单导入（`multipart/form-data`）；`dry_run=true` 只解析不入库 |
| POST | `/api/import/ocr` | 截图账单 OCR 识别（默认 `auto_confirm=false`） |
| POST | `/api/import/reparse` | 直接解析一段账单文本（粘贴入口） |
| GET | `/api/import/batches` | 导入批次历史 |

### 备份与还原（7）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/backup/list` | 备份文件列表 |
| POST | `/api/backup/create` | 立即生成一份全库 JSON 备份 |
| GET | `/api/backup/download` | 下载备份文件（`?name=` 指定） |
| POST | `/api/backup/inspect` | 只解析上传的备份并返回摘要，**不写库**（还原前预览） |
| POST | `/api/backup/restore` | 从服务端已有备份还原；还原前自动做一次安全备份 |
| POST | `/api/backup/restore-upload` | 上传备份文件并还原；`?confirm=RESTORE` 二次确认 |
| DELETE | `/api/backup/{name}` | 删除备份文件 |

---

## 4. 核心接口详解

### 4.1 新增流水 `POST /api/transactions`

```json
{
  "amount": 38.5,
  "direction": "expense",
  "category_id": 2,
  "account_id": 1,
  "trade_time": "2026-10-12 12:30",
  "counterparty": "滴滴出行",
  "memo": "打车去客户公司",
  "has_invoice": false,
  "is_confirmed": true
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `amount` | number | ✅ | **始终为正数**，方向由 `direction` 决定；`0 < amount ≤ 1e8` |
| `direction` | string | | `expense`（默认）/ `income` |
| `category_id` | int \| string | | 不传时按 `memo` 中的类别名自动匹配 |
| `account_id` | int \| string | | 资金账户 |
| `trade_time` | string | | 缺省为当前时间 |
| `counterparty` | string | | 交易对手方，参与去重模糊匹配 |
| `memo` | string | | 备注 |
| `has_invoice` | bool | | 是否已取得发票（影响税前扣除提示） |
| `is_confirmed` | bool | | 默认 `true`；为 `false` 时进入待确认 |

响应示例：

```json
{
  "success": true,
  "data": {
    "transaction": { "id": 13, "amount": 38.5, "direction": "expense", "memo": "打车去客户公司", "is_duplicate": false, "is_anomaly": false },
    "duplicate": null,
    "anomaly": { "is_anomaly": false, "reasons": [] }
  }
}
```

> **副作用**：自动执行去重检测（金额一致 + 时间窗口内 + 对手方模糊相似度 ≥ 80）
> 与异常检测（同类别历史 z-score），结果随响应返回，并写入审计日志。

### 4.2 生成报告 `POST /api/reports/generate`

```json
{
  "period": "monthly",
  "reference": null,
  "custom_days": null,
  "custom_start": null,
  "custom_end": null,
  "push": false
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `period` | string | `daily` / `weekly` / `monthly`（默认）/ `quarterly` / `yearly` / `custom`；**非法值返回 200 + `success:false`，不是 500** |
| `reference` | string | 基准日期，不传取今天；用于回看历史周期 |
| `custom_days` | int | `period=custom` 时的天数 |
| `custom_start` / `custom_end` | string | 也可直接指定起止日期 |
| `push` | bool | 是否生成后立即推送（默认 `false`） |

响应：`data` 含 `id / title / period / period_start / period_end / status / elapsed_ms /
summary_text / payload`。`summary_text` 是可直接发到聊天窗口的纯文本摘要；
`payload` 是前端动画渲染的结构化数据，**结构是前后端与出图层共同的契约**，
变更字段名必须三处（`services/report.py`、`index/js/report.js`、`render/html.py`）同步。

### 4.3 自然语言记账（两步式）

**第一步：抽取** `POST /api/ai/natural`

```json
{ "text": "昨天打车 38，请客户吃饭 260", "auto_create": false }
```

返回 `items[]`（amount / direction / category / memo / trade_time / confidence / source）
与 `engine`（`llm` 或 `rule`）。

**第二步：确认** `POST /api/ai/nl-confirm`

```json
{ "items": [ { "amount": 38.0, "direction": "expense", "category_id": 2, "account_id": 1, "memo": "打车", "trade_time": "2026-10-11 00:00" } ] }
```

> **为什么要拆成两步**：模型抽取可能出错，先返回给用户确认再写库，
> 避免 AI 幻觉直接污染账本。`auto_create: true` 时第一步直接入库，适合机器人群聊。
> **批量确认的容错规则**：某一条为负数时**跳过该条、其余正常落库**（200），
> 不会让整批变成 422 全失败。账本归属（`account_id`）由用户在确认时选择。

### 4.4 对话式查询 `POST /api/ai/chat`

```json
{ "question": "本月餐饮花了多少？", "history": [] }
```

> 模型只做**意图识别与参数抽取**，实际查询由后端预置服务函数执行
> （`sum_by_category` / `list_recent` / `compare_period` 等）。
> 刻意不做裸 NL2SQL —— 既安全可控，也便于解释。

### 4.5 建议采纳率 `GET /api/audit/stats`

```json
{
  "success": true,
  "data": {
    "total": 24,
    "by_state": { "pending": 8, "adopted": 11, "rejected": 3, "ignored": 2 },
    "adoption_rate": 78.57,
    "decided_count": 14,
    "pending_count": 8,
    "ignored_count": 2
  }
}
```

> **指标口径**：`adoption_rate = adopted / (adopted + rejected)`。
> `ignored`（超时未处理）**不计入分母**，单独以 `ignored_count` 披露 ——
> 避免把「用户没看见」算成「用户拒绝」而污染指标。

### 4.6 CSV 账单导入 `POST /api/import/csv`

`multipart/form-data`：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `file` | file | CSV 文件（≤ 8 MB） |
| `auto_confirm` | bool | 是否自动确认入库（默认 `true`） |
| `dry_run` | bool | 只解析不入库（预览）；**表单字段与查询串两种传法都认** |

> 已知处理能力：微信/支付宝账单的说明行跳过、`收/支` 与 `交易类型` 列区分、
> `不计收支` 记录跳过、未加引号的千分位逗号（`¥1,299.00`）自动修复。
> 去重优先于入库：重复导入只计入 `duplicate_count`，不产生脏数据。

---

## 5. 注意事项

### 5.1 协议端（OneBot v11）

- 反向 WS 地址：`ws://127.0.0.1:2525/onebot/v11/ws`，由 NapCat / Lagrange / go-cqhttp 主动连接。
- 推送目标在 `config.yaml` 的 `push.target_type`（`group` / `private`）与 `push.target_id` 配置。
- 配置为空但 `SUPERUSERS` 有值时仍可投递，`/api/push/status` 会给出「实际生效目标」。
- 出图依赖 Playwright，**必须单独安装内核**：`playwright install chromium`。

### 5.2 演示模式（`app.debug: true`）

前端**完全使用内置模拟数据**，不发任何业务请求并跳过登录。
每次刷新重新生成随机数据。答辩现场网络异常或库为空时打开它可稳定演示；
正式使用时保持 `false` —— 接口失败会显示错误，绝不悄悄用假数据顶替。

### 5.3 配置写入安全

`/api/settings` 的写入受两级白名单约束（`firefly/config.py` 的
`EDITABLE_FIELDS` / `SYSTEM_MANAGED_FIELDS`）。`auth.password_hash` /
`auth.password_salt` / `auth.secret_key` 等系统字段**永远拒绝 API 写入**，
且读取时一律脱敏为 `******`。历史教训：白名单漏一种输入写法（如嵌套对象）
就等于没有白名单，校验必须覆盖所有语法形态。

### 5.4 金额与数据完整性

- 单笔金额上限 1 亿元（`deps.MAX_AMOUNT`，唯一来源，同时约束交易/AI 确认/账户期初/预算/订阅五处）。
- SQLite 存 REAL（IEEE754 双精度），超过 2^53 后整数不再精确 —— 这就是必须有上界的原因。
- 新增流水会自动去重与异常检测；导入类操作提供 `dry_run` / `inspect` 预览，先看清再落库。

### 5.5 账号与口令

- 初始账号 `admin`，口令见 `config.yaml` 的 `auth.password`（首次启动自动生成并打印）。
- 改口令必须验证原口令；登录失败提示不区分「用户不存在」与「口令错误」（防账号枚举）。
- 连续失败 `max_attempts`（默认 5）次后锁定 `lockout_minutes`（默认 15）分钟。

### 5.6 部署与运维

- 端口默认 2525，改 `config.yaml` 的 `server.port` 或前端设置页保存。
- 换 PostgreSQL 只需改 `database.url` 一行（需 `pip install asyncpg`），重启自动建表。
- Nginx 反代必须放行 WebSocket 升级头（协议端连接必需）。
- 手动改 `config.yaml` 后需重启；前端设置页保存**立即生效**。
- 测试回归：`python tests/test_api.py`（98 项用例）。

## 5. v1.0.1：Excel 批量记账

### GET `/api/import/excel/template`

需登录（启用鉴权时）。返回 XLSX 文件流，不使用 JSON 信封。
工作表「记账数据」为空白输入区；「填写说明」不参与导入。

| 列名 | 规则 |
| --- | --- |
| 交易日期 | 必填，Excel 日期或 ISO / YYYY-MM-DD[ HH:MM]；无时区时按 app.timezone |
| 收支类型 | 必填，收入 / 支出；接口也接受 income / expense |
| 金额 | 必填，正数、最多两位小数、最大 100000000 元 |
| 类别 | 可选，已有类别且与收支方向一致；空白归入相应其他类别 |
| 账本名称 | 可选，已有且唯一的名称；空白使用 account_id 默认账本 |
| 交易对方 / 备注 | 最多 128 / 512 字 |
| 是否有发票 | 是 / 否，留空视为否 |

### POST `/api/import/excel`

`multipart/form-data`：`file`（必填 .xlsx）、`account_id`（可选默认账本）、`dry_run`（可选 bool，默认 false）。
`dry_run` 同时接受 Query 和 Form，表单显式值优先。仅支持未加密、无宏、不含公式的 XLSX；上传最大 8MB、解压最大 64MB、2000 行、32 列。
只读「记账数据」工作表；没有该名称则读取首个工作表。首行必须为表头。

预览返回 `data: {dry_run:true,total,preview:[最多50条],message}`，不写交易或批次、不发送消息。
校验失败返回 `success:false`，逐行错误位于 `detail.errors:[{row,message}]`（最多返回100条）。有任何填写错误，整批不入账。

成功示例：
```json
{"success":true,"data":{"batch_id":1,"total":2,"imported_count":2,"duplicate_count":0,"failed_count":0,"income":100.0,"expense":12.3,"message":"成功入账 2 笔，重复跳过 0 笔","notification":{"attempted":true,"sent":true,"message":"导入结果已推送到协议端"}}}
```

另含 `filename / imported:[{row,id,amount,direction}] / duplicates:[{row,duplicate_of}]`。
去重范围：同用户、同账本、同方向、同金额、配置时间窗口及匹配对方；跨账本不视为重复。
账目、批次与审计提交成功后才通知。推送关闭、未连接或发送超时只影响 `notification`，不会撤销已保存账目；没有新增时不重复推送。
通知沿用 push.target_type / target_id，空目标可由 SUPERUSERS 兜底。

## 6. v1.0.1：版本与仓库更新

### GET `/api/system/version`

需登录（启用鉴权时）。返回 `running_version / installed_version / commit / branch / repository / target_branch / update_supported / reason / restart_required`。
只查询本地 Git，不发起网络拉取。运行版本来自已加载代码；安装版本来自磁盘代码，二者不同表示需要重启。

### POST `/api/system/update`

需登录（启用鉴权时），仅允许本机请求，且必须带 `X-Firefly-Update: 1`。带 Origin 时须与当前 Host 一致。
不接受远端地址、分支或 Shell 命令参数；只从固定官方 origin 的 main 执行 fetch 和 merge --ff-only。

前提：Git 安装版、官方 origin、main 分支、源码与暂存区无未提交改动。仅修改 config.yaml 可以更新。
更新前备份到 `data/updates/`，更新后保留用户配置并采用新发布版本号；不修改 .env 或数据库，不自动安装依赖或重启。
分叉、非快进、未提交源码、Git 失败等返回 `success:false`；缺少操作标记 HTTP 400，来源或客户端不符 HTTP 403，未登录 HTTP 401。
成功返回 `updated / running_version / installed_version / commit / restart_required / dependencies_changed / config_backup / message`；已经最新时 `updated:false`。

bootstrap、meta、health 的 version 与运行代码一致，OpenAPI info.version 也同步。


## 7. v1.0.2：自然语言确认、只读 AI 与流水管理

### 7.1 自然语言结果与开票

`POST /api/ai/natural` 默认只解析，`data.items` 同时返回 `category` 名称和按方向匹配的 `category_id`。
前端确认卡片允许修改类别、金额、账本、备注及 `has_invoice`（布尔，默认 false）。

```json
{"items":[{"amount":38,"direction":"expense","category_id":"1","account_id":"1","trade_time":"2026-10-17T12:30:00+08:00","memo":"打车","has_invoice":true}]}
```

上述数据提交到 `POST /api/ai/nl-confirm`。每批最多 100 条；空 ID 归一化为空，类别名兜底匹配方向；禁止跨用户账本或类别、方向与类别冲突。
有效条目先整批验证再落库，返回 `created` 中保留 `has_invoice`、`category_id`、`account_id`。历史兼容：非正金额条目跳过；非有限值和超上限金额拒绝。
`auto_create:true` 是已有网页 API 的显式直写选项，不适用于 QQ 流程；默认 false。

### 7.2 AI 只读工具约定

`POST /api/ai/chat` 请求 `question`（1–4000 字）及可选 `history`（最多 20 条，使用最近 6 条）。
服务端只接受 intent / period / direction / category / top_n / custom_days；模型提供的 user_id、SQL、写入指令不会执行。
可用 intent：`sum_by_category`、`sum_total`、`top_categories`、`count_transactions`、`list_transactions`、`compare_period`、`budget_status`、`tax_status`、`account_balance`、`financial_overview`。
`top_n` 限 1–10、自定义周期限 1–366 天；明细最多最近 10 条，读取始终按当前用户隔离。

响应保留 `answer` / `intent` / `data` / `chart` / `plan` / `engine`，新增 `tools` 和 `analysis_engine`。
`engine` 表示意图规划来源，`analysis_engine` 为 `llm` 或 `rule`。先执行真实计算，再把只读结果交给模型分析；模型失败时保留真实计算答案，并返回 `analysis_note`。
模型上下文不包含配置、密钥或其他用户账目；返回的数据中的备注与名称不作为工具指令执行。

### 7.3 流水管理及操作日志

- `GET /api/transactions` 新增可选整数 `account_id` 筛选，其他筛选与 limit / offset 同时生效，total 与列表口径一致。
- `POST /api/transactions` 保留新增入口；`PATCH /api/transactions/{id}` 修改流水，`DELETE` 删除，`GET .../{id}` 查询详情。
- PATCH 中 `category_id:null` 或 `account_id:null` 明确清空归属；未传的字段保持原值。方向与类别必须匹配，引用需属于当前用户。
- 交易时间接收 ISO 8601，统一保存为 UTC；响应时间含时区信息。
- `GET /api/audit/logs?target_type=transaction&target_id=123` 可按流水查看操作日志；省略 target_id 查看全部流水操作。
- 创建日志 extra.after 保存快照；修改日志 extra.before / extra.after / extra.fields 保存变更；删除日志 extra.before 保留原内容。日志与数据修改同事务保存。

### 7.4 QQ 确认状态机（OneBot 消息入口）

自然语言记账与 `/记` 先用规则引擎生成离线结果，只保存 `ff_bot_draft`，不创建流水。
回复「接受 / 确认 / 确认入账 / 同意 / 是 / 确认记账」接受当前预览；「取消 / 取消记账 / 放弃」结束；其余回复作为修改意见交给模型，产生新结构化预览后继续等待接受。
AI 失败或无有效条目进入 needs_revision，不能接受旧的被拒结果。多轮反馈最多保留最近 10 轮上下文，每轮待确认有效期 15 分钟。
草稿按机器人 + 会话 + 发送者哈希绑定，数据库持久化；接受使用状态 / revision 条件更新，重复和并发确认只入账一次。

私聊无需 @；群聊需要 @ 机器人。管理员（SUPERUSERS）及私聊推送目标在私聊映射到 local，其余用户和群聊使用独立平台用户，所有查询、报告和记账命令采用同一映射。
含「查询 / 明细 / 多少 / 统计」等的数字问题进入只读查询，避免把「最近 10 笔」误记作 10 元。
