# 流萤 Firefly · 前端结构说明

本目录为「流萤」的**原生前端**（HTML / CSS / JavaScript），不依赖任何构建工具，
由后端 FastAPI 通过 `StaticFiles(directory="index", html=True)` 挂载在同端口根路径 `/` 下。

## 目录结构

```
index/
├── index.html              单页外壳：顶栏 / 导航 / 视图挂载点 / 页脚 / 全局组件根
├── css/
│   └── main.css            设计系统（变量层 / 基础层 / 布局 / 组件 / 动画 / 响应式）
├── js/
│   ├── utils.js            工具库：DOM、货币/日期格式化、图表封装、Toast、Modal、存储
│   ├── mock.js             离线示例数据（后端未启动时保证前端可独立演示）
│   ├── api.js              后端 API 封装 + 自动降级（失败即切 Mock）
│   ├── particles.js        萤火粒子背景（Canvas）
│   ├── views.js            总览 / 报告 视图
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
全部使用 ES5/ES6 原生语法与 `<script>` 顺序加载，任何浏览器直接打开 `index.html` 都能运行
（离线状态下自动使用 `mock.js` 的示例数据）。答辩现场不需要 npm install。

### 2. 数据降级策略
`api.js` 中每个接口都经过 `withFallback(realCall, mockCall)` 包装：
- 后端可用 → 走真实接口
- 后端不可用 / 请求失败 → 自动回落 Mock 数据，并在界面上以 toast 提示「后端未连接，展示示例数据」

这保证了**前端可以脱离后端独立演示**，也保证了后端异常时演示不中断。

### 3. 动画渲染
报告页面采用「生成中 → 分块揭示」的动画编排：
- 萤火粒子加速聚集（加载态）
- 数字滚动计数（`FF.animateCounter`）
- 卡片错峰淡入（IntersectionObserver + `.reveal`）
- 图表逐个绘制（ECharts 的 `animationDuration` / `animationEasing`）
- 税检清单逐条滑入、风险条按等级填充

动画时长由 `config.yaml` 的 `ui.counter_duration`、`ui.report_animation` 控制。

### 4. 二次元风格
- 暗色「深空」主题（`--bg-deep: #0a0e1a`）+ 萤火虫青色强调（`--firefly: #5eead4`）
- 极光渐变背景 + Canvas 粒子萤火
- 圆角卡片、柔和光晕、微交互（hover 抬起、点击涟漪）
- emoji 图标 + 中文拟人化文案（「流萤已就绪」「萤火已点亮」）
- 支持亮色主题切换，同时提供 `prefers-reduced-motion` 无障碍降级

### 5. 图表
使用 ECharts（CDN 引入，由后端 `/api/meta` 返回的 CDN 地址或本地 `assets/echarts.min.js` 加载）。
图表颜色从 CSS 变量读取，主题切换时自动重绘。

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
