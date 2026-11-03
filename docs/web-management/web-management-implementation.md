# llm-hub Web 管理台 · 实现文档（只读阶段）

> 版本：v1.0（只读视图设计阶段）
> 日期：2026-11-03
> 范围：本文档覆盖 Web 管理台的**页面功能点分析**、**API 设计（统一前缀 `/v1/management`）**、**数据模型**与**UI 截图索引**。
> 关联文件：`dashboard-mockup.html`（全页面风格稿）、`dashboard-login-mockup.html`（登录门禁页风格稿）、`images/*.png`（各页面 UI 截图）。

---

## 1. 概述

### 1.1 目标
在现有 llm-hub 网关（FastAPI，端口 8834）之上，提供一个**只读为主**的 Web 管理台，用于：
- 实时掌握多个 Ollama / LM Studio 上游实例的运行状况与 GPU/CPU 设备分布；
- 通过本地 SQLite 记录每一次请求明细（req/res、耗时、状态等），并可在 UI 检索、统计；
- 查看 HUB 当前已配置的模型拓扑（LLM / Rerank / SystemOne），本期仅查看、后续再开放配置写入口。

### 1.2 技术约束
- **前端**：纯静态资源（`app/static/admin/` 下的 `index.html` + `app.css` + `app.js`），原生 HTML/CSS/JS，**零第三方 CDN 依赖**；趋势图与饼图用 ECharts 渲染，`echarts.min.js` 随静态资源一起内置发布（见 `docs/web-management/echarts.min.js`），不引用外网地址。
- **后端**：在现有 FastAPI 应用中新增只读接口，统一挂在 `/v1/management` 前缀下；复用已落地的 `ADMIN_PASSWORD` 环境变量鉴权（HMAC 会话 Cookie；密码仅用于登录，不是请求凭据）。
- **样式**：固定宽度容器（1180px，四周留白不撑满）、深/浅主题切换，整页使用 CSS 变量，侧边栏可折叠（见 UI 截图）。
- **埋点**：请求明细写入 SQLite（`data/llmhub.db`，WAL 模式），异步批量落盘，不阻塞转发主链路。

### 1.3 鉴权
所有 `/v1/management/*` 接口均受 `require_admin` 保护（与现有 `app/admin_auth.py` 一致）：
- 通过 `POST /v1/management/auth/login` 提交 `ADMIN_PASSWORD` 换取 12h HttpOnly Session Cookie；
- `require_admin` 只接受该 Cookie：`Authorization: Bearer` / `X-API-Key` 属于网关 API Key，
  把 `ADMIN_PASSWORD` 或网关 Key 放到这些头上一律返回 `401`；
- headless 调用同样先登录，再用 Cookie jar 访问；
- 未配置 `ADMIN_PASSWORD` 时接口返回 `503 Service Unavailable`。

---

## 2. 页面功能点分析

导航结构：左侧固定栏 `Dashboard / Upstreams / Models(LLM · Rerank · System one) / Tools(Web Search) / Requests / Settings(soon)`，点击切换视图（见 `images/nav-overview.png` 与下文各页截图）。**入口为独立登录页（见 2.8）**：未登录访问任意内部页均重定向至登录门禁，登录后凭 Session Cookie 进入带侧栏的内部视图。

### 2.1 Dashboard（聚合总览）
| 功能点 | 说明 | 数据来源 |
|---|---|---|
| KPI 四卡 | ① 上游在线数（在线/总数）② 运行中模型数 ③ 显存占用（已用 / 总量，进度条）④ 今日请求数（P95、错误率） | overview 聚合 + SQLite |
| 上游实例表 | 多实例表格：名称 / 类型 / 状态 / 已加载模型 / 显存占用 / 探测延迟；含 ollama、lmstudio、离线态范例 | `/v1/management/upstreams` |
| 运行中模型 | 模型 / 上游 / 设备（GPU 100% / 部分 x% / 纯 CPU）/ 上下文长度 | `/v1/management/overview` |
| 趋势图（起止时间） | 标题区提供 `起始时间 → 结束时间` 两个 `datetime-local` + 应用按钮，柱=每小时请求数，折线=P95 延迟（ECharts） | `/v1/management/stats/timeseries` |
| 分布饼图 · Upstream | 实心饼图：各 upstream 请求数占比（ECharts） | `/v1/management/stats/distribution?group_by=upstream` |
| 分布饼图 · Model | 实心饼图：各 model 请求数占比（ECharts） | `/v1/management/stats/distribution?group_by=model` |
| 分布饼图 · 成功/失败 | 实心饼图：成功 2xx vs 失败 4xx/5xx 占比（ECharts） | `/v1/management/stats/distribution?group_by=status` |
| 即时刷新 | 「聚合视图 · 数据每 5s 自动刷新」后 `↻` 按钮，点击手动刷新并刷新「最后更新」时间戳 | 前端轮询 |
| 主题 / 折叠 | 右上角明暗切换；侧栏折叠 / 恢复（图表随主题重绘） | 前端 |

