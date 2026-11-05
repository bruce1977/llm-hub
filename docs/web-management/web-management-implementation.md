# llm-hub Web 管理台 · 实现文档（只读阶段）

> 版本：v1.1（已落地：后端 API + `web/` 前端，挂在 `/admin`）
> 日期：2026-11-04
> 范围：本文档覆盖 Web 管理台的**页面功能点分析**、**API 设计（统一前缀 `/v1/management`）**、**数据模型**与**UI 截图索引**。
> 实现状态：只读阶段全部完成——`app/management.py` + `app/request_log.py` 提供接口与明细库，`web/src` 构建为 `app/web` 后由网关在 `/admin` 提供服务；模型清单以 `config.yaml` 的 `upstreams[].models` 为准（`model_aliases` 短名优先显示，前缀通配按上游模型列表展开，根 `aliases:` 的派生模型单独成行，`api_models` 按 API 家族收窄测试窗口的可选模型且只列名字/别名，`/api/...` 原生窗口只列 Ollama 上游的模型），每个「API 用法」标签页里「请求参数」标题右侧的**测试**按钮会打开浮层测试窗口，经 `app/api_test.py` 在**服务端**真实发出这一笔请求（表单打开即带初始值，布尔参数为复选框）；回归测试见 `tests/test_management.py`（297 项）与 `docs/TESTCASES.md` 测试 34-42，落地细节与设计差异见第 5 节。
> 关联文件：`dashboard-mockup.html`（全页面风格稿）、`dashboard-login-mockup.html`（登录门禁页风格稿）、`images/*.png`（各页面 UI 截图，风格稿阶段的存档）。**当前版本的实拍截图**在 `docs/screenshots/{en,zh}/`，由 `capture-live.js` 连真实网关抓取，README 的「界面截图」一节直接引用。

---

## 1. 概述

### 1.1 目标
在现有 llm-hub 网关（FastAPI，端口 8834）之上，提供一个**只读为主**的 Web 管理台，用于：
- 实时掌握多个 Ollama / LM Studio 上游实例的运行状况与 GPU/CPU 设备分布；
- 通过本地 SQLite 记录每一次请求明细（req/res、耗时、状态等），并可在 UI 检索、统计；
- 查看 HUB 当前已配置的模型拓扑（LLM / Rerank / SystemOne），本期仅查看、后续再开放配置写入口。

### 1.2 技术约束
- **前端**：Vue 3 单页应用，源码在 `web/src`（Vite + Element Plus + axios + ECharts，全部本地依赖，无第三方 CDN）。`npm run build` 把产物写进 **`app/web`** 并随仓库提交，由网关自己以静态文件形式挂在 **`/admin`**（见 `app/main.py` 的 `route_console`）——产物与模块同级，本地 `app/web` 与镜像 `/app/web` 是同一个解析路径，`Dockerfile` 只做 `COPY app/web /app/web`，不含 node 工具链。趋势图与饼图用 ECharts 渲染，颜色取自 CSS 变量，因此主题切换时图表同步换色。
- **后端**：在现有 FastAPI 应用中新增只读接口，统一挂在 `/v1/management` 前缀下；复用 `ADMIN_PASSWORD` 环境变量鉴权（HMAC 会话 Cookie；密码仅用于登录，不是请求凭据）。
- **样式**：内容区固定宽度 1180px（四周留白不撑满）、面板最小高度 `max(640px, calc(100vh - 48px))` 以填满视口、深/浅主题切换并持久化在 `localStorage`（开关在左下角账户菜单），整页使用 CSS 变量，侧边栏可折叠（见 UI 截图）。
- **多语言**：`vue-i18n@11`（Composition 模式，`legacy: false`）。文案在 `web/src/i18n/messages/zh-CN.ts` / `en.ts`，按页面分命名空间；`en.ts` 用 `const en: MessageSchema = {...}` 绑定中文导出的类型，键少一个/多一个都会让 `vue-tsc` 报错。语言持久化在 `localStorage` 键 `llmhub-console-locale`，首次访问按 `navigator.language` 猜（`en*` → 英文，其余 → 中文），并同步 `<html lang>` 与 Element Plus 的 locale（`App.vue` 的 `el-config-provider`）。切换入口在左下角账户菜单「语言 / Language」。
- **埋点**：请求明细写入 SQLite（`data/llmhub.db`，WAL 模式），异步批量落盘，不阻塞转发主链路。

### 1.3 鉴权
所有 `/v1/management/*` 接口均受 `require_admin` 保护（实现在 `app/admin_auth.py`），接受**两种凭据之一**：
- 通过 `POST /v1/management/auth/login` 提交 `ADMIN_PASSWORD`（用户名固定 `admin`）换取 12h HttpOnly Session Cookie；
- 或网关 API Key（`GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE`，即 `.keys`），经 `Authorization: Bearer` / `X-API-Key`
  提交，便于脚本与 curl 复用已有凭据；
- `ADMIN_PASSWORD` 本身**不是**请求凭据：把它放进 `Bearer` / `X-API-Key` 仍然返回 `401`；
  查询串里的 key 一律不接受（避免密钥进入访问日志与浏览器历史）；
- 网关鉴权关闭（`auth.enabled: false` 或环境变量与密钥文件都没有 key）时没有可接受的 API Key，管理接口退回
  Cookie-only，绝不会变成匿名访问；
- 未配置 `ADMIN_PASSWORD` 时接口返回 `503 Service Unavailable`。
- `/admin` 的静态壳层（`index.html` / `assets/*`）**故意不鉴权**，否则登录页打不开；页面内每一次
  数据请求仍受上述保护。SPA 深链与编码后的 `../` 都回退到壳层，路径经 `resolve()` +
  `is_relative_to()` 校验，无法跳出构建目录。

---

## 2. 页面功能点分析

导航结构：左侧固定栏 `Dashboard / Upstreams / Requests / Models(LLM · Embed · Rerank · System one) / Tools(Web Search)`，点击切换视图（见 `images/nav-overview.png` 与下文各页截图）。`Settings` 与 `About` 都不占侧栏位，入口移到左下角账户菜单（见「账户菜单」）。**入口为独立登录页（见 2.8）**：未登录访问任意内部页均重定向至登录门禁，登录后凭 Session Cookie 进入带侧栏的内部视图。侧栏高亮只认 `router-link-exact-active`：Dashboard 挂在 `/`，是所有路由的祖先，用 `router-link-active` 会让它在每一页都亮着。

**账户菜单**：左下角固定账户卡（渐变圆形头像 + 用户名 `admin` + `Administrator`）。卡片相对居中的 1180px 壳层定位（`left: max(38px, calc((100vw - 1180px)/2 + 18px))`、`bottom: 34px`），滚动页面时不跟随内容移出视野；壳层最小高度为 `max(640px, calc(100vh - 48px))`，短页面下卡片也落在面板内部而不是悬在面板外。点击卡片弹出菜单，条目依次为：

- **语言 / Language**：悬停或点击展开**右侧子菜单**（不撑开当前菜单），当前仅 `中文` 可选，`English` 标 `soon` 占位，界面翻译完成后再启用。
- **深色模式**：开关形态，切换 `theme.mode`（沿用 `localStorage` 持久化 + `html.dark`），图标随当前模式显示 ☾ / ☀。
- **设置**：跳转 `/admin/settings`，原侧栏入口已移除。
- **关于**：跳转 `/admin/about`（项目简介、主要功能、动态版本信息、GitHub 地址与公众号二维码，见 2.10）。
- **退出登录**：调用 `signOut()` 后回到登录门禁。

