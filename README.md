# 流萤 Firefly ✨ · v2.0.0a2（会计核算内测）

<img src="./index/assets/brand/firefly-logo.jpg" alt="流萤 Logo" width="120" />

原生 HTML/CSS/JavaScript + FastAPI/NoneBot 的会计记账与 AI 问询助手。新增复式凭证、期初、审核过账、红字冲销、试算、对账结账、唯一管理员和 QQ 独立权限，保留 AI 只读问询及图片输出。

当前实现与未完成项见 [实施记录](./docx/accounting-implementation.md)，完整目标见 [开发方案](./docx/accounting-refactor-plan.md)。本版为内测，不代表完整正式会计软件已交付。

## 启动

需要 Python 3.10–3.12（推荐 3.11）与 Git。Windows 使用 start.cmd，macOS/Linux 使用：

    git clone https://github.com/ikun441/firefly_bot.git
    cd firefly_bot
    chmod +x start.sh
    ./start.sh

已有 Python 环境也可手动启动：

    python -m pip install -r requirements.txt
    python -m playwright install chromium
    cp config.example.yaml config.yaml
    cp .env.example .env
    python -m firefly.app

网页：http://127.0.0.1:2525/ ，API 文档：同端口 /docs。实际地址、端口及登录账号以本地配置为准。运行时自动执行版本化结构迁移，不会自动创建账套或测试凭证。首次升级前先备份旧数据库及配置。本地 config.yaml 不再纳入 Git，首次安装从 config.example.yaml 复制并修改；升级保留现有配置。

## AI 与 NapCat

LLM 密钥在本地 .env 的 LLM_API_KEY 或 config.yaml 的 llm.api_key 配置；环境变量优先。llm.enabled 开启且有效模型支持 function calling 时使用 AI 查询。密钥不得提交到仓库。

NapCat OneBot v11 反向 WebSocket：ws://127.0.0.1:2525/onebot/v11/ws 。配置 ONEBOT_ACCESS_TOKEN 时两端保持一致。

网页始终要求唯一管理员登录。通过“权限设置”绑定管理员 QQ、添加普通 QQ 并分别配置增删改查。SUPERUSERS、群号或推送目标不会自动获得会计权限。

## 使用

首次进入“会计工作台”为空账。创建账套并核定期初后制证；凭证流程为草稿→提交→管理员审核→过账。只有已过账分录进入正式余额，已过账凭证不能直接改删，更正走全额红字冲销与新凭证。

“账套管理”支持新建、编辑、查询、切换、删除归档和恢复。各账套独立核算；归档保留凭证和日志。科目管理支持新增科目、子科目及数量单价核算；预算管理支持费用类别及月度额度。费用凭证选择预算类别，数量核算科目按数量×单价计算金额。

“操作日志”统一显示中文操作描述、实际操作人和平台。凭证录入采用摘要、科目、借方、贷方工作表，上下均有保存入口，空摘要等错误直接显示中文提示。

QQ 回复“接受”和 CSV/XLSX 导入仅保存业务草稿，不会自动推断会计分录或过账。

| 命令 | 操作 |
| --- | --- |
| /帮助 | 按本人权限输出图片菜单 |
| /记 办公费 200 | 解析业务并等待确认 |
| /明细 上月 第2页 | 查询会计日期范围内的凭证，每页 10 张 |
| /报告 2026-11 | 指定期间已过账会计汇总 |
| /报告 2026-11-01 2026-11-30 | 自定义起止日期 |
| /余额 | 已过账科目余额与试算 |
| /预算 2026-11 | 费用类别预算与执行情况 |
| ai 本月利润是多少？ | 白名单只读查询与分析 |
| /提交、/修改摘要、/作废、/冲销 | 按对应独立权限操作 |
| /审核、/过账 | 仅唯一管理员 |

明细和报告支持今天/昨天、本周/上周、本月/上月、本季度/上季度、本年/去年、指定年/月/日与起止日期，不填默认本月。群聊普通记账需 @ 机器人，AI 可用 ai 前缀。帮助、明细、报告和 AI 回复渲染为图片，失败回退文字。

AI 服务端限定账套和权限，只读已过账凭证、科目与财务汇总，不提供任意 SQL 或写账工具；模型未配置时明确返回规则结果。

月末在工作台完成资金对账、损益结转、预检和结账；12 月另做年度利润结转。关账后不可向该期间补写，也不提供反结账。基础汇总尚不等同完整规范格式的正式财务报表。

新账启用后旧流水/导入/订阅写账受阻，旧自动报告/预警暂停，历史查询保留。演示环境与真实账务分开，真实操作保持 app.debug=false。

## 完整备份与隔离恢复

在工作台下载 v3 完整备份，包含新账、权限和操作记录。恢复仅创建新的隔离数据库，拒绝覆盖：

    python scripts/restore_backup.py /path/to/backup.json --database /path/to/new-isolated.db

核对隔离库后另行维护切换服务。配置与密钥独立保管。v2 完整会计备份可隔离恢复；旧流水备份不能直接覆盖会计账。

## 加入社群
<img width="1284" height="2283" alt="微信图片_20261030190348_75_72" src="https://github.com/user-attachments/assets/3307a675-106f-4986-a052-36c4174ccb11" />

## 验证与文档

    python -m unittest tests.test_accounting tests.test_v101 tests.test_v102 tests.test_v103 tests.test_ai_reply tests.test_alerts -q
    python -m tests.run_isolated_api

测试使用临时数据库与模拟模型，不发送真实 QQ 消息。真实模型效果取决于供应商、有效密钥与 function calling 支持。仅在授权的隔离联调环境执行真实协议测试。

- [开发方案](./docx/accounting-refactor-plan.md)
- [实施与验收记录](./docx/accounting-implementation.md)
- [原版本历史](./docx/version.md)
- [原 API 规范](./docx/api.md)
- [部署与开发参考](./docx/guide.md)
- [前端结构](./index/README.md)

税务结果以主管税务机关及专业意见为准。许可证见 [LICENSE](./LICENSE)。