> 截图：`images/dashboard.png`

### 2.2 Upstreams（上游实例）
| 功能点 | 说明 | 数据来源 |
|---|---|---|
| 实例表 | 同 Dashboard 的上游表格 | `/v1/management/upstreams` |
| 行展开 | **默认全部展开**：点击任意行展开下方「运行中模型（/api/ps，含设备判定）+ 已装模型（/api/tags）」 | `/v1/management/upstreams/{name}` |
| 多类型兼容 | ollama（有 ps）、lmstudio（无 ps，标注「不支持 /api/ps」）、离线实例（标注「不可达」） | 后端探测 |
| 设备判定 | 运行中模型按 `size_vram / size` 标注 GPU 100% / 部分 x% / 纯 CPU | 后端计算 |

> 截图：`images/upstreams.png`

### 2.3 Models · LLM
| 功能点 | 说明 | 数据来源 |
|---|---|---|
| 上游模型映射 | 模型名 / 上游 / 类型 / 别名 / num_ctx / 运行态，投影自 config.json | `/v1/management/models/llm` |
| 别名映射 | 别名 → 目标模型 → 上游 | 同上 |
| 只读 | 本期不开放编辑，仅展示 | — |

> 截图：`images/models-llm.png`

### 2.4 Models · Rerank
| 功能点 | 说明 | 数据来源 |
|---|---|---|
| 配置信息 | Endpoint、Enabled、Models（可多值）、打分方式 `mode=logprobs`、`keep_alive`（空闲卸载）、`top_logprobs`、上游协议 `upstream_api`、失败策略 `on_error`、并发上限 `max_concurrency` | `/v1/management/models/rerank` |
| 请求 API 格式 | 展示 `POST /v1/rerank` 的参数表（model / query / documents / top_n / return_documents / options）与请求、响应示例 | 静态文档 + 配置 |
| 只读 | 不展示具体运行模型名，聚焦配置与调用契约 | — |

> 截图：`images/models-rerank.png`

### 2.5 Models · SystemOne
| 功能点 | 说明 | 数据来源 |
|---|---|---|
| 配置信息 | Endpoint（`/v1/systemone`）、Enabled、Backend（后端 base URL，如 laya-server 或 Ollama 0.40+）、默认 `model`（`local` = 不注入）、`api_key_env`、Timeout | `/v1/management/models/systemone` |
| 请求 API 格式 | 展示 `POST /v1/systemone` 的参数表（`state` / `questions[].type`·`instructions`·`criteria` / `model`）与请求、响应示例（`answers` / `usage` / `routing`，后端透传） | 静态文档 + 配置 |
| 只读 | 不展示具体模型名，聚焦配置与调用契约 | — |

> 截图：`images/models-systemone.png`

### 2.6 Requests（请求明细）
| 功能点 | 说明 | 数据来源 |
|---|---|---|
| 筛选栏 | 起始/结束时间、**上游**（在模型前）、模型、状态（全部/2xx/4xx/5xx）、路径关键字 + 应用/重置 | `/v1/management/requests` |
| 统计条 | 区间请求数、P50、P95、P99、错误率 | 同上（随筛选实时计算） |
| 明细表 | 时间 / 方法 / 路径·模型 / 上游 / 状态 / 耗时 / 流式 / 操作（详情） | 同上 |
| 分页 | 每页 15 条，页码导航 | 同上 |
| 详情抽屉 | 右侧滑出：kv 元信息（到达时间 `ts`、完成时间 `ts_end`、方法、路径、查询串 `query`、请求模型 `requested_model`、解析后 `model`、上游、状态、耗时、TTFB、流式、客户端）+ 请求体 / 响应体（截断 8KB、敏感头脱敏） | `/v1/management/requests/{id}` |