菜单在点击外部区域或按 `Esc` 时关闭；语言子菜单离开后延迟 150ms 关闭，便于跨过两者之间的间隙。

### 2.1 Dashboard（聚合总览）
| 功能点 | 说明 | 数据来源 |
|---|---|---|
| KPI 四卡 | ① 上游在线数（在线/总数）② 运行中模型数 ③ 显存占用（已用 / 总量，进度条）④ 今日请求数 + **今日 token 合计**（`k` 单位、两位小数，旁边仍是 P95 与错误率） | overview 聚合 + SQLite |
| 首次加载 | 数据未到位前 KPI 卡显示 shimmer 骨架屏、图表区显示「加载中…」，避免用 `0` 或空表误导 | 前端 |
| 上游实例表 | 多实例表格：名称 / 类型 / 状态 / 已加载模型 / 显存占用 / 探测延迟；含 ollama、lmstudio、离线态范例 | `/v1/management/upstreams` |
| 运行中模型 | 模型（`model_aliases` 短名作为显示名，`alias` 角标 + 悬停给出真实 id；根 `aliases:` 声明的派生模型是**独立一行**，带 `derived` 角标，悬停提示它运行在哪个目标模型上）/ 上游 / 设备（GPU / 部分 / 纯 CPU / Cloud，不显示百分比）/ 上下文长度。除 `/api/ps` 的驻留模型外，还列出托管上游（OpenCode）探测成功时它声明的模型，这些行设备显示 **Cloud**、显存与占用为 `-`；派生模型行设备沿用目标模型所在实例、显存同样显示 `-`（那份显存已计在目标模型名下），KPI「运行中模型数」只统计真实模型，不会重复计数 | `/v1/management/overview` |
| 趋势图（起止时间） | 标题区提供 `起始时间 → 结束时间` 两个 `datetime-local` + 应用按钮，柱=每小时请求数，折线=P95 延迟（ECharts） | `/v1/management/stats/timeseries` |
| 分布饼图 · Upstream | 实心饼图（不是圆环），饼上不再画标签；名称与数量收在**图形下方一行一条**的可滚动图例里，字号 9.5px、行高 10.5px，名称超 16 字截断并附计数——长模型 id 既不会压到图形，也不会横向挤成一行 | `/v1/management/stats/distribution?group_by=upstream` |
| 分布饼图 · Model | 同上，按 model 分组 | `/v1/management/stats/distribution?group_by=model` |
| 分布饼图 · 成功/失败 | 同上，成功 `ok` 绿 / 失败 `error` 红 | `/v1/management/stats/distribution?group_by=status` |
| 即时刷新 | 「聚合视图 · 数据每 1 分钟自动刷新」后 `↻` 按钮；刷新期间 `↻` 持续旋转并禁用、顶栏底部走一条不确定进度条、副标题转成「刷新中…」并带小号 spinner、KPI 卡降透明度、图表蒙上带 spinner 的薄纱，完成后恢复「最后更新 …」 | 前端轮询 |
| 主题 / 折叠 | 左下角账户菜单内切换明暗（图表随主题重绘）；侧栏折叠 / 恢复 | 前端 |
| 页签图标 | `web/public/favicon.svg`（蓝紫渐变圆角方块 + 汇聚节点标志），构建时原样复制到 `app/web/`，经 `/admin/favicon.svg` 服务，离线容器与 CSP 均可用 | 静态资源 |

> 截图：`images/dashboard.png`

### 2.2 Upstreams（上游实例）
| 功能点 | 说明 | 数据来源 |
|---|---|---|
| 实例表 | 同 Dashboard 的上游表格 | `/v1/management/upstreams` |
| 行展开 | 点击任意行展开该实例的「模型」表，每次展开都重新探测。一张**合并表**：行集合以 config.yaml 的 `upstreams[].models` 为准（`model_aliases` 短名作为显示名，前缀通配经上游模型端点展开成命中的具体模型），每行再并上 `/api/ps` 的运行态（设备 / 上下文 / 显存·占用）；已声明但未加载的行标「未加载」，实例里在跑却未声明的模型追加在末尾。根 `aliases:` 声明的派生模型是**独立一行**（带 `derived` 角标、设备沿用目标模型、显存 `-`），排在归属上游的声明名之后。`opencode` 类型探测有效即整批在线（标签「在线（探测有效）」、设备 **Cloud**）。未声明任何模型时提示「该上游未在 config.yaml 中声明任何模型」 | `/v1/management/upstreams/{name}` |
| 多类型兼容 | ollama（有 ps）、lmstudio（无 ps，标注「不支持 /api/ps」）、离线实例（标注「不可达」） | 后端探测 |
| 设备判定 | 运行中模型按 `size_vram / size` 标注 GPU / 部分 / 纯 CPU（不显示百分比），并给出「显存 / 占用」(GiB) 与上下文长度；托管 provider 无加载状态，设备显示 **Cloud**、显存显示「—」 | 后端计算 |

> 截图：`images/upstreams.png`

### 2.3 Models · LLM
页面分两个 Tab：**模型清单**（默认）与 **API 用法**。切换 Tab 不重新拉取数据，`↻` 刷新始终走 `/v1/management/models/llm`。

