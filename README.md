# 流萤 Firefly ✨

> **智能财报总结与财税优化助手** —— 面向金融科技应用专业的毕业设计作品

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![NoneBot2](https://img.shields.io/badge/NoneBot2-2.5.0-1E90FF)](https://nonebot.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.143.0-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](./LICENSE)

流萤把「个人记账」升级为「小微企业财税助手」：用户在网页端录入流水，系统按自定义周期
自动生成**财务税务整体状况**与**财务税务优化方向**两大模块的复盘报告，前端以动画渲染
结果，同一份数据同时经协议端定时推送静态图片到聊天窗口。温馨提示：本项目建议本地运行，以规避可能出现的网络安全风险

---

## 目录

- [🚀 三分钟跑起来](#-三分钟跑起来)
- [部署与启动](#部署与启动)
  - [方式一：Conda 部署（推荐）](#方式一conda-部署推荐)
  - [方式二：venv 部署](#方式二venv-部署)
  - [首次启动检查清单](#首次启动检查清单)
  - [生产环境部署](#生产环境部署)
- [本项目优化记录](#本项目优化记录)
- [项目简介](#项目简介)
- [核心特性](#核心特性)
- [架构设计](#架构设计)
  - [分层结构与依赖方向](#分层结构与依赖方向)
  - [为什么这样分层](#为什么这样分层)
  - [关键设计决策与理由](#关键设计决策与理由)
  - [数据模型](#数据模型)
  - [报告数据契约](#报告数据契约)
  - [请求生命周期](#请求生命周期)
- [配置说明](#配置说明)
  - [`config.yaml` —— 全局设置（前端可改）](#configyaml--全局设置前端可改)
  - [登录鉴权](#登录鉴权)
  - [`.env` —— 敏感值（不进版本库）](#env--敏感值不进版本库)
- [项目结构](#项目结构)
- [功能地图](#功能地图)
- [协议端对接](#协议端对接)
- [API 接口文档](#api-接口文档)
  - [约定](#约定)
  - [接口总览](#接口总览)
  - [核心接口详解](#核心接口详解)
  - [错误处理](#错误处理)
- [常见问题](#常见问题)
- [论文可量化指标](#论文可量化指标)
- [技术栈](#技术栈)

---

## 🚀 三分钟跑起来

已经装好依赖的话，**只需要这三步**：

```bash
cd firefly_bot

cp .env.example .env          # 填入 LLM_API_KEY（留空也能跑，NL 记账降级为规则引擎）
python -m firefly.app         # 启动
```

看到终端横幅后，浏览器打开 **http://127.0.0.1:2525/** 即可。
首次部署（还没装依赖）请直接看下一节 [部署与启动](#部署与启动)。

> **环境要求**：Python **3.10 ~ 3.12**（本项目在 3.11.17 上开发验证）。
> macOS 自带的 `/usr/bin/python3` 是 3.9，**不要直接用它装依赖**，请按下一节创建 3.10+ 环境。

| 入口 | 地址 |
| --- | --- |
| 🖥 前端控制台 | http://127.0.0.1:2525/ |
| 📡 API 文档（Swagger） | http://127.0.0.1:2525/docs |
| 🔌 OneBot v11 反向 WS | http://127.0.0.1:2525/onebot/v11/ws |

**初始账号**：用户名 `admin`，口令见 `.env` 的 `AUTH_PASSWORD`（首次启动会自动生成并打印在终端）。

---

## 部署与启动

> **环境要求**：推荐 **Python 3.10 ~ 3.12**（本项目在 3.11.17 上开发与验证）。
> 在 Python 3.9 上 pip 虽能装完（会自动回退到 nonebot2 2.4.4、pandas 2.x 等旧版），
> 但不属于受支持的组合，请勿用于正式演示。
>
> ⚠️ **macOS 用户请注意**：系统自带的 `/usr/bin/python3` 是 3.9，**不要直接 `pip install -r requirements.txt`**，
> 请务必按下方任一方式创建 3.10+ 的虚拟环境。

### 方式一：Conda 部署（推荐）

Conda 跨平台一致性好，且能精确锁定 Python 版本，是**答辩演示的首选**。

#### 1. 安装 Miniconda / Miniforge

| 平台 | 操作 |
| --- | --- |
| **macOS (Apple Silicon)** | `brew install --cask miniforge`，或从 [miniforge releases](https://github.com/conda-forge/miniforge/releases) 下载 `Miniforge3-MacOSX-arm64.sh` 后 `bash Miniforge3-MacOSX-arm64.sh -b -p "$HOME/miniforge3"` |
| **macOS (Intel)** | 同上，选择 `MacOSX-x86_64.sh` |
| **Windows** | 下载 [Miniconda3 Windows 安装包](https://docs.conda.io/en/latest/miniconda.html) 并勾选 *Add to PATH* |
| **Linux** | `wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/mc.sh && bash /tmp/mc.sh -b -p "$HOME/miniconda3"` |

验证安装：

```bash
conda --version      # 期望输出 conda 24.x 或更高
```

#### 2. 创建并激活环境

```bash
cd /path/to/firefly_bot

# 创建 Python 3.11 环境（首次约需 1-2 分钟）
conda create -n firefly python=3.11 -y

conda activate firefly

# 确认版本不低于 3.10
python -V            # 期望 Python 3.11.x
```

> Windows 用户若提示 `conda` 不是内部命令，请改用 **Anaconda Prompt** 执行以上命令。

#### 3. 安装依赖

```bash
# 使用清华镜像可显著提速（国内网络）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 若网络通畅，直接用官方源亦可
# pip install -r requirements.txt
```

#### 4. 安装 Playwright 浏览器内核

htmlrender 依赖 Playwright 渲染 HTML 出图，**必须单独安装内核**：

```bash
playwright install chromium
```

> 若只需网页端与 API 功能、暂不需要图片推送，可跳过此步。

#### 5. 配置密钥

```bash
cp .env.example .env
```

编辑 `.env`，至少填写 LLM Key（留空也能跑，自然语言记账会降级为规则引擎）：

```ini
LLM_API_KEY=sk-你的deepseek密钥
```

#### 6. 启动

```bash
python -m firefly.app
```

浏览器打开终端提示的地址即可：

```
╭──────────────────────────────────────────────────────────────╮
│                       流 萤 · Firefly                        │
│               财报总结优化 · 智能财务财税助手                │
╰──────────────────────────────────────────────────────────────╯
  🖥  前端控制台   http://127.0.0.1:2525/
  📡 API 文档     http://127.0.0.1:2525/docs
  🔌 OneBot v11   http://127.0.0.1:2525/onebot/v11/ws
```

---

### 方式二：venv 部署

venv 是 Python 标准库自带方案，**无需安装任何额外工具**，适合轻量部署。

#### 1. 确认 Python 版本

```bash
python3 -V           # 推荐 3.10 ~ 3.12
```

若你的系统 Python 版本过低（例如 macOS 自带的 3.9），请先安装新版：

| 平台 | 安装命令 |
| --- | --- |
| **macOS** | `brew install python@3.11` |
| **Ubuntu / Debian** | `sudo apt update && sudo apt install python3.11 python3.11-venv -y` |
| **CentOS / RHEL** | `sudo dnf install python3.11 -y` |
| **Windows** | 从 [python.org](https://www.python.org/downloads/) 下载 3.11 安装包，**务必勾选 Add python.exe to PATH** |

#### 2. 创建并激活虚拟环境

```bash
cd /path/to/firefly_bot

# 创建
python3 -m venv .venv

# 激活 —— macOS / Linux
source .venv/bin/activate

# 激活 —— Windows PowerShell
# .venv\Scripts\Activate.ps1

# 激活 —— Windows CMD
# .venv\Scripts\activate.bat

# 确认已进入虚拟环境（前缀应出现 (.venv)）
python -V
```

> **Windows 执行策略报错**：若 PowerShell 提示 *无法加载脚本，因为在此系统上禁止运行脚本*，
> 请先执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，或改用 CMD。

#### 3. 安装依赖

```bash
# 升级 pip 可减少依赖解析失败
python -m pip install --upgrade pip

pip install -r requirements.txt

# 国内网络建议加镜像
# pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> **Linux 编译问题**：若 chromadb 相关依赖编译失败，先装编译工具链：
> `sudo apt install build-essential python3-dev -y`

#### 4. 安装 Playwright 内核

```bash
playwright install chromium

# Linux 若缺少系统库，追加：
# playwright install-deps chromium
```

#### 5. 配置密钥

```bash
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY
```

#### 6. 启动

```bash
python -m firefly.app
```

退出虚拟环境：`deactivate`

---

### 首次启动检查清单

启动后按顺序确认以下五点，可避免绝大多数问题：

- [ ] **终端横幅打印出 `http://127.0.0.1:2525/`** —— 说明前后端已同端口挂载成功
- [ ] **浏览器打开该地址能看到萤火粒子背景与导航栏** —— 前端资源加载正常
- [ ] **顶栏右上角状态胶囊显示「服务在线」** —— `/api/health` 连通
- [ ] **顶栏出现「🤖」按钮** —— 点击可查看协议端连接状态
- [ ] **打开 `http://127.0.0.1:2525/docs` 能看到 Swagger 接口文档** —— 文档路由可用

若前三项正常但设置页报错，通常是 `config.yaml` 被外部编辑器改坏了 YAML 缩进，
删除该行或恢复默认值即可。

---

### 生产环境部署

#### 反向代理（Nginx）

```nginx
server {
    listen 80;
    server_name firefly.example.com;

    location / {
        proxy_pass http://127.0.0.1:2525;
        proxy_http_version 1.1;

        # WebSocket 升级（协议端连接必需）
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

#### 后台常驻

```bash
# 方式一：nohup
nohup python -m firefly.app > firefly.log 2>&1 &

# 方式二：systemd（Linux）
sudo tee /etc/systemd/system/firefly.service > /dev/null <<'EOF'
[Unit]
Description=Firefly Finance Assistant
After=network.target

[Service]
Type=simple
User=firefly
WorkingDirectory=/opt/firefly_bot
ExecStart=/opt/firefly_bot/.venv/bin/python -m firefly.app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now firefly
```

#### 更换为 PostgreSQL

```bash
pip install asyncpg
```

```ini
# .env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/firefly
```

重启后自动建表，无需手动迁移。

---

## 本项目优化记录

以下问题都是**实际跑起来后暴露出来的**，不是纸面设计。每条都记录了「症状 → 根因 → 修法」，
作为开发过程与工程能力的一手材料。

### 一、数据隔离：测试数据污染生产库

| # | 症状 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | `app.debug=false` 时，总览页与报告页仍显示 **1299 元**支出 | 测试脚本写入的流水残留在正式库中，且**回收机制没跑起来** —— 以 `python tests/test_api.py` 方式运行时项目根不在 `sys.path`，`import firefly` 全线 ImportError，基线快照失败被静默忽略 | 修正测试入口的模块路径；为同批入库的流水补登记回收名单 |
| 2 | 报告页仍残留 2598 元等历史数字 | 报告归档 `ff_report` 未随测试清理，`/api/reports/latest` 仍返回**旧报告 payload**，而 payload 是报告页与推送图片的唯一真值来源 | 测试回收扩展到报告与税务检查表；并清空已归档的历史报告，使前端重新生成 |

**核心教训**：测试回收不能只依赖「测试跑完自己清理」——一旦测试因 import 失败而中断，
清理逻辑根本不会执行，而失败信息又被 `try/except` 吞掉。**静默失败比报错危险**。

### 二、金额字段缺上界

- **症状**：`POST /api/transactions` 提交 `999999999999999` 直接入库，总览页显示天文数字
- **根因**：所有金额字段只写了 `gt=0`，没有上界。SQLite 用 IEEE754 双精度存储，
  超过 2^53 后整数不再精确 —— 回读值已从 `999999999999999` 漂移成 `1000000000000087`
- **修法**：在 `deps.py` 定义 `MAX_AMOUNT = 1e8` 作为**唯一来源**，
  统一应用到交易、AI 确认、账户期初、预算、订阅五处

**关键细节**：修复时不能简单给所有字段加 `gt=0`。项目约定
**「业务规则错误走 HTTP 200 + `success:false`，只有字段校验错误才返回 422」**，
而预算/订阅的负数原本由 service 层拦截并返回带文案的信封。
若把它提到 pydantic，会变成裸 422（没有 `success` 字段），前端拿不到提示。
最终**只卡上界、不动下界**：

| 场景 | 响应 |
| --- | --- |
| 预算 / 订阅负数 | 200 + `{success:false, message, code}` |
| 金额超 1 亿 | 422 |
| AI 批量确认含负数 | 200，跳过该条，其余正常落库 |
| 交易负数 | 422（`gt=0` 原本就在 schema 中） |

### 三、前端账本选择失效

| # | 症状 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | 手动记账「关联账本」显示**暂无可用账本**，但账本页明明有三个 | `collector.js` 写成 `window.FFApi.accountsList()`，而该方法挂在内层 `api` 对象上；调用即抛 `TypeError`，被外层 `catch` **静默吞掉**，`/api/accounts` 请求从未发出 | 改用已声明的 `api.accountsList()`；并让 `window.FFApi` 展开内层方法，两种写法都可用 |
| 2 | 自然语言记账识别后**没有账本选项** | 前端确认区压根没有该字段；后端 `NLItemIn` 也不存在 `account_id`，即使传了也会被 pydantic 丢弃 | 前后端同时补：前端加下拉，后端加字段并接进 `create_transaction` |
| 3 | 点击「确认入账」恒返回 **422**，该功能实际不可用 | LLM 抽出的类别名匹配不到已有类别时，前端传 `category_id: ""`，过不了 pydantic 的整数解析 | 前端空串转 `null`；后端放宽为 `Union[int, str]` 并用 `_as_int` 归一化 |

**第 3 条的隐蔽点**：`item.category_id in id_set` 中集合元素是 `int`，
而前端 `<select>` 的 value 永远是字符串，不做归一化则**永远匹配不上**，
用户的类别选择会被静默丢弃、全部记成未分类。这类「字符串 vs 整数」的坑在
`<select>` 场景下会反复出现，因此项目里统一用 `toId()` / `_as_int()` 在入口处收口。

### 四、静默失败的治理

上述多个 bug 的共同特征是：**异常被 `catch` 吞掉，表现成「什么都没发生」**——
页面不报错、请求不发出、数据静默丢失，排查成本极高。

治理方式：
- `window.FFApi` 展开内层方法，消除「两种写法、一种必错」的陷阱
- 静态资源走 `NoCacheStaticFiles`（`Cache-Control: no-cache, must-revalidate`），
  避免浏览器拿旧 JS 复现已修好的 bug

> 部署后**务必强制刷新（Cmd+Shift+R）**，否则可能仍在跑旧的 JS 缓存。

### 五、接口回归覆盖

维护 `tests/test_api.py` 共 **98 项**用例，覆盖 **16 个分组**：

| # | 分组 | # | 分组 |
| --- | --- | --- | --- |
| 一 | 免鉴权接口 | 九 | 订阅与周期记账 |
| 二 | 鉴权守卫 | 十 | 报告生成 |
| 三 | 登录与身份 | 十一 | 税务自检 |
| 四 | 元信息与配置 | 十二 | AI 能力 |
| 五 | 类别管理 | 十三 | 账单导入 |
| 六 | 流水 CRUD | 十四 | 审计与建议采纳率 |
| 七 | 统计与看板 | 十五 | 协议端推送 |
| 八 | 预算管理 | 十六 | 错误处理与健壮性 |

```bash
python tests/test_api.py        # 期望：98 项通过 / 0 失败 / 0 跳过
```

修复上述问题后，套件稳定在 **98/98 通过（100%）**。

---

## 项目简介

**流萤 Firefly** 是一个面向**小微企业**的智能财税助手，把传统的「个人记账」升级为
「记账 → 分析 → 优化 → 推送」的完整闭环。

### 解决什么问题

小微企业和个体经营者面临三个现实困境：

1. **记账门槛高** —— 传统记账软件要求用户理解会计科目，手工选分类、填方向，坚持不下来；
2. **数据变不成洞察** —— 记完账只看到一串数字，不知道「钱花在哪了」「哪里可以不花」；
3. **税务风险看不见** —— 不清楚自己离增值税起征点还有多远、税负率是否偏离行业区间、
   哪些支出没有取得发票无法税前扣除。

### 怎么做

| 环节 | 方案 |
| --- | --- |
| **降门槛** | 自然语言记账（「昨天打车 38」）、账单 CSV/截图批量导入、周期订阅自动入账 |
| **做分析** | 自定义周期报告，涵盖收支结构、储蓄率、异常识别、归因瀑布图、预算执行 |
| **给方向** | 8 项税务自检（每条附政策依据）+ 按优先级排序的优化行动项 |
| **送上门** | 同一份数据同时渲染到网页（动画）与聊天窗口（静态长图，定时推送） |

### 核心亮点

- **一份数据源，两种呈现**：报告 payload 是唯一真值，网页动画与推送图片消费同一份数据，
  杜绝「网页显示的和机器人发的不一致」；
- **建议采纳率**：系统记录每一条 AI 建议及用户是否采纳，为论文提供可量化的硬指标；
- **可解释优先**：异常检测给出命中的关键词，税务自检给出政策原文依据，而非黑盒结论；
- **零构建前端**：原生 HTML/CSS/JS，答辩现场一条命令即可运行，无 npm 依赖风险。

---

## 核心特性

| 模块 | 能力 |
| --- | --- |
| 📝 **财务数据收集器** | 自定义记账类别、金额、备注、方向、发票标记；快捷金额；支持二级分类 |
| 🔁 **自定义周期报告** | 日 / 周 / 月 / 季 / 年，或任意自定义天数；环比自动对比上期 |
| 📊 **整体状况分析** | 收支结构、储蓄率、日均支出、支出 TOP、账户余额、净现金流 |
| 🌊 **归因分析** | 瀑布图把「支出涨了」拆成「哪几个类别各涨了多少」 |
| 🧮 **税务自检** | 8 项可解释检查（增值税临界点、税负率偏离、个人消费混入、进销项倒挂、缺票等），每条附政策依据 |
| 🎯 **预算 vs 实际** | 分类预算 + 总预算，超支/预警状态自动着色 |
| 🚨 **异常检测** | 同类别 z-score 金额异常 + 个人消费混入识别，**可解释**（给出命中的关键词） |
| 🤖 **自然语言记账** | 「昨天打车 38，请客户吃饭 260」→ 结构化抽取；LLM 不可用时降级规则引擎 |
| 💬 **对话式查询** | 协议端直接问「上月餐饮花了多少」，函数调用而非裸 NL2SQL |
| 🖼️ **静态图推送** | htmlrender 渲染报告为 PNG，定时/手动推送到 QQ 群或私聊 |
| 📈 **建议采纳率** | 每一条 AI 建议记录用户是否采纳，为论文提供**硬指标** |
| 🎨 **二次元风格前端** | 原生 HTML/CSS/JS，萤火粒子背景、极光渐变、数字滚动动画，零构建工具 |

---
## 架构设计

### 分层结构与依赖方向

```
┌──────────────────────────────────────────────────────────────────────────┐
│  接入层                                                                   │
│                                                                          │
│   index/  原生 HTML/CSS/JS          plugins/chat.py   OneBot v11 对话     │
│   （浏览器，动画渲染）               （QQ 群 / 私聊）                      │
└────────────┬─────────────────────────────────────┬───────────────────────┘
             │ HTTP /api/*                         │ 消息事件
             ▼                                     ▼
┌────────────────────────────┐        ┌───────────────────────────────────┐
│  api/     FastAPI 路由层    │        │  scheduler/tasks.py  APScheduler  │
│  参数校验 · 响应包装 · 依赖注入│        │  报告推送 / 订阅入账 / 阈值预警    │
└────────────┬───────────────┘        └───────────────┬───────────────────┘
             │                                        │
             └──────────────┬─────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  services/   业务逻辑层（纯 Python，不 import FastAPI / NoneBot）          │
│                                                                          │
│   ledger      记账底座：流水增删改查 · 周期计算 · 分类聚合                  │
│   report  ★   报告编排：汇总全部子分析，产出唯一 payload                    │
│   taxcheck    8 项税务自检 + 政策依据映射                                  │
│   anomaly     异常检测（z-score + 关键词加权）                             │
│   attribution 归因分析（瀑布图）· 多期趋势                                 │
│   budget      预算执行状态                                                 │
│   audit   ★   审计日志 + 建议采纳率统计                                    │
│   importer    CSV / OCR 账单解析 · 模糊去重                                │
│   subscription 周期订阅自动入账                                            │
└────────────┬──────────────────────────────────────┬──────────────────────┘
             ▼                                      ▼
┌────────────────────────────┐        ┌───────────────────────────────────┐
│  ai/    AI 能力层           │        │  db/   数据访问层                  │
│   llm         OpenAI 兼容客户端│       │   models.py  SQLModel 表定义       │
│   natural     自然语言记账     │       │   session.py 异步引擎 / 会话管理   │
│   classifier  智能分类        │       │   → SQLite (aiosqlite)            │
│   assistant   对话式查询       │       └───────────────────────────────────┘
└────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  render/   输出层                                                         │
│   html.py  报告 HTML 模板  →  image.py  HTML→PNG (Playwright)  →         │
│   bot.py   OneBot v11 发送  →  push.py   推送编排                         │
└──────────────────────────────────────────────────────────────────────────┘
```

**依赖方向严格单向**：`接入层 → services → db / ai`，`services` 永不反向依赖 `api` 或
`plugins`。这条铁律带来一个直接收益。

### 为什么这样分层

**1. `services/` 不依赖任何 Web 框架**

业务逻辑写在 `services/` 里，`api/`（HTTP）与 `plugins/`（聊天）都只是它的**调用方**。
所以「网页上能算出来的数」和「机器人算出来的数」是同一个函数算的，不可能出现两套口径。

反面教材：如果把聚合逻辑写进路由函数，机器人要用就得复制一份，两边的口径迟早分叉。

**2. 报告 payload 是唯一数据源**

`services/report.py` 的 `build_report_payload()` 产出**一个字典**，它同时喂给：

- 网页端 —— `index/js/report.js` 拿它做动画渲染；
- 出图层 —— `render/html.py` 拿它渲染 HTML 再转 PNG。

所以「网页显示的和推送图片里的」永远一致。这也是为什么 payload 的结构被当作
**对外契约**（见下文「报告数据契约」），而不是内部实现细节。

**3. 降级路径内建**

`ai/llm.py` 在未配置 `LLM_API_KEY` 时抛 `LLMNotReadyError`，但**不会让服务启动失败**；
自然语言记账会自动切到规则引擎（正则 + 关键词）。这样答辩现场即使断网、密钥失效，
核心功能依然可演示。`/api/health` 与 `/api/meta` 都会如实报告 `llm_ready: false`。

### 关键设计决策与理由

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| **驱动器** | NoneBot2 + **FastAPI** | NoneBot 的 FastAPI driver 本身就是一个标准 ASGI app，可以直接 `app.mount("/", StaticFiles(...))` 把前端挂到根路径，实现**前后端同端口**：无 CORS 配置、无第二个进程、演示时只有一个地址 |
| **协议** | **OneBot v11** | 生态最成熟，NapCat / Lagrange / go-cqhttp 都支持，且反向 WS 模式无需公网 IP |
| **数据库** | **SQLite + aiosqlite** | 毕设场景单机部署，SQLite 零运维、便于打包演示；SQLModel 让换 PostgreSQL 只需改 `database.url` 一行 |
| **ORM** | **SQLModel** | 与 FastAPI 同作者、与 Pydantic 同源，模型定义即校验规则，避免 ORM 与 schema 两套定义 |
| **LLM 接入** | **OpenAI 兼容协议** | 只依赖一个事实标准，改 `base_url` 即可在 DeepSeek / 通义 / 智谱 / Ollama 之间切换，代码零改动 |
| **前端** | **原生 HTML/CSS/JS** | 不引入构建工具链。答辩现场 `python -m firefly.app` 一条命令就跑，不存在 `node_modules` 装不上、产物没构建的风险 |
| **图表** | **Canvas 2D 自绘** | 避免引入 ECharts 等大体积库；自绘还能精确控制二次元风格的配色与动画节奏 |
| **定时任务** | **APScheduler** | 与 asyncio 原生集成，`replace_existing=True` 支持配置热更新后重新排程 |
| **图片渲染** | **htmlrender + Playwright** | 复用同一套 HTML 模板出图，样式与网页端一致，避免用 Pillow 手写排版 |

**刻意不做的三件事**：

- ❌ **本地部署大模型** —— 本机 M1 16G 跑 7B 量化模型，抽取准确率撑不住叙事，
  且现场首次加载慢；走 API 更快更准；
- ❌ **裸 NL2SQL** —— 让模型直接写 SQL 既不可控也不可解释。本项目用**函数调用**：
  模型只负责抽取意图与参数，实际查询由预先写好的服务函数执行，安全且可审计；
- ❌ **前端引入构建工具链** —— 见上表。

### 数据模型

13 张表，统一 `ff_` 前缀。核心关系：

```
User ──┬── Account      资金账户（现金/银行卡/微信…）
       ├── Category     记账类别 ── tax_account ──→ 税务口径科目 ★
       ├── Transaction  流水（金额恒为正，方向由 direction 表达）★
       ├── Subscription 周期订阅（房租/宽带…自动入账）
       ├── Budget       预算（category_id 为空 = 总预算）
       ├── Report       报告（payload JSON 为唯一数据源）★
       ├── Suggestion    AI 建议（state 记录是否被采纳）★
       ├── AuditLog     审计日志（谁在什么渠道做了什么）
       ├── ImportBatch  导入批次记录
       └── SystemState  系统状态（如推送去重标记）

Report ── TaxCheckResult   税检结果（关联报告）
PolicyDoc                  政策知识库（供 RAG 使用）
```

**三个关键建模决策**：

**① `Transaction.amount` 恒为正数，方向由 `direction` 字段表达**

不采用「支出记负数、收入记正数」的方案。理由：聚合时反复出现
「这个 sum 该不该取绝对值」的判断，极易出错；而方向独立成枚举后，
`sum(amount) where direction=expense` 语义明确，`case()` 分支也好写。

**② `Category.tax_account` 把记账类别直接挂到税务口径**

```python
{"name": "餐饮", "tax_account": "业务招待费", "deductible": True}
{"name": "交通", "tax_account": "差旅费",     "deductible": True}
{"name": "医疗", "tax_account": "",           "deductible": False}
```

这样税务自检可以直接 `join Category` 汇总「可税前扣除的支出」，
**不需要在业务代码里维护一张「记账类别 → 税务科目」的映射表**。用户新增类别时，
AI 会建议它该归到哪个税务口径，人工确认后落库（P2 类别自演化）。

**③ `Suggestion.state` 是「建议采纳率」的唯一来源**

每条 AI 建议（分类建议、异常提醒、税务优化、预算提醒）落一行 `Suggestion`，
状态机为 `pending → adopted / rejected / ignored`。超时未处理的标记为 `ignored`
并**单独披露**，避免把「用户没看见」算成「用户不接受」而导致指标失真。

### 报告数据契约

`GET /api/reports/{id}` 返回的 `payload` 是前端与出图层共同消费的契约。结构如下：

```
payload
├── meta           周期标识、起止时间、生成时间、币种
├── overview       收支总览
│   ├── income / expense / net            本期三值
│   ├── net_assets                        净资产（只算 include_in_net 账户）
│   ├── balance                           账户余额合计（算全部账户）
│   ├── savings_rate / avg_daily_expense
│   └── compare.{income,expense,net}      环比 {value, ratio, direction}
│
├── structure      支出 / 收入结构
│   ├── expense_categories[]   {name, icon, color, amount, count, ratio}
│   ├── income_categories[]
│   ├── top_category
│   └── top_counterparties[]
│
├── accounts[]     账户明细 {id, name, kind, balance, include_in_net}
│
├── trend          趋势
│   ├── daily_expense[] / daily_income[]   {date, amount}
│   └── monthly[]                          近 6 期 {label, income, expense}
│
├── anomaly        异常统计 {scanned_count, anomaly_count,
│                           personal_mixed_count, anomaly_amount, ...}
│
├── attribution    归因（瀑布图）
│   ├── waterfall[]  首尾为 total，中间为 increase/decrease 类别
│   ├── contributions[]  每类 {current, previous, delta, contribution_ratio}
│   ├── delta_total / change_ratio
│   └── summary       自然语言结论
│
├── budget         预算执行 {total_budget, total_actual, items[], over_count}
│
├── tax            税务自检
│   ├── checks[]     {check_key, title, level, actual_value,
│   │                 threshold_value, description, suggestion, policy_basis} ★
│   ├── highest_level / risk_count / counts
│   ├── optimizations[]  非 info 项按等级降序
│   └── disclaimer       免责声明
│
├── optimization   优化方向
│   ├── action_items[]  7 类行动项，按 priority(high/medium/low) 排序
│   └── ...
│
└── highlights[]   前端顶部高亮卡 {label, value, text, tone}
                    tone ∈ {info, warning, positive, danger}
```

> ⚠️ **契约变更须知**：`payload` 的结构同时被 `index/js/report.js` 与
> `firefly/render/html.py` 消费。修改字段名必须**两处同步**，否则会出现
> 「网页正常但推送图片空白」这类静默故障。
> 前端在 `index/js/views.js` 的 `normalizeReportPayload()` 中做了一层兼容映射，
> 但那是防御手段，不应作为随意改字段的理由。

### 请求生命周期

以「生成一份报告」为例：

```
1. 浏览器  POST /api/transactions        （用户记一笔账）
       └─ api/transaction.py
            ├─ 未指定类别 → 按备注匹配类别名
            ├─ ledger.find_duplicates()    金额+时间窗口+对手方模糊匹配
            ├─ anomaly.analyze_transaction()  z-score + 个人消费关键词
            └─ audit.log_action()
       └─ db/session.py  get_session() 依赖 → 请求结束自动 commit ★

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

**关键点**：`db/session.py` 的 `get_session()` 是 FastAPI 依赖，它在 `yield` 之后
**显式 commit**，异常时 rollback。这一点看似琐碎，但若只写 `yield session`，
所有写操作会在请求结束时被静默回滚 —— 接口返回 200 而库里什么都没有。

---

## 配置说明

项目采用**三级配置优先级**，后者覆盖前者：

```
真实环境变量  >  .env 文件  >  config.yaml 默认值
```

### `config.yaml` —— 全局设置（前端可改）

根目录 `config.yaml` 存放所有非敏感配置，**其中大部分字段可在前端「设置」页直接修改**，
保存后立即写回文件并生效（无需重启）。

<div align="center">

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
| `push` | `enabled` `target_type` `target_id` `schedule_times` `content_type` `ai_summary` `image_scale` `image_max_height` | **推送**目标、时间、内容形态与出图精度 |
| `auth` | `enabled` `username` `session_hours` `max_attempts` | **登录鉴权**（详见下节） |
| `ui` | `theme` `accent_color` `particle_enabled` | 前端主题与动画 |

</div>

### 推送到协议端

报告页的「📤 推送到协议端」按钮不是一发即走，而是**先弹选项、再发送**。
这样做的原因很实际：同一个报告，给财务同事看要图（信息完整），
给自己快速扫一眼要文字（省流量、能搜索），
要不要附带 AI 解读则取决于这次是不是给人讲结论。把这些固定进配置，
用户每次都得先改配置再回来点，是把成本推给了用户。

| 选项 | 取值 | 说明 |
| --- | --- | --- |
| 推送类型 | `text` / `image` / `both` | 文字摘要、htmlrender 长图、或两者都发 |
| AI 总结 | 开关 | 开启后在规则摘要**之后**追加一段 AI 解读 |
| 温度 | 0 ~ 1.5 | 越低措辞越克制稳定，总结类任务建议 0.4 ~ 0.8 |
| 字数上限 | 64 ~ 4000 | 约束 AI 那一段的长度，不影响规则摘要 |

选项会记在浏览器本地，下次打开沿用上次的选择。定时推送（`schedule_times`）
则固定读取 `config.yaml` 的 `push.content_type` / `push.ai_summary`
等同名配置 —— 无人值守的任务没有「这次想怎么发」的语境。

> **规则摘要 + AI 解读，而不是让 AI 直接写摘要。**
> 数字由代码算、由代码排版，AI 只负责解释「为什么」和「怎么办」。
> 让大模型复述金额，出现幻觉时读者无法分辨哪句是真的 ——
> 财务场景下这个代价太大。

#### 关于出图清晰度

推送图会先经 htmlrender 截整页，再等比缩到协议端可接受的高度。
清晰度由三个参数共同决定，调参前先理解它们的关系：

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `push.image_width` | 800 | 截图视口宽度，应与出图版 HTML 的布局宽度匹配 |
| `push.image_scale` | 3 | 设备像素比，直接决定有效分辨率 |
| `push.image_max_height` | 6000 | 缩放的高度上限；**设得越小，降采样倍数越大，字越糊** |

一个反直觉但重要的结论：**高度上限是清晰度的主要瓶颈，而不是压缩算法**。
报告整页约 7058px 高，若上限设成 2400，就等于把 14px 正文降采样到 4.8px
（2.94 倍），再怎么优化 PNG 编码也救不回来；
上限设成 6000 时降采样降到 1.18 倍，正文等效 11.9px，清晰度提升约 2.5 倍。
实测 QQ 能正常接收 8000px 高的图，所以默认值放宽到了 6000。

> 早期这里踩过一个坑：出图复用了网页版的 1000px 布局
> （`render_report_html` 漏传 `for_image=True`），画布比必要宽度大了三成，
> 多出来的宽度在压缩时全部变成画质损失。
> 布局宽度不是无关紧要的样式细节，它直接决定最终清晰度。

> **演示模式（`app.debug`）**：设为 `true` 后前端**完全使用内置模拟数据**，
> 不发任何业务请求，并且**跳过登录**（本地数据本来就不需要后端）。
> 每次刷新都会重新生成一组随机数据，顶栏会亮起「演示模式 · 模拟数据」徽标。
> 答辩现场网络异常或数据库为空时，把它打开即可稳定演示全部界面。
>
> 反之，`app.debug: false`（默认）时数据**一律来自后端**：接口失败就显示
> 错误提示，**绝不悄悄用假数据顶替** —— 否则用户会把自己记的账和演示数据混在
> 一起。这条约束由 `index/js/api.js` 的 `withFallback` 强制：
> 只有在演示模式下才允许返回模拟数据。

### 登录鉴权

本项目自带登录保护，**默认开启**（`auth.enabled: true`），默认账号 `admin` /
口令 `Firefly2026`。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `auth.enabled` | `true` | 关闭后所有接口恢复免鉴权（仅建议本机演示时临时关闭） |
| `auth.username` | `admin` | 登录账号 |
| `auth.password` | `Firefly2026` | 明文口令，便于用户直接编辑 `config.yaml` |
| `auth.password_hash` | 自动生成 | PBKDF2-HMAC-SHA256 派生值，登录时实际比对的就是它 |
| `auth.password_salt` | 自动生成 | 每个部署独立的盐值 |
| `auth.secret_key` | 自动生成 | 令牌签名根密钥 |
| `auth.session_hours` | `12` | 登录态有效期（小时） |
| `auth.max_attempts` | `5` | 连续失败多少次后锁定 |
| `auth.lockout_minutes` | `15` | 锁定时长（分钟） |
| `auth.require_strong_password` | `true` | 改口令时是否强制强度校验 |

**安全设计要点**（答辩可讲）：

- **令牌存 HttpOnly Cookie**，前端 JS 完全读不到 —— 即使发生 XSS 也偷不走登录态。
  同时兼容 `Authorization: Bearer <token>`，方便 curl / 脚本调用。
- **改口令必须验证原口令**。若只凭登录态就能改口令，一旦令牌被窃取（比如被贴进
  聊天记录），攻击者可以直接改掉口令把主人锁在门外。
- **登录失败提示不区分「用户不存在」与「口令错误」**，避免账号枚举。
- **口令比对用 `hmac.compare_digest` 恒定时间比较**，防止通过响应时间逐字节爆破哈希。
- **`password_hash` / `password_salt` / `secret_key` 不允许经 `/api/settings` 写入**，
  详见 [配置写入白名单](#配置写入白名单)。

#### 修改口令

两种方式：

1. **前端「设置」页 → 修改登录口令**：需填原口令，带实时强度提示（至少 8 位且
   包含大写/小写/数字/符号中的三类）。
2. **直接编辑 `config.yaml`**：改 `auth.password` 后**删除 `password_hash` 与
   `password_salt`** 两行，重启服务会自动按新口令重建哈希。

> ⚠️ 只改 `auth.password` 而不删哈希是**无效**的 —— 登录只比对哈希。系统会把明文
> 同步写回以便用户查看，但哈希才是权威值。

#### 配置写入白名单

`/api/settings` 的写入受两级白名单约束，两者都由代码显式声明：

| 集合 | 位置 | 含义 |
| --- | --- | --- |
| `EDITABLE_FIELDS` | `firefly/config.py` | 前端设置页**可以**修改的字段（约 56 个点号键） |
| `SYSTEM_MANAGED_FIELDS` | `firefly/config.py` | 仅系统内部可写（口令哈希/盐/令牌密钥），API 永远拒绝 |
| `SENSITIVE_DISPLAY_FIELDS` | `firefly/config.py` | 读取设置时一律脱敏为 `******` 的字段 |

系统自身写这些字段时走 `update_config(patch, allow_system=True)`；`/api/settings`
的调用方**拿不到这个开关**。

> 这里曾经有过一个真实漏洞：早期只在「扁平写法」（`{"auth.secret_key": "x"}`）
> 分支上做白名单校验，而「嵌套写法」（`{"auth": {"secret_key": "x"}}`）会绕过
> 检查直接写入 —— 实测可以把 `secret_key` 覆盖成攻击者指定的值，从而伪造任意
> 登录令牌。修复方式是**让校验覆盖所有输入语法形态**。教训：白名单漏一种写法
> 就等于没有白名单。

### `.env` —— 敏感值（不进版本库）

`.env` 存放密钥类配置，**已在 `.gitignore` 中排除**：

```ini
LLM_API_KEY=sk-xxx          # 大模型密钥（留空则降级为规则引擎）
LLM_BASE_URL=               # 可选：覆盖供应商地址
LLM_MODEL=                  # 可选：覆盖模型名
ONEBOT_ACCESS_TOKEN=        # 协议端鉴权令牌
ONEBOT_WS_URL=              # 正向 WS 地址（留空则用反向 WS）
SUPERUSERS=                 # 超级用户 QQ 号，逗号分隔
```

### 切换 LLM 供应商

只需修改 `config.yaml` 的 `llm.base_url` 与 `llm.model`，代码无需改动：

| 供应商 | `base_url` | `model` 示例 |
| --- | --- | --- |
| DeepSeek（默认） | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 阿里通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` |

---

## 项目结构

```
firefly_bot/
├── config.yaml              # 全局配置（前端可改）
├── .env                     # 敏感配置（不进版本库）
├── .env.example             # 敏感配置模板
├── requirements.txt         # Python 依赖清单
├── todo.md                  # 架构设计与路线图
│
├── firefly/                 # 后端主包
│   ├── app.py               # ★ 入口：初始化 NoneBot、挂载前端、打印横幅
│   ├── config.py            # 配置加载 / 校验 / 热更新
│   │
│   ├── db/                  # 数据层
│   │   ├── models.py        #   SQLModel 表定义（13 张表）
│   │   └── session.py       #   异步引擎与会话管理
│   │
│   ├── api/                 # Web API 层（FastAPI，53 个接口）
│   │   ├── router.py        #   路由聚合
│   │   ├── deps.py          #   依赖注入（当前用户、统一响应体）
│   │   ├── meta.py          #   元信息与健康检查
│   │   ├── transaction.py   #   流水增删改查
│   │   ├── category.py      #   类别管理
│   │   ├── stats.py         #   统计聚合
│   │   ├── report.py        #   报告生成与归档
│   │   ├── taxcheck.py      #   税务自检
│   │   ├── budget.py        #   预算
│   │   ├── audit.py         #   审计日志与建议采纳率
│   │   ├── importer.py      #   CSV / 账单导入
│   │   ├── ai.py            #   自然语言记账与对话
│   │   ├── push.py          #   推送控制
│   │   ├── subscription.py  #   周期订阅
│   │   └── settings.py      #   配置读写
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
│   │   └── importer.py      #   账单解析与去重
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
├── index/                   # 前端（原生 HTML/CSS/JS，无构建工具）
│   ├── index.html           #   单页外壳
│   ├── css/main.css         #   二次元风格样式
│   └── js/
│       ├── app.js           #   Hash 路由与启动流程
│       ├── api.js           #   接口封装与降级
│       ├── utils.js         #   Canvas 图表与动画
│       ├── views.js         #   总览 / 记账视图
│       ├── report.js        #   报告视图（动画渲染）
│       ├── collector.js     #   财务数据收集器
│       ├── assistant.js     #   对话助手
│       ├── bot-status.js    #   协议端状态面板
│       ├── budget-settings.js # 预算与设置
│       ├── mock.js          #   模拟数据
│       └── particles.js     #   萤火粒子背景
│
├── data/                    # 运行时数据（不进版本库）
│   ├── firefly.db           #   SQLite 数据库
│   └── reports/             #   报告 HTML 与 PNG
│
└── tests/                   # 测试
```

**架构要点**：`services/` 层完全不依赖 FastAPI，因此 Web 端与 Bot 端共享同一套业务逻辑，
不存在「网页能算、机器人算不出」的不一致。

---

## 功能地图

按**投入产出比**排序的实现状态：

<details open>
<summary><b>P0 · 核心闭环（已完成）</b></summary>

| 编号 | 功能 | 状态 |
| --- | --- | --- |
| 0-1 | 工程骨架与配置系统 | ✅ |
| 0-2 | 数据模型（13 张表） | ✅ |
| 0-3 | 前后端同端口挂载 | ✅ |
| 0-4 | 二次元风格前端 | ✅ |
| 0-5 | 财务数据收集器 | ✅ |
| 0-6 | 周期报告引擎（自定义周期） | ✅ |
| 0-7 | 报告动画渲染 | ✅ |
| 0-8 | htmlrender 静态出图 | ✅ |
| 0-9 | 协议端定时推送 | ✅ |

</details>

<details open>
<summary><b>P1 · 智能化（已完成）</b></summary>

| 编号 | 功能 | 状态 |
| --- | --- | --- |
| 1-1 | 自然语言记账 | ✅ |
| 1-2 | 智能分类（冷启动 + 纠正） | ✅ |
| 1-3 | 异常检测（z-score + 关键词） | ✅ |
| 1-4 | 预算 vs 实际 | ✅ |
| 1-5 | 归因分析（瀑布图） | ✅ |
| 1-6 | 税务风险自检清单 | ✅ |
| 1-7 | 对话式查询（函数调用） | ✅ |
| 1-8 | 阈值主动预警 | ✅ |
| 1-9 | 审计日志 + 建议采纳率 | ✅ |

</details>

<details>
<summary><b>P2 · 增强（部分完成）</b></summary>

| 编号 | 功能 | 状态 |
| --- | --- | --- |
| 2-1 | 账单 CSV / OCR 批量导入 + 去重 | ✅ |
| 2-2 | 政策知识库 RAG | ⏳ |
| 2-3 | 周期订阅自动入账 | ✅ |
| 2-4 | 类别自演化（LLM 建议税务科目） | ✅ |
| 2-5 | 报告归档与历史对比 | ✅ |
| 2-6 | 可视化大屏 | ⏳ |

</details>

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

在聊天窗口 @机器人 或私聊发送：

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

---

## API 接口文档

交互式文档（Swagger UI）：**http://127.0.0.1:2525/docs**
OpenAPI 描述：**http://127.0.0.1:2525/api/openapi.json**

> NoneBot 的 FastAPI driver 默认关闭了 `docs_url` / `openapi_url`，
> 本项目在 `firefly/app.py` 中显式补回了这两个路由。

### 约定

**所有业务接口挂在 `/api` 前缀下。**

**成功响应**统一包装为：

```json
{ "success": true, "data": { ... } }
```

部分列表接口额外带元字段：

```json
{ "success": true, "data": [ ... ], "total": 42 }
```

**失败响应**统一为：

```json
{ "success": false, "message": "错误描述", "code": 400, "detail": null }
```

**时间格式**：交换统一用 `YYYY-MM-DD HH:MM` 或 `YYYY-MM-DD`（也接受 ISO 8601），
后端解析为带时区的 datetime 并以 **UTC** 存储，展示时按 `app.timezone`（默认 Asia/Shanghai）呈现。

**认证**：除 `/api/health`、`/api/bootstrap`、`/api/auth/*` 与 `/api/openapi.json`
外，所有接口都需要登录态。
浏览器端由 HttpOnly Cookie 自动携带；脚本调用可显式带令牌：

```bash
# 1. 取令牌
TOKEN=$(curl -s -X POST http://127.0.0.1:2525/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Firefly2026"}' | jq -r '.data.token')

# 2. 带令牌调用
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:2525/api/stats
```

未携带或令牌无效时返回 `401`：

```json
{"success": false, "message": "未登录或登录已过期，请重新登录", "code": 401}
```

> 前端收到 `401` 会自动弹出登录遮罩，无需各页面单独处理。


### 接口总览

共 **57** 个业务接口，按功能分组：

#### 登录鉴权

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/auth/status` | 鉴权是否启用、是否仍用默认口令、会话时长与锁定策略（**免登录**） |
| `POST` | `/api/auth/login` | 登录；成功写 HttpOnly Cookie 并返回令牌 |
| `GET` | `/api/auth/me` | 当前登录用户（未登录返回 401，前端据此决定是否弹遮罩） |
| `POST` | `/api/auth/logout` | 登出；清除 Cookie |
| `POST` | `/api/auth/change-password` | 修改口令；**必须提供原口令**，成功后换发新令牌 |

#### 元信息

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查；探测数据库连通性，返回 `llm_ready`（**免登录**） |
| `GET` | `/api/bootstrap` | **登录前**的引导信息：应用名、版本、主题、演示模式开关（**免登录**） |
| `GET` | `/api/meta` | 应用元信息（名称、版本、端口、币种、UI 配置、演示模式标记、当前用户） |

> **为什么 `bootstrap` 与 `meta` 要分成两个接口**：前端必须在
> **发出任何业务请求之前**就知道 `app.debug`（演示模式），否则会出现
> 「配置里开着演示模式、页面却仍去打真实接口」的错位。
> 而 `/api/meta` 带当前用户名与税率口径等业务信息，不能免鉴权。
> 所以把「纯展示配置」单独拆成 `/api/bootstrap`——它**刻意不含**
> 用户、税率、推送目标等任何业务字段。
>
> 这个拆分不是过度设计：早期版本让前端用 `/api/meta` 来设置 debug 开关，
> 未登录时该接口返回 401，于是 `debug` 永远没被设上，
> 配置里的演示模式**完全失效**。

#### 流水

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/transactions` | 列表；支持 `start` `end` `category_id` `direction` `keyword` `only_pending` `only_anomaly` `limit` `offset` 过滤 |
| `POST` | `/api/transactions` | **新增**；自动去重 + 异常检测 + 审计日志 |
| `GET` | `/api/transactions/{txn_id}` | 详情 |
| `PATCH` | `/api/transactions/{txn_id}` | 局部更新 |
| `DELETE` | `/api/transactions/{txn_id}` | 删除 |
| `POST` | `/api/transactions/{txn_id}/confirm` | 确认待入账记录 |

#### 类别

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/categories` | 列表；可按 `direction` 过滤 |
| `POST` | `/api/categories` | 新增（可带 `tax_account` 与 `deductible` 挂接税务口径） |
| `DELETE` | `/api/categories/{category_id}` | 删除；系统内置类别与已被引用的类别拒绝删除 |

#### 统计

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/stats` | **总览聚合**：收支、环比、分类结构、日趋势、多期趋势、账户、异常统计 |
| `GET` | `/api/anomalies` | 异常流水；`mode=all\|anomaly\|personal` |
| `GET` | `/api/budget-status` | 预算执行状态 |

#### 报告

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/reports/generate` | **生成报告**（核心接口） |
| `GET` | `/api/reports/latest` | 最新一份**已完成**的报告 |
| `GET` | `/api/reports` | 归档列表（不含 payload，减小体积） |
| `GET` | `/api/reports/{report_id}` | 报告详情（含完整 payload） |
| `GET` | `/api/reports/{report_id}/html` | 返回报告 HTML（可直接在浏览器看） |
| `GET` | `/api/reports/{report_id}/preview` | 返回 HTML 文件路径与访问 URL |
| `POST` | `/api/reports/{report_id}/push` | 手动推送到协议端 |

#### 税务自检

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/tax-check` | 执行税检（可传周期） |
| `GET` | `/api/tax-check/checks` | 历史税检结论列表；`limit` 默认 20（**按时间倒序的明细行**，非最近一次快照） |
| `GET` | `/api/tax-check/policy` | **政策依据库**：返回全部检查项对应的政策原文引用 |

#### 预算

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/budgets` | 预算列表 + 执行摘要 |
| `POST` | `/api/budgets` | 新增预算（同范围重复创建会覆盖旧记录） |
| `DELETE` | `/api/budgets/{budget_id}` | 删除 |

#### AI 能力

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/ai/status` | AI 引擎状态（是否就绪、模型名） |
| `POST` | `/api/ai/natural` | **自然语言记账**：文本 → 结构化条目 |
| `POST` | `/api/ai/nl-confirm` | 确认并入库上一步抽出的条目 |
| `POST` | `/api/ai/chat` | 对话式查询（函数调用） |
| `POST` | `/api/ai/classify` | 对备注做智能分类 |
| `GET` | `/api/ai/categories/suggest` | 类别自演化：建议新类别归属的税务口径 |

#### 账单导入

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/import/csv` | CSV 账单导入；`dry_run=true` 只解析不入库 |
| `POST` | `/api/import/ocr` | 截图账单 OCR 识别 |
| `POST` | `/api/import/reparse` | 直接解析一段账单文本（粘贴入口） |
| `GET` | `/api/import/batches` | 导入批次历史 |

#### 审计与建议

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/audit/stats` | **建议采纳率统计**（论文指标来源） |
| `GET` | `/api/audit/logs` | 操作日志；可按 `channel` 过滤 |
| `GET` | `/api/audit/suggestions` | 建议列表；可按 `state` `kind` 过滤 |
| `POST` | `/api/audit/suggestions/{suggestion_id}/resolve` | 标记建议为已采纳 / 已拒绝 |

#### 推送与订阅

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/push/status` | 推送配置 + **协议端连接状态** |
| `POST` | `/api/push/test` | 发送测试消息 |
| `POST` | `/api/push/latest` | 推送最新报告 |
| `GET` | `/api/subscriptions` | 订阅列表 |
| `POST` | `/api/subscriptions` | 新增订阅 |
| `DELETE` | `/api/subscriptions/{sub_id}` | 删除 |
| `GET` | `/api/subscription/status` | 订阅调度状态与下次执行时间 |
| `PUT` | `/api/subscription/status` | 开关订阅调度（写回配置并重新排程） |

#### 设置

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/settings` | 读取当前配置（敏感字段脱敏为 `******`） |
| `GET` | `/api/settings/schema` | 可编辑字段白名单 + 只读字段 |
| `PUT` | `/api/settings` | 更新配置；**同时支持扁平点号键与嵌套对象** |

### 核心接口详解

#### 1. 新增流水

`POST /api/transactions`

请求体：

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
| `amount` | number | ✅ | **始终为正数**，方向由 `direction` 决定 |
| `direction` | string | | `expense`（默认）/ `income` |
| `category_id` | int \| string | | 不传时按 `memo` 中的类别名自动匹配 |
| `account_id` | int \| string | | 资金账户 |
| `trade_time` | string | | 缺省为当前时间；支持 `YYYY-MM-DD HH:MM` 等格式 |
| `counterparty` | string | | 交易对手方，参与去重模糊匹配 |
| `memo` | string | | 备注 |
| `has_invoice` | bool | | 是否已取得发票（影响税前扣除提示） |
| `is_confirmed` | bool | | 默认 `true`；为 `false` 时进入待确认 |

响应：

```json
{
  "success": true,
  "data": {
    "transaction": {
      "id": 13,
      "amount": 38.5,
      "direction": "expense",
      "trade_time": "2026-10-12T04:30:00+00:00",
      "memo": "打车去客户公司",
      "is_duplicate": false,
      "is_anomaly": false
    },
    "duplicate": null,
    "anomaly": { "is_anomaly": false, "reasons": [] }
  }
}
```

> **副作用**：该接口会自动执行**去重检测**（金额一致 + 时间窗口内 + 对手方模糊相似度 ≥ 80）
> 与**异常检测**（同类别历史 z-score），并在响应中返回判定结果，同时写入审计日志。

#### 2. 生成报告

`POST /api/reports/generate`

请求体：

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
| `period` | string | `daily` / `weekly` / `monthly`（默认）/ `quarterly` / `yearly` / `custom` |
| `reference` | string | 基准日期，不传取今天；用于回看历史周期 |
| `custom_days` | int | `period=custom` 时的天数 |
| `custom_start` / `custom_end` | string | 也可直接指定起止日期 |
| `push` | bool | 是否生成后立即推送（默认 `false`） |

响应（**已省略 payload 内部细节，结构见「报告数据契约」**）：

```json
{
  "success": true,
  "data": {
    "id": 3,
    "title": "2026年10月 财税报告",
    "period": "monthly",
    "period_start": "2026-10-01T00:00:00+08:00",
    "period_end": "2026-11-01T00:00:00+08:00",
    "status": "completed",
    "elapsed_ms": 47,
    "summary_text": "【2026年10月 财税报告】\n收入 ¥26,800.00 …",
    "payload": { "meta": {...}, "overview": {...}, "tax": {...}, "...": "..." }
  }
}
```

> `summary_text` 是可直接发到聊天窗口的**纯文本摘要**，
> `payload` 是给前端做动画渲染的结构化数据。

#### 3. 自然语言记账（两步式）

**第一步：抽取** —— `POST /api/ai/natural`

```json
{ "text": "昨天打车 38，请客户吃饭 260", "auto_create": false }
```

响应：

```json
{
  "success": true,
  "data": {
    "items": [
      { "amount": 38.0, "direction": "expense", "category": "交通",
        "memo": "打车", "trade_time": "2026-10-11 00:00:00",
        "confidence": 0.62, "source": "rule" },
      { "amount": 260.0, "direction": "expense", "category": "餐饮",
        "memo": "请客户吃饭", "confidence": 0.72, "source": "rule" }
    ],
    "engine": "rule"
  }
}
```

**第二步：确认** —— `POST /api/ai/nl-confirm`

```json
{
  "items": [
    { "amount": 38.0, "direction": "expense", "category": "交通",
      "memo": "打车", "trade_time": "2026-10-11 00:00:00" }
  ]
}
```

> **为什么要拆成两步**：模型抽取可能出错（金额看错、类别判错）。
> 先返回给用户确认再写库，避免 AI 幻觉直接污染账本。
> `auto_create: true` 时会在第一步直接入库，适合机器人群聊场景。

#### 4. 对话式查询

`POST /api/ai/chat`

```json
{ "question": "本月餐饮花了多少？", "history": [] }
```

响应：

```json
{
  "success": true,
  "data": {
    "answer": "本月餐饮支出合计 ¥260.00，共 1 笔，占总支出的 9.2%。",
    "intent": "sum_by_category",
    "chart": { "type": "bar", "data": [ { "name": "餐饮", "value": 260.0 } ] }
  }
}
```

> **实现方式**：模型只做**意图识别与参数抽取**，实际查询由后端预置的服务函数执行
> （`sum_by_category` / `list_recent` / `compare_period` 等）。
> 不做裸 NL2SQL —— 既安全可控，也便于在论文中解释。

#### 5. 建议采纳率

`GET /api/audit/stats`

```json
{
  "success": true,
  "data": {
    "total": 24,
    "by_state": { "pending": 8, "adopted": 11, "rejected": 3, "ignored": 2 },
    "adoption_rate": 78.57,
    "decided_count": 14,
    "pending_count": 8,
    "ignored_count": 2,
    "by_kind": {
      "anomaly": { "total": 6, "adoption_rate": 83.33 },
      "tax_optimize": { "total": 5, "adoption_rate": 80.0 }
    }
  }
}
```

> **指标口径**：`adoption_rate = adopted / (adopted + rejected)`。
> `ignored`（超时未处理）**不计入分母**，而是单独以 `ignored_count` 披露。
> 这样避免把「用户没看见」算成「用户拒绝」或「用户接受」而污染指标。

`POST /api/audit/suggestions/{id}/resolve`

```json
{ "adopted": true }
```

#### 6. CSV 账单导入

`POST /api/import/csv`（`multipart/form-data`）

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `file` | file | CSV 文件（≤ 8 MB） |
| `auto_confirm` | bool | 是否自动确认入库（默认 `true`） |
| `dry_run` | bool | **只解析不入库**，用于导入前预览 |

> **设计理由**：账单导入最大的痛点是「导错账要一条条删」，
> 所以提供 `dry_run` 预览；同时**去重优先于入库**，
> 同一份账单重复导入时只会计入 `duplicate_count` 而不产生脏数据。
>
> 已知处理能力：微信/支付宝账单的说明行跳过、`收/支` 与 `交易类型` 列区分、
> `不计收支` 记录跳过、**未加引号的千分位逗号**（`¥1,299.00`）自动修复。

### 错误处理

| HTTP | 场景 | 响应示例 |
| --- | --- | --- |
| `400` | 参数非法 | `{"success":false,"message":"未从文件中解析出任何账目…","code":400}` |
| `404` | 资源不存在 | `{"success":false,"message":"流水不存在","code":404}` |
| `422` | Pydantic 校验失败 | FastAPI 标准格式 `{"detail":[{"loc":["body","amount"],"msg":"Input should be greater than 0"}]}` |
| `500` | 服务端异常 | 检查终端日志，堆栈会完整打印 |

**排查建议**：接口返回 200 但业务失败时，务必检查响应体的 `success` 字段 ——
本项目所有业务异常都以 `success: false` 返回，而非依赖 HTTP 状态码。

---

---

## 常见问题

<details>
<summary><b>安装时提示 <code>Could not find a version that satisfies nonebot2</code></b></summary>

Python 版本过低。NoneBot2 2.5 要求 `>=3.10,<4.0`。

```bash
python -V    # 确认版本
```

若确实是 3.9，请按前文用 Conda 或安装新版 Python 重建环境。
**macOS 自带的 `/usr/bin/python3` 就是 3.9，不可直接使用。**

> 补充：在 3.9 上 `pip install -r requirements.txt` 有时**不会报错**，
> 而是静默降级到 nonebot2 2.4.4、pandas 2.x 等旧版本。
> 这种"装得上但版本不对"的情况更危险，请用 `pip list` 核对版本。
</details>

<details>
<summary><b>启动报错 <code>SettingsError: error parsing env var "superusers"</code></b></summary>

`.env` 里存在一个**空值**的 `SUPERUSERS=`。

NoneBot 会在启动早期直接读取 `.env`（早于本项目代码），并把 `SUPERUSERS` 当作
JSON 数组解析，空字符串会导致解析失败。

**解决**：把该行注释掉，或填入真实 QQ 号（两种写法均可）：

```ini
# SUPERUSERS=
SUPERUSERS=123456789,987654321
# 或 NoneBot 原生 JSON 写法
# SUPERUSERS=["123456789","987654321"]
```

`.env.example` 中的该项已默认注释，正常 `cp` 不会触发此问题。
</details>

<details>
<summary><b>启动报错 <code>ValueError: NoneBot has not been initialized</code></b></summary>

这是**正常现象**，出现在单独导入 `nonebot_plugin_htmlrender` 或 `nonebot_plugin_apscheduler` 时。
它们是 NoneBot **插件**而非普通库，必须在 NoneBot 初始化后才能导入。
通过 `python -m firefly.app` 正常启动不会有此问题。
</details>

<details>
<summary><b>推送图片时报 Playwright 相关错误</b></summary>

未安装浏览器内核：

```bash
playwright install chromium
```

Linux 若还缺系统库：

```bash
playwright install-deps chromium
```
</details>

<details>
<summary><b>端口 2525 被占用</b></summary>

改 `config.yaml`：

```yaml
server:
  port: 2526
```

或前端「设置」页直接修改并保存。
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
记一条 error，无法从 JS 侧消除。

正确的验收方式是：**登录之后**再统计错误。登录后遍历全部 6 个视图应为
「0 JS 错误、0 个 HTTP ≥ 400」。
</details>

<details>
<summary><b>登录后顶栏看不到用户名 / 退出按钮</b></summary>

`index/js/auth.js` 的 `renderTopbarUser()` 依赖顶栏操作区的类名。
若以后调整 `index/index.html` 的类名（当前是 `.nav-actions`），
该函数找不到容器就会**静默返回**、按钮凭空消失。
函数里已保留 `.topbar-actions` 作为回退选择器，改版时请同步核对。

> 这是一个真实踩过的坑：重写顶栏时把 `.topbar-actions` 改成了 `.nav-actions`，
> 而 CSS 里原本根本没有 `.topbar` / `.topbar-inner` /
> `.topbar-actions` 这几条规则 —— 顶栏因此完全没样式、操作区被挤到第二行。
> 动手改 UI 前，建议先核对「HTML 用到的类名」与「CSS 实际定义的类名」是否一致。
</details>

---

## 论文可量化指标

本项目的设计刻意保留了可测量的实验接口，供论文实证章节使用：

| 指标 | 数据来源 | 实验设计建议 |
| --- | --- | --- |
| **自然语言抽取准确率** | `Transaction.confidence` + `raw_input` | 构造 200 条口语化语句测试集，人工标注对比 |
| **分类准确率** | `Suggestion` vs 用户最终类别 | 对比「LLM 冷启动」与「累积纠正样本后」两组的准确率提升 |
| **异常检出率 / 误报率** | `Transaction.is_anomaly` | 注入已知异常样本，统计 Precision / Recall |
| **建议采纳率** ⭐ | `Suggestion.state` + `audit.get_adoption_stats()` | **对照组实验**：有 AI 建议 vs 无建议，直接构成论文章节 |
| **报告生成耗时** | `Report.elapsed_ms` | 统计不同数据量下的耗时曲线 |

> **建议采纳率**是最有说服力的指标：系统记录每一条 AI 建议（分类、异常、税务优化、预算）
> 及用户是否采纳，并把超时未处理的标记为 `ignored` 单独披露，避免指标注水。

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
<img width="1284" height="2283" alt="微信图片_20261013231908_10_72" src="https://github.com/user-attachments/assets/75d0454b-eea8-43dd-a5b9-d7442a4ff01d" />



## 免责声明

本项目的税务自检结果依据公开政策规则自动生成，**仅供参考，不构成专业税务意见**。
实际申报请以主管税务机关的口径为准，并咨询专业税务顾问。

---

## 许可

MIT License · 仅用于学习与研究

<div align="center">

**流萤 Firefly** · 让每一笔账都有回响 ✨

</div>