> 截图：`images/requests.png`、`images/requests-detail.png`（详情抽屉，已含 `ts_end` / `query` /
> `requested_model` 三行）。改动 mockup 后重跑 `node docs/web-management/capture.js` 即可刷新，
> 脚本已改为按自身位置解析 HTML 与输出目录。

### 2.7 Settings（即将开放，占位）
列出后续将开放的写入口：API Keys 管理、Rerank 配置、Security 规则、Playground 调试、上游增删改。本期为 `soon` 占位页，与侧栏标记一致。

> 截图：`images/settings.png`

### 2.8 Login（登录门禁页）
登录页是管理台的**入口门禁**，独立于带侧栏的内部页面：整页居中卡片、无侧栏，由 `POST /v1/management/auth/login` 校验 `ADMIN_PASSWORD` 后写入 12h HttpOnly Session Cookie，再跳入 Dashboard。功能点：

- **品牌与标题**：复用与内部页一致的 `llm-hub` 渐变 Logo 与版本号，标题「管理员登录」，副标题标 `只读视图` 标签，强化「只读」预期。
- **密码输入**：单行 `ADMIN_PASSWORD` 密码框，带 👁 显隐切换；回车即提交；占位提示「请输入管理台密码」。
- **登录按钮**：主色（accent）全宽主按钮「登录」。
- **错误态**：密码错误时显示红色告警条「⚠ 密码错误，请重试」，并自动聚焦输入框（见 `images/login-error.png`）。
- **未配置锁定态**：当环境变量 `ADMIN_PASSWORD` 未设置时，登录卡片切换为「🔒 管理台未启用」状态，展示 `HTTP 503 · Service Unavailable` 文案（见 `images/login-locked.png`）——与后端「未配置返回 503」逻辑一一对应。
- **主题切换**：右上角浮动按钮，默认深色，可一键切浅色（见 `images/login-light.png`）；登录页同样跟随同一套 CSS 变量主题系统。
- **鉴权说明**：卡片底部注明「仅依赖环境变量 `ADMIN_PASSWORD`，不写入 config.json；登录后凭 12h HttpOnly 会话 Cookie 访问管理接口」，降低使用者误解。

> 截图：`images/login.png`（默认深色）、`images/login-error.png`（错误态）、`images/login-locked.png`（未配置 503 锁定态）、`images/login-light.png`（浅色主题）

### 2.9 Tools · Web Search
侧栏新增 `Tools` 分组，其下 `Web Search` 子项，样式与 `Models · System one` 一致：顶部配置信息卡 + 下方「请求 API 格式」。功能点：

- **配置信息卡**（对应 `config.json` 的 `websearch` 段，只读）：`Endpoint`（对外 `/v1/tools/web-search`）、`Enabled`、`Upstream`（提供检索的上游名，通常 `opencode` 类型）、`Base URL`（取自该 upstream，只读派生）、`Provider Path`（默认 `v1/web-search`）、`API Key Env`（环境变量名，空=不签名）、`Timeout`。注意：**没有 provider / top_k / 默认条数字段**，这些由 upstream 与请求体决定。
- **请求 API 格式**：上方「请求参数」表（参数 / 类型 / 必填 / 说明），下方左右分栏——左「请求示例」（POST + headers + JSON body），右「响应示例」（200 application/json + 透传契约）。JSON 带键名/字符串/数字/布尔着色，便于开发者对照。
- **请求参数**（以 `app/main.py` 的 `openapi_extra` 为准）：`query`(string, 必填)；`model`(string)；`top_k`(integer, 1–50)；`max_chars_per_page`(integer)；`locale`(string)；`recency`(string: day|week|month|year)；`synthesize`(boolean)；`include_sources`(boolean)。`additionalProperties: true`，未知字段保留。
- **响应结构**（Provider 透传）：`query` / `model` / `engine` / `answer`(可为 null) / `truncated` / `results[]`（每项 `title`/`url`/`snippet`/`paragraphs[]`）/ `text`（拼接后的段落文本）/ `sources[]`。
- **边界**：`websearch.enabled=false` → `404`；已启用但找不到 provider upstream → `503`；请求体与上游响应**原样转发**，llm-hub 不改写。

