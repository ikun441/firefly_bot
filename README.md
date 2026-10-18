# 流萤 Firefly ✨ · v1.0.3

<img src="./index/assets/brand/firefly-logo.jpg" alt="流萤 Logo" width="120" />

原生 HTML / CSS / JavaScript + FastAPI / NoneBot 的记账与财税分析助手。
支持流水管理、自然语言确认记账、Excel 导入、预算、周期报告、AI 只读查询及 QQ 推送。

## 启动

需要 Python 3.10–3.12（推荐 3.11）、Git；Windows 双击 `start.cmd`，macOS / Linux 执行：

```bash
git clone https://github.com/ikun441/firefly_bot.git
cd firefly_bot
chmod +x start.sh
./start.sh
```

脚本创建环境、检查依赖并启动。已有 Python 环境可手动运行：

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
python -m firefly.app
```

- 网页：http://127.0.0.1:2525/ ，API 文档：同端口 `/docs`。
- 地址和端口以 `config.yaml` 的 `server` 为准，登录信息以 `auth` 为准。
- Windows 排查：`start.cmd --firefly-console`，日志位于 `logs/startup.log`。

## 配置 AI 与 NapCat

LLM 密钥可放在 `.env` 的 `LLM_API_KEY`，也可写入本地 `config.yaml`：

```yaml
llm:
  enabled: true
  base_url: https://api.deepseek.com/v1
  model: deepseek-chat
  api_key_env: LLM_API_KEY  # 环境变量名，不是密钥
  api_key: ''              # 在本机填入该服务商的有效密钥
```

环境变量优先于 YAML。密钥不会通过设置接口回显，也不要提交到仓库。
401 / 403 表示模型服务认证或权限问题；开启 SOCKS 代理时需要 `socksio`，已包含在依赖中。

NapCat 添加 OneBot v11 反向 WebSocket：`ws://127.0.0.1:2525/onebot/v11/ws`。
配置 `ONEBOT_ACCESS_TOKEN` 时两端需一致。`push.target_type`、`push.target_id` 决定推送目标；`.env` 的 `SUPERUSERS` 为管理员 QQ 号。

## 使用

| 入口 | 操作 |
| --- | --- |
| 流水记账 | 手动录入，或自然语言解析后核对类别、账本、金额、发票并确认 |
| 流水管理 | 全部流水筛选分页、增删改查、查看操作前后快照 |
| Excel 导入 | 下载空白 `.xlsx` 模板，按填写说明上传；最多 8MB / 2000 条，校验后批量入账 |
| 财务总览 / 预算 | 查看财务指标与当前月预算；未设预算与零使用率分开显示 |
| 周期报告 | 生成报告，选择文字 / 图片 / 图文，启用 AI 后解读同时进入所选内容 |
| AI 助手 | 输入 `ai 本月支出多少？`，模型通过白名单 function_call 查询并分析 |
| 系统设置 | 查看版本、切换演示模式、从官方 main 一键快进更新 |

QQ 私聊直接发送「今天打车 38 元」：先返回离线解析，回复「接受」才入账；「不接受 + 修改意见」交给 AI 重解析，再次确认；「取消」结束。
草稿 15 分钟有效，绑定会话与发送者，重复确认不会重复入账。

QQ 或网页输入 `ai 问题` 会进入**仅查询**模式：可读取收支、明细、类别、预算和余额，不能记账、接受草稿、删账或修改配置。
该入口优先于记账流程；群聊可用 `ai 问题` 触发，普通自然语言记账需 @ 机器人。
QQ 的 AI 查询与分析回复会排版成 Markdown 图片，长回复分页，渲染或发送失败时尝试回退完整文字。
预算提醒开启后每分钟检查总预算与分类预算，达到预警比例或超支会主动推送；同日按等级去重，发送失败会重试。
管理员与私聊推送目标在私聊使用网页本地账本；其他用户及群聊账本独立隔离。

一键更新要求官方 origin、本地 main、源码无未提交改动且可以快进。配置先备份并保留，`.env` 和数据库不参与 Git 更新。更新后重启服务、刷新网页；依赖变更时使用启动脚本补齐。
`app.debug: true` 为模拟数据演示模式，真实财务操作请关闭。

## 文档与验证

- [版本历史](./docx/version.md)
- [修复与验证记录](./docx/debug.md)
- [API 规范](./docx/api.md)
- [部署、架构与开发参考](./docx/guide.md)
- [前端结构](./index/README.md)

```bash
python -m unittest tests.test_v101 tests.test_v102 tests.test_v103 tests.test_ai_reply tests.test_alerts -v
python -m tests.run_isolated_api
```

回归使用临时数据库与模拟模型，不发送真实 QQ 消息。真实模型能力取决于供应商接口、有效密钥与 function calling 支持。
税务结果仅供参考，以主管税务机关及专业意见为准。许可证见 [LICENSE](./LICENSE)。