| 功能点 | 说明 | 数据来源 |
|---|---|---|
| Tab · 模型清单 | | |
| 上游模型映射 | 模型名 / 上游 / 类型 / num_ctx / 设备 / 显存 · 占用 (GiB) / 运行态。行集合来自 **`config.yaml` 的 `upstreams[].models`**（实例里装了但没声明的模型不列，网关本来就路由不到它们）；前缀通配（`opencode/*`）按与路由同一个发现结果展开成具体模型并带 `wildcard` 角标。`model_aliases` 短名行显示 `display`（悬停给出真实 id，`alias` 角标）；根 `aliases:` 声明的派生模型是**独立一行**，紧跟在它的目标模型后面，带 `derived` 角标（悬停「运行在 {target} 上」）、设备沿用目标模型的实例、显存 `-`。设备与内存只在 Ollama 运行时给值，托管上游（OpenCode）探测有效即全部「在线」，设备显示 **Cloud**、内存为 `-` | `/v1/management/models/llm` |
| 别名与派生模型 | 名称 / 类型（`显示别名` = `upstreams[].model_aliases`，只改名；`派生模型` = 根 `aliases:`，是带独立固化参数的新模型）/ 目标模型 / 上游 / 固化参数（`think=false` 形式）/ 说明 | 同上（`aliases[].kind`） |
| 上下文固化 | 模型 → `num_ctx` | `num_ctx_overrides` |
| 测速（每行按钮） | 「操作」列每行一个 `测速` 按钮（**所有模型都可点**）。点击后 `POST /v1/management/bench` 拿 `job_id`，弹出模态框并每秒轮询 `/bench/{job_id}`：跑的过程中显示「测速中 · 第 x/y 轮」与进度条，结束后展示 Decode / Prefill / TTFT 三张统计卡（均值，副行给出 ±标准差、最小/最大、变异系数）与逐轮明细表（轮次 / 类型 / Decode / Prefill / TTFT / 生成 tokens）；失败时展示后端 `error` 原文并提供「重新测速」。同时只允许一个任务在跑，弹窗关闭即停止轮询。**派生模型行测的是它的目标模型**（上游并不认识别名名），并把别名 `params.think` 作为 `think` 一起提交，所以「nothink 变体」的测速结果与真实调用口径一致 | `POST /bench` + `GET /bench/{job_id}`（`app/bench.py`） |
| 悬停测速历史 | 鼠标停在任意模型行（250 ms 防抖）浮出小卡片，显示该 `upstream` + `model` **最近 3 份**记录：时间 · 计时口径徽标 + Decode / Prefill / TTFT；同一行的结果在会话内缓存，不重复请求。无记录时提示「暂无测速记录」，读取失败显示错误原文。**派生模型行按目标模型查询**，所以它读到的是同一份实例测速记录 | `GET /bench/history?upstream=&model=`（`bench_results` 表） |
| 测速任务调度 | 任务跑在**独立线程 + 自己的事件循环**（`asyncio.run`）里，不挂在 `POST /bench` 那个请求的 loop 上——否则请求作用域一结束任务就被取消，job 永远停在 `running`。状态写成「顺序」讲究：先 `store.record()` 入队、再置 `done`，`/bench/history` 读之前 `await store.flush()`（等队列落盘），所以「完成后立刻看历史」不会读到空表。`BenchStore.record()` 用 `loop.call_soon_threadsafe` 跨线程投递，队列仍归应用主循环所有 | `app/management.py` + `app/bench_store.py` |
| 列宽 | 「模型名」自适应吃剩余宽度，其余列给定宽度提示（上游 96 / 类型 88 / num_ctx 78 / 设备 104 / 显存·占用 124 / 运行态 96 / 操作 84 px）并 `nowrap`，避免「● 运行中」徽标被折成两行、显存列抢占运行态宽度 | 前端（`theme.css` 的 `.badge` 也加了 `nowrap`） |
| Tab · API 用法 | 两个已支持端点的调用契约，复用 Rerank 页的 `ApiDoc` 版式（参数表 + 请求/响应示例）：`POST /api/generate`（Ollama 原生，参数表与 `/openapi.json` 中的 schema 一致：model / prompt / system / template / raw / context / images / format / stream / keep_alive / logprobs / top_logprobs / options）与 `POST /v1/chat/completions`（OpenAI 兼容：model / messages / temperature / max_tokens / stream / tools）。两处都标注**缺省即流式**（不写 `stream` 时 `/api/generate` 走 NDJSON、`/v1/chat/completions` 走 SSE），以及 `model` 先经别名解析、`options.num_ctx` 仍被 `num_ctx_overrides` 裁剪、非 POST 一律 405 | 静态文档（与 `app/main.py` 的 schema、`docs/API.md` 对齐） |
| Tab · API 用法 · 测试窗口 | 每个 `ApiDoc` 的「请求参数」标题右侧有一个**测试**按钮，点开后是浮层里的 `ApiTester`（`kind = generate` / `chat`）：模型选择框（选项来自 `/api-test/models`，**只显示名字或别名**——有别名用别名，否则用声明名；`generate` 是 Ollama 原生端点，所以只列 `type: "ollama"` 上游的模型，`lmstudio` / `opencode` 的模型只出现在 `chat` 窗口；上游、运行态/云端与「未声明」等细节放在悬停 title 里；字段旁的 × 可清除，清除后请求体不带 `model`，而 `model_required` 家族发送前会提示「请先选择模型」）+ 该端点的参数输入（**打开时已按初始值填好**；有文档默认值的布尔参数是复选框，勾选与否都显式发送 `true` / `false`，`stream` / `think` 默认关闭；其余留空即整个字段不发送）+「恢复默认值」/「清空」+「查看请求体」预览 +「发送测试」。提交前本地拦一道：数字/JSON 非法标红并提示「请先修正标红的输入项」，`model_required` 没选模型提示「请先选择模型」。结果区显示状态徽标、耗时 / 首字 / `content_type`、实际路由（`qwen3:8b-nothink → qwen3:8b @ Ollama-Arc-B65`）与注入参数，正文按流式分块拼装或渲染 JSON；响应超上限时提示「响应较大，只回传了前 128 KB」。功能停用只在标题挂「未启用」徽标，请求照样发出去并由端点返回它的 404/503——测试窗口显示的是外部客户端真实会看到的结果 | `GET /api-test/models` + `POST /api-test`（`app/api_test.py`） |
| 只读 | 本期不开放编辑，仅展示 | — |

> 截图：`images/models-llm.png`

### 2.3b Models · Embed
页面分两个 Tab：**模型清单**（默认）与 **API 用法**。格式与 Rerank 页一致，但**不拉取任何接口**：网关没有 `embed` 配置段，两个嵌入端点都是 catch-all 原样透传，页面内容全部为静态契约。

| 功能点 | 说明 | 数据来源 |
|---|---|---|
| Tab · 模型清单 · 端点信息卡 | 原生端点 `/api/embed`（Ollama）、OpenAI 兼容端点 `/v1/embeddings`、认证方式、模型解析（别名 → 路由，与 LLM 共用 `upstreams[].models` / `aliases`）、响应「原样透传」 | 静态契约 |
| Tab · 模型清单 · 可用模型 | 独立卡片，参照 Rerank 的「可用模型」段：三列「模型 / 类型 / 调用端点」，列出网关约定的 `bge-m3:latest`（`embedding`）。清单为静态说明，实际可调用还需该模型已在 `upstreams[].models` 声明（或被前缀通配覆盖）且上游已拉取 | 静态清单 |
| Tab · 模型清单 · 功能介绍 | 模型清单 Tab 的**第一张卡片**，约 200 字说明（样式类 `feature-intro`）：嵌入的用途（语义检索 / 聚类 / 去重 / RAG）、两个入口的分工、别名解析与模型声明要求 | 静态文案 |
| Tab · 模型清单 · 端点对照 | `/api/embed`：`model` / `input` / `truncate` / `options` / `keep_alive` → `embeddings[][]`；`/v1/embeddings`：`model` / `input` / `encoding_format` / `dimensions` / `user` → `data[].embedding` | 静态契约 |
| Tab · API 用法 | 顶部说明块 + 两个 `ApiDoc`：请求参数表 + 请求 / 响应示例；注明模型必须已在 `config.yaml` 声明（或被前缀通配覆盖），否则 **404** 路由失败 | 静态文档（与 `docs/API.md` 对齐） |
| Tab · API 用法 · 测试窗口 | 两个 `ApiTester`（`kind = embed_ollama` / `embed_openai`）：模型框只给 `api_models.embed`（未配置时由可调用池推导；`embed_ollama` 是原生端点，只留 `type: "ollama"` 上游的行，`embed_openai` 也列 `lmstudio` / `opencode`），参数是各自的 `input` / `truncate` / `options` / `keep_alive` 与 `encoding_format` / `dimensions`；结果直接把 `embeddings[][]` / `data[].embedding` 的 JSON 呈现出来。页面其余部分仍然**不拉任何接口**，只有这两个测试窗口会读 `/api-test/models`（全站共用一份缓存） | `GET /api-test/models` + `POST /api-test` |

### 2.4 Models · Rerank
页面分两个 Tab：**模型清单**（默认）与 **API 用法**。切换 Tab 不重新拉取数据，`↻` 刷新始终走 `/v1/management/models/rerank`。