> 截图：`images/tools-websearch.png`

> 说明：Rerank、SystemOne、Web Search 的「请求 API 格式」统一改为**参数说明表 + 请求/响应双栏 + JSON 语法着色 + 底部说明**的组件（样式类 `api-doc`），三页保持一致。

---

## 3. API 设计（`/v1/management`）

> 统一前缀 `/v1/management`；全部 `GET`（除登录/登出）；均受 `require_admin` 保护；错误码沿用 HTTP 标准（`400` 参数错误、`401` 未认证、`403` 禁止、`404` 资源不存在、`503` 未配置密码 / 上游不可达）。

### 3.1 鉴权
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/management/auth/login` | Body `{ "password": "..." }`，成功返回 `200` 并在响应上 `set_cookie`（HttpOnly+SameSite=Lax，HTTPS 自动 Secure）；失败 `401` |
| POST | `/v1/management/auth/logout` | 清除会话 Cookie |
| GET  | `/v1/management/auth/ping`  | 受保护心跳，返回 `200 { "ok": true }`；未登录 `401` |

### 3.2 总览与上游
| 方法 | 路径 | 查询参数 | 说明 |
|---|---|---|---|
| GET | `/v1/management/overview` | — | 返回 KPI、上游摘要、运行中模型（含设备判定） |
| GET | `/v1/management/upstreams` | — | 上游实例列表（状态/已加载数/显存/延迟/类型/vram_total） |
| GET | `/v1/management/upstreams/{name}` | — | 单实例详情：运行中模型(ps)、已装模型(tags)、健康；离线返回 `503` + `unreachable` 标记 |

**`GET /v1/management/overview` 响应示例**
```json
{
  "kpi": {
    "upstreams_online": 2, "upstreams_total": 2,
    "running_models": 3,
    "vram_used_gib": 21.4, "vram_total_gib": 31.8,
    "requests_today": 1284, "p95_ms": 412, "error_rate": 0.006
  },
  "upstreams": [
    { "name": "Ollama-Arc-B65", "type": "ollama", "status": "online",
      "loaded": 2, "vram_used_gib": 18.2, "vram_total_gib": 31.8, "probe_ms": 42 }
  ],
  "running_models": [
    { "model": "qwen3.6:35b-pruned", "upstream": "Ollama-Arc-B65",
      "device": "gpu", "device_ratio": 1.0, "num_ctx": 65536 }
  ]
}
```

**设备判定字段 `device`**
- `gpu`（ratio ≈ 1.0）→ 显示「GPU 100%」
- `partial`（0 < ratio < 1.0）→ 显示「部分 xx%」
- `cpu`（ratio = 0）→ 显示「纯 CPU」
- 非 Ollama（如 LM Studio）/ 离线 → `device: null`，前端显示「—」

### 3.3 模型配置（只读投影）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/management/models/llm` | 返回 upstreams→models 映射 + aliases + num_ctx_overrides |
| GET | `/v1/management/models/rerank` | 返回 rerank 配置：`enabled` / `endpoint` / `mode` / `models[]` / `keep_alive` / `top_logprobs` / `upstream_api` / `on_error` / `max_concurrency`（rerank 无 api_key 字段，无需掩码） |
| GET | `/v1/management/models/systemone` | 返回 systemone 配置：`enabled` / `backend` / `model` / `api_key_env` / `path` / `timeout` |
| GET | `/v1/management/tools/websearch` | 返回 web-search 配置：`enabled` / `upstream` / `path` / `api_key_env`(掩码) / `timeout` / `base_url`(由 upstream 派生) |

**`GET /v1/management/models/llm` 响应示例**
```json
{
  "models": [
    { "model": "qwen3.6:35b-pruned", "upstream": "Ollama-Arc-B65",
      "type": "ollama", "alias": "q35-35b", "num_ctx": 65536, "running": true }
  ],
  "aliases": [
    { "alias": "q35-35b", "target": "qwen3.6:35b-pruned", "upstream": "Ollama-Arc-B65" }
  ]
}
```

