# 流萤 Firefly · 前端结构说明

本目录为「流萤」的**原生前端**（HTML / CSS / JavaScript），不依赖任何构建工具，
由后端 FastAPI 通过 `StaticFiles(directory="index", html=True)` 挂载在同端口根路径 `/` 下。

## 目录结构

```
index/
├── index.html              单页外壳：统一左侧导航 / 工作区 / 视图挂载点 / 页脚 / 全局组件根
├── css/
│   ├── main.css            设计系统（变量层 / 基础层 / 布局 / 组件 / 动画 / 响应式）
│   └── workspace.css       工作区视觉层：玻璃材质 / 折叠侧栏 / 手机抽屉 / 深浅主题
├── js/
│   ├── utils.js            工具库：DOM、货币/日期格式化、图表封装、Toast、Modal、存储
│   ├── mock.js             离线示例数据（后端未启动时保证前端可独立演示）
│   ├── api.js              后端 API 封装 + 自动降级（失败即切 Mock）
│   ├── particles.js        萤火粒子背景（Canvas）
│   ├── views.js            总览 / 报告 视图
│   ├── transactions.js     流水管理：筛选分页、增删改查、前后快照操作日志
│   ├── collector.js        记账视图（手动记账 + AI 自然语言记账 + 流水列表）
│   ├── report.js           报告视图（动画渲染 + 归因瀑布图 + 税检清单）
│   ├── assistant.js        AI 助手视图（对话式查询）
│   ├── budget-settings.js  预算管理 + 系统设置视图
│   └── app.js              Hash 路由、导航、主题、健康检查、启动流程
├── pages/                  （预留）独立子页面
├── mock/                   （预留）额外静态数据文件
└── assets/                 （预留）图片等静态资源
```

## 设计要点

### 1. 无构建工具
全部使用原生 HTML / CSS / JavaScript 与 `<script>` 顺序加载，不需要 npm install。
使用项目根目录的 `python -m firefly.app` 启动完整应用。
演示数据需要 `config.yaml` 的 `app.debug = true`；正式模式下请求失败展示错误，避免把模拟金额误认为真实财务数据。

### 2. 数据降级策略
`api.js` 中每个接口都经过 `withFallback(realCall, mockCall)` 包装：
- `app.debug = false` → 走真实接口，失败显示错误，不自动换成模拟金额
- `app.debug = true` → 使用 Mock 数据，并通过侧栏标记和 toast 明确提示演示状态

数据来源策略保持在 `api.js`，视觉层不会更改鉴权、业务接口或财务计算。

### 3. 动画渲染
报告页面采用「生成中 → 分块揭示」的动画编排：
- 萤火粒子加速聚集（加载态）
- 数字滚动计数（`FF.animateCount`，重复启动会取消旧任务，隐藏视图收敛到最终值）
- 卡片错峰淡入（共享 IntersectionObserver + `.reveal`，清空 DOM 时释放观察）
- 原生 Canvas 图表（共享 ResizeObserver，在窗口缩放、侧栏折叠与主题切换时重绘）
- 税检清单逐条滑入、风险条按等级填充

粒子缓存光晕纹理，按时间差推进，桌面绘制上限约 30fps、窄屏约 24fps，切到后台停止。
系统启用「减少动态效果」时粒子静态呈现，计数直接显示最终值，报告跳过扫描动画。
报告综述完整呈现，聊天仍保留限时打字反馈。

### 4. 二次元风格
- 暗色「深空」主题（`--bg-deep: #0a0e1a`）+ 萤火虫青色强调（`--firefly: #5eead4`）
- 极光渐变背景 + Canvas 粒子萤火
- 半透明玻璃材质、克制高光、柔和边框和轻微悬停反馈
- 统一 SVG 侧栏图标，保留功能模块原有的 emoji 与中文文案
- 支持亮色主题切换，同时提供 `prefers-reduced-motion` 无障碍降级

### 5. 图表
使用 `utils.js` 中的原生 Canvas 图表，不引入额外依赖。
图表颜色从 CSS 变量读取，主题切换只重绘图表，保留当前页面和表单草稿。
横轴标签数量依据可用宽度调整，窄屏瀑布图金额使用紧凑格式。