| 功能点 | 说明 | 数据来源 |
|---|---|---|
| Tab · 模型清单 · 配置信息 | Endpoint、Enabled、Models（可多值）、打分方式 `mode=logprobs`、`keep_alive`（空闲卸载）、`top_logprobs`、上游协议 `upstream_api`、失败策略 `on_error`、并发上限 `max_concurrency` | `/v1/management/models/rerank` |
| Tab · 模型清单 · 可用模型 | 独立卡片，五列「模型 / 上游 / 类型 / Base URL / 凭据」；行为 `config.yaml` 声明的 `rerank.models[]`，凭据只显示脱敏值或「未配置」。网关按 `mode` 选择打分实现，`model_modes` 可为单个模型覆盖 | `/v1/management/models/rerank` |
| Tab · 模型清单 · 功能介绍 | 模型清单 Tab 的**第一张卡片**，约 200 字说明（样式类 `feature-intro`）：重排的作用（召回后精排）、两种打分方式（logprobs / embedding）、结果归一化与 `top_n` / `return_documents` | 静态文案 |
| Tab · API 用法 | 展示 `POST /v1/rerank` 的参数表（model / query / documents / top_n / return_documents / options）与请求、响应示例 | 静态文档 + 配置 |
| Tab · API 用法 · 测试窗口 | `ApiTester`（`kind = rerank`）：模型框给 `api_models.rerank`（未配置时回落 `rerank.models`），`endpoint` 用**配置里的** `rerank.endpoint` 而不是常量；查询串与文档列表必填，空着时前端直接拦住并给出原因，运行后展示 `results[]` 的分数与顺序 | `GET /api-test/models` + `POST /api-test` |
| 只读 | 本期不开放编辑，仅展示（不展示运行态驻留信息） | — |

> 截图：`images/models-rerank.png`

### 2.5 Models · SystemOne
页面分两个 Tab：**模型清单**（默认）与 **API 用法**。切换 Tab 不重新拉取数据，`↻` 刷新始终走 `/v1/management/models/systemone`。

| 功能点 | 说明 | 数据来源 |
|---|---|---|
| Tab · 模型清单 · 配置信息 | Endpoint（`/v1/systemone`）、Enabled、Backend（后端 base URL，如 laya-server 或 Ollama 0.40+）、默认 `model`（`local` = 不注入）、`api_key_env`、Timeout | `/v1/management/models/systemone` |
| Tab · 模型清单 · 可用模型 | 独立卡片，参照 Rerank：三列「模型 / 类型 / 调用端点」，列出决策后端可接受的 `tev1:0.8b`、`tev1:latest`、`nimble:latest`。清单为静态说明（后端只配置单个默认 `model`），由请求体 `model` 指定，缺省时网关按配置注入；实际能否调用取决于后端是否已部署 | 静态清单 |
| Tab · 模型清单 · 功能介绍 | 模型清单 Tab 的**第一张卡片**，约 200 字说明（样式类 `feature-intro`）：快速决策的适用场景（意图识别 / 分流 / 风控 / 校验）、两种后端（laya-server 与 Ollama 0.40+）、`model` 注入与 SSE 流式 | 静态文案 |
| Tab · API 用法 | 展示 `POST /v1/systemone` 的参数表（`state` / `questions[].type`·`instructions`·`criteria` / `model`）与请求、响应示例（`answers` / `usage` / `routing`，后端透传），另附 SSE 流式响应示例 | 静态文档 + 配置 |
| Tab · API 用法 · 测试窗口 | `ApiTester`（`kind = systemone`）：`state` 与 `questions` 用 JSON 输入框（带文档里的示例作默认值，非法 JSON 当场标红），`stream` 是开关；模型框 `free_model: true`——laya-server 只收 `auto`/`english`/`multilingual`/`typed-decisions` 这几个枚举，Ollama 0.40+ 只收已安装模型名，所以下拉给 `api_models.systemone`（未配置时取 `systemone.model`），末尾另有「自定义模型…」选中后换成手输输入框，字段旁的 × 可清除，空着则由网关按配置注入。若 `SYSTEMONE_SERVER_API_KEY` 已设置，卡片以只读掩码 `••••••` 显示并标注变量名 | `GET /api-test/models` + `POST /api-test` |
| 只读 | 本期不开放编辑，仅展示 | — |

> 截图：`images/models-systemone.png`

### 2.6 Requests（请求明细）
| 功能点 | 说明 | 数据来源 |
|---|---|---|
| 筛选栏 | 起始/结束时间、**上游**（在模型前）、模型、状态（全部/2xx/3xx/4xx/5xx）、路径关键字 + 应用/重置 | `/v1/management/requests` |
| 模型下拉随上游收敛 | 选定上游后，模型只列该上游在 config.yaml 里声明的名字（`upstreams[].models`，前缀通配按上游模型端点展开，不列出实例里未声明的模型）；标签变成「模型 · <上游>」；不选上游时给出全量并附加所有别名。别名归属其目标模型所在的每个上游；根 `aliases:` 的派生模型既是一行模型也是别名，选项按名字去重。换上游会让不再可选的已选模型自动清空 | `/v1/management/models/llm` |
| 统计条 | 区间请求数、P50、P95、P99、错误率 | 同上（随筛选实时计算） |
| 明细表 | 时间 / 方法 / 路径·模型 / 上游 / 状态 / 耗时 / 流式 / 操作（详情） | 同上 |
| 分页 | 每页 15 条，页码导航 | 同上 |
| 详情抽屉 | 右侧滑出：kv 元信息（到达时间 `ts`、完成时间 `ts_end`、方法、路径、查询串 `query`、请求模型 `requested_model`、解析后 `model`、上游、状态、耗时、TTFB、流式、客户端）+ 请求体 / 响应体（截断 8KB、敏感头脱敏） | `/v1/management/requests/{id}` |
| 请求体 / 响应体版式 | `JsonPane` 新增 `height` + `wrap`：**固定高度 320px**、`pre-wrap` 软换行（不再横向滚走）、只保留纵向滚动条，且解析得到的话先把单行 JSON 缩进成 2 空格多行，截断的 8KB 或非 JSON 原文按原样显示。其他页面（`ApiDoc` 的请求/响应示例）沿用默认「`max-height` + 不换行」，行为不变 | 前端（`components/JsonPane.vue` + `.code2.wrap`） |

> 截图：`images/requests.png`、`images/requests-detail.png`（详情抽屉，已含 `ts_end` / `query` /
> `requested_model` 三行）。改动 mockup 后重跑 `node docs/web-management/capture.js` 即可刷新，
> 脚本已改为按自身位置解析 HTML 与输出目录。

### 2.7 Settings（即将开放，占位）
入口在左下角账户菜单的「设置」，侧栏不再保留该项。页面列出后续将开放的写入口：API Keys 管理、Rerank 配置、Security 规则、Playground 调试、上游增删改。本期为占位页。

> 截图：`images/settings.png`

### 2.8 Login（登录门禁页）
登录页是管理台的**入口门禁**，独立于带侧栏的内部页面：整页居中卡片、无侧栏，由 `POST /v1/management/auth/login` 校验 `ADMIN_PASSWORD` 后写入 12h HttpOnly Session Cookie，再跳入 Dashboard。功能点：

- **品牌与标题**：复用与内部页一致的 `llm-hub` 渐变 Logo 与版本号，标题「管理员登录」，副标题标 `只读视图` 标签，强化「只读」预期。
- **密码输入**：单行 `ADMIN_PASSWORD` 密码框，带 👁 显隐切换；回车即提交；占位提示「请输入管理台密码」。
- **登录按钮**：主色（accent）全宽主按钮「登录」。
- **错误态**：密码错误时显示红色告警条「⚠ 密码错误，请重试」，并自动聚焦输入框（见 `images/login-error.png`）。
- **未配置锁定态**：当环境变量 `ADMIN_PASSWORD` 未设置时，登录卡片切换为「🔒 管理台未启用」状态，展示 `HTTP 503 · Service Unavailable` 文案（见 `images/login-locked.png`）——与后端「未配置返回 503」逻辑一一对应。
- **主题切换**：右上角浮动按钮，默认深色，可一键切浅色（见 `images/login-light.png`）；登录页同样跟随同一套 CSS 变量主题系统。
- **鉴权说明**：卡片底部注明「仅依赖环境变量 `ADMIN_PASSWORD`，不写入 config.yaml；登录后凭 12h HttpOnly 会话 Cookie 访问管理接口」，降低使用者误解。