> 说明：本节接口只负责**读配置投影**，不代理调用。真正对外提供能力的业务接口 `/v1/rerank`、`/v1/systemone`、`/v1/tools/web-search` **不在** `/v1/management` 下，其契约见 2.4 / 2.5 / 2.9，并以运行时 `/docs` 的 OpenAPI 为准（`openapi_extra` 中已内置请求与响应示例）。

```json
// GET /v1/management/tools/websearch  响应示例
{
  "enabled": true, "upstream": "opencode", "path": "v1/web-search",
  "api_key_env": "", "timeout": 300.0, "base_url": "http://127.0.0.1:8787"
}
```

### 3.4 请求明细与统计
| 方法 | 路径 | 查询参数 | 说明 |
|---|---|---|---|
| GET | `/v1/management/requests` | `start`,`end`(ISO8601),`upstream`,`model`,`status`(2xx\|4xx\|5xx),`path`(关键字),`page`,`page_size` | 返回 `stats` + `rows` + `pagination` |
| GET | `/v1/management/requests/{id}` | — | 单条详情（含 `request_body` / `response_body`，已截断与脱敏） |
| GET | `/v1/management/stats/timeseries` | `start`,`end`,`bucket`(hour\|day，默认 hour) | 时序聚合：每桶 `count` / `p95` / `errors`（驱动 Dashboard 趋势图） |
| GET | `/v1/management/stats/distribution` | `start`,`end`,`group_by`(upstream\|model\|status) | 分组计数分布：分别驱动 Dashboard 三张饼图（各 Upstream / 各 Model / 成功失败） |

**`GET /v1/management/requests` 响应示例**
```json
{
  "stats": { "count": 1284, "p50_ms": 318, "p95_ms": 412, "p99_ms": 980, "error_rate": 0.006 },
  "rows": [
    { "id": 10231, "ts": "2026-11-03T10:17:21", "method": "POST",
      "path": "/v1/chat/completions", "model": "qwen3.6:35b-pruned",
      "upstream": "Ollama-Arc-B65", "status": 200, "duration_ms": 386,
      "stream": true }
  ],
  "pagination": { "page": 1, "page_size": 15, "total": 1284, "pages": 86 }
}
```

**`GET /v1/management/requests/{id}` 响应示例**
```json
{
  "id": 10231, "ts": "2026-11-03T10:17:21", "method": "POST",
  "path": "/v1/chat/completions", "model": "qwen3.6:35b-pruned",
  "upstream": "Ollama-Arc-B65", "status": 200, "duration_ms": 386, "ttfb_ms": 120,
  "stream": true, "client_ip": "127.0.0.1",
  "request_body": "{ \"model\": \"qwen3.6:35b-pruned\", ... } (truncated 8KB)",
  "response_body": "{ \"choices\": [ ... ] } (first 8KB)",
  "request_body_truncated": true, "response_body_truncated": true
}
```

**`GET /v1/management/stats/distribution` 响应示例**
```json
// GET /v1/management/stats/distribution?group_by=upstream
{ "group_by": "upstream",
  "items": [ { "key": "Ollama-Arc-B65", "count": 642 },
             { "key": "Ollama-iGPU", "count": 380 },
             { "key": "LM-Studio-local", "count": 262 } ] }
// group_by=model   -> items: qwen3.6:35b-pruned / qwen3.5:9b / qwen3-reranker:4b-rk / bge-m3
// group_by=status  -> items: ok(2xx)=1247 / error(4xx/5xx)=37
```
> 三张饼图（各 Upstream / 各 Model / 成功失败）均由该接口按不同 `group_by` 取数；前端使用 **ECharts** 渲染实心饼图，并随主题切换明暗。趋势图（柱+折线）由 `stats/timeseries` 驱动，同样为 ECharts。

> 说明：Dashboard 已移除「失败请求明细」列表（失败明细统一在 Requests 页按状态筛选查看）；成功/失败分布以饼图呈现，不再单列错误样例表。后端仍保留 `GET /v1/management/stats/failures`（返回 `by_code` / `by_upstream` / `samples`），本期前端不再调用。

---

## 4. 数据模型