### 6. 左侧工作区导航

- 九个页面共用一套左侧导航，当前页通过 `aria-current` 标记。
- 桌面可折叠为图标栏，折叠状态保存在 `firefly-sidebar`；图标保留可访问名称和提示。
- 760px 及以下使用左侧抽屉，支持遮罩关闭、Escape、焦点循环和焦点恢复。
- 抽屉关闭时侧栏不可聚焦；打开时背景工作区不可交互。
- `Alt+1` 至 `Alt+9` 按导航顺序切换；「跳至主要内容」不修改 Hash 路由。
- 每次重渲染使用独立挂载节点，旧请求不会覆盖新页面；报告生成状态也限定于对应页面实例。

浏览器验证覆盖：桌面与手机七个页面、320px/390px 窄屏、深浅主题、侧栏折叠与图表宽度、报告快速切页后重入、表单草稿保留、确认框取消回车以及 Escape 关闭导航。使用演示数据，真实后端保存、鉴权与协议端推送仍需在实际部署中联调。

预览截图：[桌面](../docx/frontend-desktop.png) · [手机](../docx/frontend-mobile.png)。

## 与后端的接口约定

| 前端调用 | 后端路由 | 说明 |
| --- | --- | --- |
| `api.health()` | `GET /api/health` | 健康检查 |
| `api.meta()` | `GET /api/meta` | 应用名 / 版本 / 主题 / 周期选项 |
| `api.stats()` | `GET /api/stats` | 总览统计 |
| `api.transactions.list()` | `GET /api/transactions` | 流水列表 |
| `api.transactions.create()` | `POST /api/transactions` | 新增流水 |
| `api.transactions.remove()` | `DELETE /api/transactions/{id}` | 删除流水 |
| `api.categories.list()` | `GET /api/categories` | 类别列表 |
| `api.reports.generate()` | `POST /api/reports/generate` | 生成周期报告 |
| `api.reports.latest()` | `GET /api/reports/latest` | 最近报告 |
| `api.reports.list()` | `GET /api/reports` | 报告归档 |
| `api.assistant.natural()` | `POST /api/ai/natural` | 自然语言记账 |
| `api.assistant.chat()` | `POST /api/ai/chat` | 对话式查询 |
| `api.anomalies.list()` | `GET /api/anomalies` | 异常列表 |
| `api.audit.stats()` | `GET /api/audit/stats` | 建议采纳率 |
| `api.taxCheck.run()` | `POST /api/tax-check` | 税务自检 |
| `api.budgets.list()` | `GET /api/budgets` | 预算列表 |
| `api.budgets.create()` | `POST /api/budgets` | 新增预算 |
| `api.budgets.remove()` | `DELETE /api/budgets/{id}` | 删除预算 |
| `api.getSettings()` | `GET /api/settings` | 读取配置 |
| `api.updateSettings()` | `PUT /api/settings` | 写回 config.yaml |
| `api.subscription.status()` | `GET /api/subscription/status` | 推送订阅状态 |
| `api.subscription.update()` | `PUT /api/subscription/status` | 更新推送订阅 |

### v1.0.1 新模块

- `js/excel-import.js`：模板、填写规范、默认账本、上传与拖入、校验预览、入账及通知结果。使用真实接口，演示模式关闭导入入口。
- `js/system.js`：版本信息卡片与显式仓库更新操作。
- `assets/templates/firefly-import.xlsx`：模板下载接口使用的空白模板。

侧栏显示运行版本。Excel 入账后使财务视图缓存失效，下次访问读取新流水。Alt+1 至 Alt+9 按八个页面的侧栏顺序切换。

## v1.0.2 交互约定

自然语言结果以 category / category_id 匹配收支类别，下拉框显示值与提交值一致，has_invoice 为独立可编辑字段。流水管理使用真实接口，演示模式禁用；编辑用 PATCH，显式 null 清空归属。新增、修改、删除后广播 firefly:transactions-changed，使财务视图缓存失效。原生 dialog 提供焦点约束和 Escape 关闭，所有用户文本按 textContent 展示。