> 截图：`images/login.png`（默认深色）、`images/login-error.png`（错误态）、`images/login-locked.png`（未配置 503 锁定态）、`images/login-light.png`（浅色主题）

### 2.9 Tools · Web Search
侧栏新增 `Tools` 分组，其下 `Web Search` 子项。页面与 Models · Rerank 同为两个 Tab：**模型清单**（默认，配置信息卡 + 可用模型）与 **API 用法**（请求契约），切换 Tab 不重新请求。功能点：

- **配置信息卡**（对应 `config.yaml` 的 `websearch` 段，只读）：`Endpoint`（对外 `/v1/tools/web-search`）、`Enabled`、`Upstream`（提供检索的上游名，通常 `opencode` 类型）、`Base URL`（取自该 upstream，只读派生）、`Provider Path`（默认 `v1/web-search`）、`API Key Env`（环境变量名，空=不签名）、`Timeout`。注意：**没有 provider / top_k / 默认条数字段**，这些由 upstream 与请求体决定。
- **可用模型**：数据来自 `/v1/management/models/llm` 中属于 `websearch.upstream` 的行（声明名、通配已展开、`model_aliases` 短名作为显示名、根 `aliases:` 的派生模型独立成行并带 `derived` 角标），列 模型 / 上游 / 类型 / 设备 / 状态；托管 provider 探测有效时行显示 `● Cloud` 与 `● 在线`。卡片顶部说明检索能力来自后台配置的 [opencode-port](https://github.com/bruce1977/opencode-port)：需在 `config.yaml` 中把一个 `type: "opencode"` 的 upstream 指向它（默认 `127.0.0.1:10000`）并让 `websearch.upstream` 指向该 upstream；声明 `opencode/*` 时会展开成它当前支持的模型（内置 `opencode/*-free`、`auto`、`ollama-local/*`）。请求的 `model` 不是 llm-hub 配置的模型，而是 opencode-port 支持的模型名，也可以不填——省略时由 opencode-port 使用它自己的默认模型。
- **功能介绍**：模型清单 Tab 的第一张卡片，约 200 字说明（样式类 `feature-intro`）：检索接入的用途、`query` 必填与几个关键可选字段、`synthesize` 的额外综合，以及返回 `text` 可直接拼进 prompt。
- **API 用法**：上方「请求参数」表（参数 / 类型 / 必填 / 说明），下方左右分栏——左「请求示例」（POST + headers + JSON body），右「响应示例」（200 application/json + 透传契约）。JSON 带键名/字符串/数字/布尔着色，便于开发者对照。
- **API 用法 · 测试窗口**：`ApiTester`（`kind = websearch`）。`query` 必填并预置一条示例查询，`top_k` / `max_chars_per_page` / `locale` / `recency`（下拉：day/week/month/year）/ `synthesize` 是复选框（默认不勾选，显式发送 `false`），`include_sources` 是三态下拉，留空即不发送。模型下拉 `free_model: true` 且**默认留空**（末尾「自定义模型…」可输入任意 opencode-port 模型名）——`model` 在这里是 opencode-port 支持的模型名（不是 llm-hub 配置的别名），省略时由 provider 用它自己的默认；`OPENCODE_API_KEY` 已设置时卡片显示只读掩码 `••••••` 与变量名。响应原样透传，测试窗口把 `text` / `results[]` 的 JSON 直接渲染出来。
- **请求参数**（以 `app/main.py` 的 `openapi_extra` 为准）：`query`(string, 必填)；`model`(string，opencode-port 支持的模型名，可省略 = 使用 opencode-port 默认模型)；`top_k`(integer, 1–50)；`max_chars_per_page`(integer)；`locale`(string)；`recency`(string: day|week|month|year)；`synthesize`(boolean)；`include_sources`(boolean)。`additionalProperties: true`，未知字段保留。
- **响应结构**（Provider 透传）：`query` / `model` / `engine` / `answer`(可为 null) / `truncated` / `results[]`（每项 `title`/`url`/`snippet`/`paragraphs[]`）/ `text`（拼接后的段落文本）/ `sources[]`。
- **边界**：`websearch.enabled=false` → `404`；已启用但找不到 provider upstream → `503`；请求体与上游响应**原样转发**，llm-hub 不改写。

> 截图：`images/tools-websearch.png`

> 说明：Rerank、SystemOne、Web Search 的「请求 API 格式」统一改为**参数说明表 + 请求/响应双栏 + JSON 语法着色 + 底部说明**的组件（样式类 `api-doc`），三页保持一致。

### 2.10 About（关于）
入口在左下角账户菜单的「关于」，路由 `/admin/about`，同样不占侧栏位。页面回答「这个项目是什么、能做什么、跑的是哪个版本」，功能点：

| 功能点 | 说明 | 数据来源 |
|---|---|---|
| 项目简介 | 一段说明（样式类 `feature-intro`）：FastAPI 网关把多台 Ollama / LM Studio / OpenAI 兼容后端收敛成一个入口，并自带 Rerank、System One、Web 搜索、管理台与请求明细 | 静态文案 |
| 主要功能 | 两列功能网格（图标 + 标题 + 一行说明）：多上游路由、别名与参数注入、嵌入与重排、System One、Web 搜索、安全与配额、请求明细与统计、只读管理台、热重载与流式透传 | 静态文案 |
| 版本信息 | **动态**：`Gateway 版本` 由 `GET /v1/management/overview` 的 `gateway_version` 给出，版本前的圆点在请求进行中是**空心并呼吸闪烁**、拿到结果后填充实心；同卡还显示上游在线数、运行中模型、今日请求 / token、请求明细库状态与保留天数、数据时间 | `/v1/management/overview` |
| 项目地址 | GitHub 仓库 <https://github.com/bruce1977/llm-hub>（新窗口打开）、Docker Hub 镜像 `bruce1977/llm-hub:latest`、在线文档 `/docs` · `/openapi.json` | 静态链接 |
| 公众号 | 二维码图片来自 `web/public/wechat-qr.jpg`，构建时原样复制进 `app/web/`，经 `/admin/wechat-qr.jpg` 服务（CSP `img-src 'self' data:` 允许同源图片）；文件缺失时卡片给出补齐说明而不是破图 | 静态资源 |
| 刷新 | 顶栏 `↻` 重新拉取 overview，`refreshing` 复用在版本圆点与顶栏进度上 | 前端 |

---

## 3. API 设计（`/v1/management`）

> 统一前缀 `/v1/management`；全部 `GET`（除登录/登出）；均受 `require_admin` 保护（会话 Cookie **或**网关 API Key）；错误码沿用 HTTP 标准（`400` 参数错误、`401` 未认证、`403` 禁止、`404` 资源不存在、`503` 未配置密码 / 上游不可达 / 前端未构建）。

### 3.1 鉴权
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/management/auth/login` | Body `{ "username": "admin", "password": "..." }`（`username` 可省略，默认 `admin`，比较时忽略大小写与首尾空格）；成功返回 `200` 并在响应上 `set_cookie`（HttpOnly+SameSite=Lax，HTTPS 自动 Secure）；失败 `401`（附约 100 ms 延迟） |
| POST | `/v1/management/auth/logout` | 清除会话 Cookie |
| GET  | `/v1/management/auth/ping`  | 受保护心跳，返回 `200 { "ok": true, "admin": true }`；未登录 `401`；未配置 `ADMIN_PASSWORD` `503` |

### 3.2 总览与上游
| 方法 | 路径 | 查询参数 | 说明 |
|---|---|---|---|
| GET | `/v1/management/overview` | — | 返回 KPI（含今日请求数与今日 token 合计 `tokens_today`）、上游摘要、运行中模型（含设备判定） |
| GET | `/v1/management/upstreams` | — | 上游实例列表（状态/已加载数/显存/延迟/类型/vram_total） |
| GET | `/v1/management/upstreams/{name}` | — | 单实例详情：`models`（该上游 **config.yaml 声明**的模型，`model_aliases` 短名作为显示名；前缀通配如 `qwen3:*`、`opencode/*` 用路由同一个发现调用展开成命中的具体模型，展开为空时保留通配本身以暴露失效声明；末尾追加归属于该上游的根 `aliases:` 派生模型名）、`running_models`（Ollama 取 `/api/ps` 运行态，含设备与显存/占用并附别名，`derived` 标记区分派生行——它们沿用目标模型的运行态、`size_gib`/`vram_gib` 为 `null`；`opencode` 类型只要健康探测有效即全部在线，设备上报 `"cloud"`、显存 `null`，前端显示 `Cloud` 与「—」）、健康；离线返回 `503` + `unreachable` 标记 |

**`GET /v1/management/overview` 响应示例**
```json
{
  "kpi": {
    "upstreams_online": 2, "upstreams_total": 2,
    "running_models": 2,
    "vram_used_gib": 21.4, "vram_total_gib": 31.8,
    "requests_today": 1284, "tokens_today": 287431, "p95_ms": 412, "error_rate": 0.006
  },
  "upstreams": [
    { "name": "Ollama-Arc-B65", "type": "ollama", "status": "online",
      "loaded": 2, "vram_used_gib": 18.2, "vram_total_gib": 31.8, "probe_ms": 42 }
  ],
  "running_models": [
    { "model": "qwen3.6:35b-pruned", "display": "q35-35b", "alias": "q35-35b", "derived": false,
      "upstream": "Ollama-Arc-B65", "device": "gpu", "device_ratio": 1.0,
      "num_ctx": 65536, "size_gib": 21.1, "vram_gib": 21.1, "expires_at": null },
    { "model": "qwen3.6:35b-nothink", "display": "qwen3.6:35b-nothink", "alias": null,
      "derived": true, "target": "qwen3.6:35b-pruned", "params": {"think": false},
      "description": "35B with thinking disabled",
      "upstream": "Ollama-Arc-B65", "device": "gpu", "device_ratio": 1.0,
      "num_ctx": 65536, "size_gib": null, "vram_gib": null, "expires_at": null },
    { "model": "opencode/big-pickle", "display": "opencode/big-pickle", "alias": null,
      "derived": false,
      "upstream": "OpenCode", "device": "cloud", "device_ratio": null,
      "num_ctx": null, "size_gib": null, "vram_gib": null, "expires_at": null }
  ]
}
```

**设备判定字段 `device`**
- `gpu`（ratio ≈ 1.0）→ 显示「GPU」
- `partial`（0 < ratio < 1.0）→ 显示「部分」
- `cpu`（ratio = 0）→ 显示「CPU」
- `cloud`（可访问的 `opencode` 托管上游）→ 显示「Cloud」
- 非 Ollama / 离线 → `device: null`，前端显示「—」

徽标只区分设备类型，**不显示占用百分比**（`device_ratio` 仍随响应返回，供图表等使用）。

`running_models` 同时包含 Ollama 实例里真正驻留的模型（`/api/ps`）、`opencode` 类型上游声明的托管模型和
根 `aliases:` 声明的**派生模型**：托管模型没有加载状态，健康探测一旦有效就整批算在线，显存/大小/上下文一律为
`null`；派生模型（`derived: true`）与它的 `target` 共用同一个实例，设备、`device_ratio`、`num_ctx` 都沿用目标模型
的运行态，`size_gib` / `vram_gib` 固定 `null`（前端显示「—」），因为那份显存已经计在目标模型名下。
`display` 是 `model_aliases` 短名（没有则真实 id），`alias` 为 `null` 表示未配置显示别名——注意根 `aliases:`
不会给目标模型改名，它自己成为一行。KPI 的 `running_models` 只统计**真实模型**（驻留 + 托管），派生行不进 KPI，
所以列表比数字多几行是预期行为，不是漏计。

### 3.3 模型清单与配置投影
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/management/models/llm` | 每个上游在 **config.yaml 声明**（`upstreams[].models`）的模型，一行一个名字 + aliases + num_ctx_overrides；前缀通配按上游模型端点展开成命中的具体模型，实例里装了但没声明的模型不出现；根 `aliases:` 的派生模型是独立一行，紧跟其目标模型 |
| GET | `/v1/management/models/rerank` | 返回 rerank 配置：`enabled` / `endpoint` / `mode` / `models[]` / `keep_alive` / `top_logprobs` / `upstream_api` / `on_error` / `max_concurrency`（rerank 无 api_key 字段，无需掩码） |
| GET | `/v1/management/models/systemone` | 返回 systemone 配置：`enabled` / `backend` / `model` / `api_key_env` / `path` / `timeout` |
| GET | `/v1/management/tools/websearch` | 返回 web-search 配置：`enabled` / `upstream` / `path` / `api_key_env`(掩码) / `timeout` / `base_url`(由 upstream 派生) |

**`GET /v1/management/models/llm` 响应示例**
```json
{
  "models": [
    { "model": "qwen3.6:35b-pruned", "display": "q35-35b", "alias": "q35-35b", "derived": false,
      "upstream": "Ollama-Arc-B65", "type": "ollama", "wildcard": false,
      "running": true, "device": "gpu", "device_ratio": 1.0,
      "num_ctx": 65536, "size_gib": 21.1, "vram_gib": 21.1 },
    { "model": "qwen3.6:35b-nothink", "display": "qwen3.6:35b-nothink", "alias": null,
      "derived": true, "target": "qwen3.6:35b-pruned", "params": {"think": false},
      "description": "35B with thinking disabled",
      "upstream": "Ollama-Arc-B65", "type": "ollama", "wildcard": false,
      "running": true, "device": "gpu", "device_ratio": 1.0,
      "num_ctx": 65536, "size_gib": null, "vram_gib": null },
    { "model": "qwen3.5:4b", "display": "qwen3.5:4b", "alias": null, "derived": false,
      "upstream": "Ollama-Arc-B65", "type": "ollama", "wildcard": true,
      "running": false, "device": null, "device_ratio": null,
      "num_ctx": 32768, "size_gib": null, "vram_gib": null },
    { "model": "opencode/big-pickle", "display": "opencode/big-pickle", "alias": null,
      "derived": false,
      "upstream": "OpenCode", "type": "opencode", "wildcard": true,
      "running": true, "device": "cloud", "device_ratio": null,
      "num_ctx": null, "size_gib": null, "vram_gib": null }
  ],
  "aliases": [
    { "alias": "q35-35b", "kind": "rename", "target": "qwen3.6:35b-pruned", "upstream": "Ollama-Arc-B65",
      "params": {}, "description": null },
    { "alias": "qwen3.6:35b-nothink", "kind": "derived", "target": "qwen3.6:35b-pruned",
      "upstream": "Ollama-Arc-B65", "params": {"think": false},
      "description": "35B with thinking disabled" }
  ],
  "num_ctx_overrides": { "qwen3.5:4b": 32768 }
}
```
- 行只来自 `upstreams[].models`：显式声明的名字原样列出，实例里装了却未声明的模型**不会**出现（它本来就不可路由）
- `wildcard` 表示该行是 `qwen3:*`、`opencode/*` 这类前缀声明经上游模型端点展开后命中的具体模型；
  通配展开不到任何模型时（含上游离线），保留通配本身成一行、`running: false`，前端仍打 `wildcard` 标记，
  而不是谎称一个模式串在线
- `display` 配了 `model_aliases` 短名时是短名，`alias` 为短名本身、未配置时是 `null`；`model` 始终是真实上游 id。
  显示别名不再混进 `model` 字段，也**不会给目标模型改名**，映射关系另由 `aliases[]` 表达
- `derived: true` 的行来自**根 `aliases:`**——它是一个独立模型：`model` 与 `display` 都是别名的名字，
  真实 id 在 `target`，`params` / `description` 原样给出（固化参数与说明），`alias` 恒为 `null`。
  这类行紧跟在它的目标模型之后（目标未在任何上游声明时排在末尾），`running` / `device` / `device_ratio` /
  `num_ctx` 沿用目标模型的运行态，`size_gib` / `vram_gib` 恒为 `null`
- `aliases[]` 每项带 `kind`：`rename`（`upstreams[].model_aliases`，只改名）或 `derived`（根 `aliases:`，
  带独立固化参数的新模型），另有 `target` / `upstream` / `params` / `description`
- `running`：Ollama 看是否真在 `/api/ps` 里驻留；`opencode` 类型只要健康探测有效即整批 `true`
- 设备与 `size_gib` / `vram_gib` 只有驻留模型才有（`running: false` 与非 Ollama 一律 `null`，前端显示「—」；
  可访问的 `opencode` 托管行例外：`device: "cloud"`，内存仍为 `null`）；
  `num_ctx` 优先取 `/api/ps` 的 `context_length`，缺失时回退 `num_ctx_overrides`

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
> 三张分布图（各 Upstream / 各 Model / 成功失败）均由该接口按不同 `group_by` 取数；前端用 **ECharts** 渲染**实心饼图**（不是圆环）：饼上不画任何标签（长模型 id 曾与图形叠在一起），名称与计数收在**图形下方一行一条**的可滚动图例里（9.5px 字号 / 10.5px 行高、名称超 16 字截断并附计数），圆盘半径按容器短边计算并上移，图例永不压到扇区；颜色随主题切换明暗。趋势图（柱+折线）由 `stats/timeseries` 驱动，同样为 ECharts。

> 说明：Dashboard 已移除「失败请求明细」列表（失败明细统一在 Requests 页按状态筛选查看）；成功/失败分布以饼图呈现，不再单列错误样例表。后端仍保留 `GET /v1/management/stats/failures`（返回 `by_code` / `by_upstream` / `samples`），本期前端不再调用。

### 3.5 接口测试（api-test）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/management/api-test/models` | 每个 API 家族测试窗口需要的全部前置信息：`kinds` 以 `generate` / `chat` / `embed_ollama` / `embed_openai` / `rerank` / `systemone` / `websearch` 为键，每键给 `method` / `endpoint` / `family` / `model_required` / `free_model` / `enabled` / `source`（选项来自哪个配置项）/ `models[]`（`model` / `display` / `alias` / `derived` / `target` / `upstream` / `type` / `running` / `device` / `api_key`（掩码）/ `unknown`）/ `default_model` / `api_key`（该家族自己签名的密钥，掩码）/ `api_key_env`（只给变量名）；顶层另有 `max_response_bytes`。`api_models` 为空时按配置推导，其中 `/api/...` 原生家族（`generate` / `embed_ollama`）只保留 `type: "ollama"` 上游的行，`/v1` 家族也列 `lmstudio` / `opencode` 的模型，显式列表原样返回。一次探测所有上游，前端整站共用一份缓存 |
| POST | `/v1/management/api-test` | Body `{"kind": "...", "body": {...}}`；**服务端**用网关自己的服务发出这一笔，返回信封 `status` / `ok` / `duration_ms` / `ttft_ms` / `content_type` / `stream` / `requested_model` / `model` / `upstream` / `injected` / `body` / `text` / `truncated` / `error`。`kind` 不在白名单，或需要模型的家族没给 `model` → `400` |

**`POST /v1/management/api-test` 响应示例**

```json
{
  "kind": "generate", "method": "POST", "endpoint": "/api/generate", "stream": true,
  "requested_model": "qwen3:8b-nothink", "model": "qwen3:8b", "upstream": "Ollama-Arc-B65",
  "injected": { "think": false },
  "status": 200, "ok": true, "duration_ms": 812, "ttft_ms": 260,
  "content_type": "application/x-ndjson",
  "body": null,
  "text": "{\"model\":\"qwen3:8b\",\"response\":\"H\"}\n{\"response\":\"i\"}\n...",
  "truncated": false, "error": null
}
```

> 为什么必须服务端执行：管理台会话 Cookie 只满足 `require_admin`，它**不是**调用方的网关 API Key，
> 浏览器直接 `fetch /api/generate` 会被网关鉴权拒掉。`app/api_test.py` 构造一个最小 ASGI `Request`
> 交给 `app.state.proxy` / `rerank` / `systemone` / `websearch`，所以别名注入、`num_ctx_overrides`
> 裁剪、路径屏蔽、请求明细落库与真实客户端完全一致；`KINDS` 是封闭白名单，不能借它打任意地址。
> 网关自身拒绝的调用（405 / 400 / 404）作为**结果数据**回到信封里，测试窗口展示的就是外部客户端
> 会看到的那一页。响应体上限 128 KB，超出记 `truncated: true`。
>
> 测试窗口从「请求参数」标题右侧的**测试**按钮打开，是页面上的浮层而不是文档下方的内嵌块；
> 首次打开才挂载、之后只切换显示，所以关闭再开不会丢掉填过的内容与上一次答复。模型是真正
> 的下拉框，**只显示名字或别名**（上游与运行状态在悬停提示）；候选同样按方言分流——`/api/generate`、
> `/api/embed` 是 Ollama 原生接口，只列 `type: "ollama"` 上游的模型，`lmstudio` / `opencode` 的模型
> 只出现在 `/v1/chat/completions`、`/v1/embeddings` 窗口。`free_model` 家族在末尾多一项
> 「自定义模型…」，选中后换成手输输入框（自动聚焦，带 datalist 候选），选回列表里的名字即收起；
> 表单**打开时已填好初始值**，「恢复默认值」重填、「清空」清空。**模型可以清除**：字段旁的 ×
> （或 select 的空白项）清空后请求体不带 `model`，`model_required` 的家族由窗口先拦下，
> `free_model` 的家族交给端点决定；「恢复默认值」会重新选中 `default_model`。**有文档默认值的
> 布尔参数是复选框**，勾选与否都显式发送 `true` / `false`；其余空字段不会进入请求体。两个 LLM
> 窗口的 `stream` 默认 `false`（先拿到单个 JSON），generate 另有默认关闭的 `think` 复选框，
> chat 提供 `reasoning_effort`。

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
| `tokens_in` / `tokens_out` | INTEGER | 输入 / 输出 token 数；上游未回报时为 NULL（与真实的 0 可区分） |
| `tokens` | INTEGER | 总用量（优先取上游的 `total_tokens`，否则 `tokens_in + tokens_out`） |
| `req_size` / `res_size` | INTEGER | 请求/响应体字节 |
| `client_ip` | TEXT | 客户端 IP |
| `error` | TEXT | 错误信息（可空） |
| `request_body` / `response_body` | TEXT | 截断至 8KB、敏感字段脱敏为 `***` |
| `request_body_truncated` / `response_body_truncated` | INTEGER(0/1) | 是否被截断 |

索引：`ts`、`model`、`upstream`、`status`。保留策略：按天数滚动清理（默认 30 天，`REQUEST_LOG_RETENTION_DAYS=0` 永久）。
旧库升级：启动时对比 `PRAGMA table_info(requests)`，缺失列用 `ALTER TABLE ADD COLUMN` 补齐，历史数据不动。

### 4.1b 入库开关
`config.yaml` 的 `request_log.enabled`（默认 `true`）逐请求生效，支持热加载：
- 关闭时代理层直接跳过请求体/响应体采集与写库，管理台 `request_log.enabled` 回报 `false`；
- 打开后恢复记录，无需重启；`ConfigManager` 每秒最多比对一次配置 mtime，因此开关在约 1 秒内生效
  （已用本地 Ollama 实例验证：关→零新增行，开→恰好一行）；
- 数据库路径与保留天数仍由环境变量 `REQUEST_LOG_DB` / `REQUEST_LOG_RETENTION_DAYS` 决定。

### 4.2 配置只读投影
全部来自 `ConfigManager`（config.yaml）经 Pydantic 校验后的安全子集，绝不回显 API Key 明文。

---

## 5. 实施记录（已落地）
1. `app/request_log.py`：SQLite 建表 + 异步批量写入队列 + 统计查询接口（timeseries/distribution/分页）。
2. `app/proxy.py`：在转发出口埋点（覆盖流式/非流式/异常），`config.py` 给 `UpstreamConfig` 增加 `vram_total_gb`。
3. `app/management.py`：`/v1/management/*` 只读接口，复用 `require_admin`（会话 Cookie 或网关 API Key）。
4. `app/main.py`：先注册 management 路由，再注册 `/admin` 静态路由，最后才是 catch-all 代理。
5. `app/bench.py`：测速引擎（Python 重写 `bench_llm.mjs`，无 Node 依赖）——预热 + N 轮流式请求，
   Decode/Prefill 优先采信服务端计时（`eval_duration` / `predicted_ms`），缺失回落墙钟，口径记在
   `timing_source`；思考模型自动关 think；测速前 preflight 对齐模型名；内部 client 用
   `trust_env=False`（否则本机 `HTTP_PROXY` 会把上游请求绕到代理上，表现为 502）。
6. `app/bench_store.py`：`bench_results` 表（与 `requests` 同库、同 WAL 队列写入），每个
   `(upstream, model)` 只留最新 3 份；`/bench/history` 读它。
7. `app/api_test.py` + `config.py` 的 `ApiModelsConfig`：接口测试窗口的执行引擎与按家族收窄的
   可选模型。执行走**服务端**（管理台 Cookie 不是调用方 API Key），`KINDS` 是 7 项封闭白名单，
   构造最小 ASGI `Request` 后交给 `app.state.proxy` / `rerank` / `systemone` / `websearch`，
   于是别名注入、`num_ctx` 裁剪、路径屏蔽与请求明细落库都和真实客户端一致；响应上限 128 KB，
   流式响应读完立即 `aclose`（代理的生成器在 `finally` 里写日志行）；网关自身拒绝的调用
   （405/400/404）作为结果数据回传而不是让 `/api-test` 报错。
8. `web/src/components/ApiTester.vue` + `web/src/composables/apiTestContext.ts`：7 个测试窗口挂在
   5 个页面的「API 用法」标签页里，由每个 `ApiDoc` 的「请求参数」标题右侧**测试**按钮以浮层打开
   （首次打开才挂载，之后 `v-show` 切换，填过的内容与上次答复都保留）。参数表由所在视图以
   `TestField[]` 声明（`text` / `number` / `bool` / `lines` / `json` / `select`，`def` 就是文档里的
   默认值，**打开时就用它填好**，空输入**整个字段不发送**，`a.b` 形式的 key 会写进嵌套对象）；
   模型选项**只显示名字或别名**，上游/设备/运行状态在 `title` 提示里。上下文（可选模型、默认项、
   `free_model`、掩码密钥、`enabled`）来自模块级共享的 `useApiTestContext()`，一次探测全站复用，
   只有首次与「重新探测」才会再打 `/api-test/models`。
9. `web/src/`：前端页面（依 `dashboard-mockup.html` 风格稿与 `images/*.png` 实现，9 视图 + 详情抽屉 +
   深/浅主题 + 侧栏折叠 + **ECharts** 图表 + 关键字/时间筛选 + 分页）。
10. 文档同步：`.env.example`、`config.example.yaml`、`README`、`CHANGELOG`、`docs/API.md`、
    `docs/TESTCASES.md`、`docs/DEPLOY.md`、`AGENTS.md`。

与设计稿的实现差异（均为有意选择）：
- **前端位置**：代码是 Vue 3 + TypeScript 单文件组件（Vite + Element Plus + axios + ECharts），
  `*.ts` 模块承载接口类型（`src/types/api.ts`）与状态，SFC 统一 `<script setup lang="ts">`，源码保持在需求指定的
  `web/src`，不在 `app/static/admin/`；ECharts 通过 npm 依赖 tree-shaking 打进 `app/web/assets`，
  无需再 vendored `echarts.min.js`。
- **产物目录**：构建输出从最初的 `web/dist` 调整为 **`app/web`**（`vite.config.ts` 里
  `build.outDir = '../app/web'`）。产物与模块同包同层，`app/main.py` 只需一个解析路径
  `Path(__file__).parent / "web"` 就同时适配本地（`app/web`）与镜像（`/app/web`，与 `app/static`
  一样的平铺布局），`Dockerfile` 的 `COPY app/web /app/web` 也跟 `COPY app/*.py` 并列在同一层。
  代价是宿主机以 `-v ./app:/app` 覆盖模块目录时必须先构建，否则 `/admin` 返回 503。
- **挂载点**：静态资源挂在 `/admin`（`vite.config.ts` 里 `base: '/admin/'`、路由
  `createWebHistory('/admin/')`），与既有的 `/admin/login|logout|ping`、CSP 作用域和
  `path_allowlist` 豁免共用同一前缀；开发时 `npm run dev` 走 `http://localhost:5173/admin/`，
  `/v1/management` 代理到本地 8000 网关，保证 Cookie 同源。
- **刷新方式**：Dashboard 与 Upstreams 每 **60 秒**自动刷新一次（`POLL_MS = 60000`；`onMounted` 起定时器、
  `onBeforeUnmount` 清理，离开页面即停止探测——一次刷新会并发探测所有上游，5 秒一轮会把网关的上游探测打满）；
  其余页面进入时加载一次，靠顶栏「刷新」按钮手动刷新。
- **刷新可见性**：每个可刷新视图只有一个 `refreshing` 状态（重入即直接返回，`finally` 里复位），
  并把它交给 `TopBar` 的 `loading` prop。顶栏据此旋转并禁用 `↻`、在底部走一条延迟淡入的不确定进度条、
  把副标题换成「刷新中…」，Dashboard 还额外让 KPI 卡降透明度、给图表盖薄纱。`TopBar` 内部不再有
  自计时器——那种 600ms 的假旋转会在探测仍在跑的时候就宣称完成。
- **静态资源**：`web/public/` 里的文件（目前是 `favicon.svg`）由 Vite 原样复制进 `app/web/`，
  与哈希产物同居一层，因此同一套 `/admin/{asset_path}` 路由即可服务；`index.html` 里的
  `/favicon.svg` 会按 `base` 改写成 `/admin/favicon.svg`。
- 401 一律登出并跳回登录页（保留 `redirect`），503 视为业务答复而非鉴权失败。

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