### 4.1 SQLite 表 `requests`（埋点）
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `ts` | TEXT/ISO8601 | 请求到达时间（naive UTC，字符串序==时间序，精确到秒） |
| `ts_end` | TEXT/ISO8601 | 响应完成时间（流式请求在流关闭时写入；亚秒耗时以 `duration_ms` 为准） |
| `method` | TEXT | GET/POST… |
| `path` | TEXT | 请求路径（不含查询串） |
| `query` | TEXT | URL 查询串，如 `format=json` |
| `requested_model` | TEXT | 客户端写来的模型名（别名解析前） |
| `model` | TEXT | 解析后的真实模型名（可能空） |
| `upstream` | TEXT | 命中的上游实例名 |
| `status` | INTEGER | HTTP 状态码 |
| `duration_ms` | INTEGER | 端到端耗时 |
| `ttfb_ms` | INTEGER | 首字节耗时 |
| `stream` | INTEGER(0/1) | 是否流式 |
| `tokens` | INTEGER | 用量（如可获取） |
| `req_size` / `res_size` | INTEGER | 请求/响应体字节 |
| `client_ip` | TEXT | 客户端 IP |
| `error` | TEXT | 错误信息（可空） |
| `request_body` / `response_body` | TEXT | 截断至 8KB、敏感字段脱敏为 `***` |
| `request_body_truncated` / `response_body_truncated` | INTEGER(0/1) | 是否被截断 |

索引：`ts`、`model`、`upstream`、`status`。保留策略：按天数滚动清理（默认 30 天，`REQUEST_LOG_RETENTION_DAYS=0` 永久）。
旧库升级：启动时对比 `PRAGMA table_info(requests)`，缺失列用 `ALTER TABLE ADD COLUMN` 补齐，历史数据不动。

### 4.1b 入库开关
`config.json` 的 `request_log.enabled`（默认 `true`）逐请求生效，支持热加载：
- 关闭时代理层直接跳过请求体/响应体采集与写库，管理台 `request_log.enabled` 回报 `false`；
- 打开后恢复记录，无需重启；`ConfigManager` 每秒最多比对一次配置 mtime，因此开关在约 1 秒内生效
  （已用本地 Ollama 实例验证：关→零新增行，开→恰好一行）；
- 数据库路径与保留天数仍由环境变量 `REQUEST_LOG_DB` / `REQUEST_LOG_RETENTION_DAYS` 决定。

### 4.2 配置只读投影
全部来自 `ConfigManager`（config.json）经 Pydantic 校验后的安全子集，绝不回显 API Key 明文。

---

## 5. 实施建议（落地顺序）
1. `app/request_log.py`：SQLite 建表 + 异步批量写入队列 + 统计查询接口（timeseries/distribution/分页）。
2. `app/proxy.py`：在转发出口埋点（覆盖流式/非流式/异常），`config.py` 给 `UpstreamConfig` 增加 `vram_total_gb`。
3. `app/management.py`：`/v1/management/*` 只读接口，复用 `require_admin`。
4. `app/main.py`：注册 management 路由与静态资源（置于 catch-all 之前）。
5. `app/static/admin/`：前端页面（依 `dashboard-mockup.html` 风格稿实现，含 8 视图、抽屉、主题、折叠、**ECharts** 图表、5s 轮询）。
6. 文档同步：`.env.example`、`example.config.json`、`README`、`CHANGELOG`。

---

## 6. UI 截图索引
| 页面 | 截图 |
|---|---|
| 导航总览 | `images/nav-overview.png` |
| Dashboard | `images/dashboard.png` |
| Upstreams | `images/upstreams.png` |
| Models · LLM | `images/models-llm.png` |
| Models · Rerank | `images/models-rerank.png` |
| Models · SystemOne | `images/models-systemone.png` |
| Tools · Web Search | `images/tools-websearch.png` |
| Requests | `images/requests.png` |
| Requests · 详情抽屉 | `images/requests-detail.png` |
| Settings | `images/settings.png` |
| Login · 登录门禁（深色默认） | `images/login.png` |
| Login · 密码错误态 | `images/login-error.png` |
| Login · 未配置锁定态（503） | `images/login-locked.png` |
| Login · 浅色主题 | `images/login-light.png` |
