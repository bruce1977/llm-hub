# 更新日志

本文件记录项目的所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [未发布]

### 变更（管理台源码迁移到 TypeScript）

- `web/src` 现在全部是 TypeScript：所有非 SFC 模块改为 `.ts`（`main.ts`、`api/client.ts`、`api/management.ts`、`stores/{session,theme,gateway}.ts`、`composables/chartTheme.ts`、`plugins/echarts.ts`、`router/index.ts`、`utils/format.ts`）以及 `vite.config.ts`，所有单文件组件统一使用 `<script setup lang="ts">` 和带类型的 `defineProps<T>()` / `defineEmits<T>()` / `defineModel<T>()`。旧的 `.js` 文件与 `vite.config.js` 已删除，`index.html` 改为加载 `/src/main.ts`
- 新增 `web/tsconfig.json`（`strict`、`moduleResolution: "bundler"`、`verbatimModuleSyntax`、`isolatedModules`、`noUnusedLocals` / `noUnusedParameters`、`@/*` → `src/*`），类型检查器为 `vue-tsc`。`npm run typecheck` 即 `vue-tsc --noEmit`；`npm run build` 会在 Vite 之前先跑同一套检查，组件 props 或接口载荷不匹配会直接让构建失败，而不是发布一个空表格
- 接口类型取自真实的 `/v1/management` 响应：`src/types/api.ts` 为每个响应定义一个接口，并如实标注可空性（请求日志行里的 `stream` 是 SQLite 的 0/1 整数，详情里才是布尔值；`loaded`、`vram_total_gib`、`ttfb_ms`、`tokens`、`query`、`requested_model`、`models_error` 等可为空/缺省）；`src/types/ui.ts` 描述展示组件的 props；`Badge` / `BadgeClass` 放在 `utils/format.ts`；`plugins/echarts.ts` 导出经 `ComposeOption` 收窄的 `ChartOption` 与 `ChartInstance`。`api/management.ts` 用 axios 泛型逐个包装路由，后端字段一旦改名会先在包装层编译报错
- 视图的 `catch` 参数改为 `unknown`，统一走 `api/client.ts` 的 `statusOf()` / `errorResponseBody()`，不再对 `any` 写 `error.response?.status`；行为不变（401 仍跳回登录页，503 仍是业务语义）。模板与样式没有任何改动
- 重新构建了 `app/web`，并在线复验管理台：登录、Dashboard 的 KPI 与图表、Upstreams 展开（在线实例与 503 `unreachable` 分支）、Models·LLM 的模型发现、Rerank / System One / Web Search / Requests、请求详情抽屉以及深浅色切换。`npm run typecheck` 零错误，四个 mock 套件依旧全绿（123 / 63 / 158 / 15）
- 文档：AGENTS.md（目录树、Console Development、Web 管理台一节）、README / README_CN、`docs/DEPLOY.md` 与 `docs/web-management/web-management-implementation.md` 均已改为描述 TypeScript 源码与 `npm run typecheck` 步骤

### 新增（`/api/generate` 路由）

- `POST /api/generate`（Ollama 原生的文本补全端点）现在是 `app/main.py` 里的**显式路由**，不再只靠兜底转发。行为完全没变（代理本来就按 `model` 路由、应用别名并注入参数、钳制 `num_ctx`、NDJSON 流式透传并写请求日志），显式注册的意义是让它出现在 **`/openapi.json` 和 `/docs`** 里：带请求 schema（必填 `model` + `prompt`，另有 `system`、`template`、`context`、`raw`、`images`、`format`、`stream`、`keep_alive`、`logprobs`、`top_logprobs`、`options` 等；`additionalProperties` 保持开放，Ollama 以后新增字段照样透传），200 响应同时声明 `application/json` 与 `application/x-ndjson`
- `/api/generate` 加入 `POST_ONLY_PATHS`，对它发 `GET` / `PUT` / `DELETE` 现在由网关直接返回 **405** 并带 `Allow: POST`，而不是转发给上游
- 这个路径只对 `type: "ollama"` 上游有意义——OpenAI 兼容或 opencode 上游会返回它自己的 404，网关原样透传
- 测试：`tests/smoke_test.py` 新增分组 `[18] Ollama 原生文本生成（/api/generate）`（19 项——单个 JSON 对象与 NDJSON 分块数、省略 `stream` 时缺省即流式、`prompt` / `raw` / `format` / `keep_alive` / `options` 透传、别名 `big-nothink` 固定上游同时替换 `model` 并注入 `think:false` + `num_ctx:8192`、无 Key 时 401、405 与 `Allow` 头），mock 用例总数 **123**；`tests/test_gateway_new.py` 校验 schema（路径存在、必填字段、属性覆盖、两种响应 media type），总数 **63**
- 文档：`docs/API.md` 文本补全一节重写（完整参数表、NDJSON 示例、仅 POST/405 说明）；`docs/TESTCASES.md` 测试 12 重写；README / README_CN 在流式特性行与请求示例里补上 `/api/generate`；AGENTS.md 新增「Schema 可见的代理路由」一节并修正「新增端点」步骤

### 新增（本地启动脚本）

- 新增 `scripts/start-local.ps1`（外加 `scripts/start-local.bat`，一个用 `-ExecutionPolicy Bypass` 调用它的 cmd 包装）：定位 python（先 `python`，再 `py -3`，可用 `-Python` 指定）、读取 `.env`、找到 `config.json`，并在启动前**跑一遍应用自己的规则**，因此检查不会与实现走偏——`config.json` 由真实的 Pydantic 模型解析，密钥由 `auth.find_weak_keys` 筛查
- 启动前检查覆盖本地常见的失败原因：缺依赖（并提示用 `-Install`）、配置被校验器拒绝、开了 `auth.enabled` 却没有任何 Key、弱/占位 Key（只有 `security.fail_on_weak_keys` 判定会导致启动失败时才报错退出，否则只告警）、`ADMIN_PASSWORD` 未设置（管理台与 `/v1/management` 会返回 503）、`app/web/index.html` 缺失（需要重新构建产物）、端口被占用（打印占用者的 PID 与进程名）
- 启动方式按扁平模块的要求来：`--app-dir app`，且工作目录设为 `app/`，与镜像的 `WORKDIR /app` 一致，因此 `.env` 里的相对路径（`../data/config.json`、`../data/.keys`）在宿主机和容器里解析到同一份文件。原先文档里的 `python -m uvicorn app.main:app` 已从 AGENTS.md **删除**——它根本跑不起来，模块之间用裸名互相 import（`ModuleNotFoundError: No module named 'admin_auth'`）
- 配置解析优先级：`-Config`（相对调用者所在目录）> `.env` 里的 `CONFIG_PATH`（相对 `app/`，仅在文件存在时采用）> `<repo>\data\config.json`（不存在时从 `data\example.config.json` 复制并告警）。shell 中已导出的变量优先于 `.env`；`GATEWAY_API_KEYS_FILE` 读不到而内联 `GATEWAY_API_KEYS` 可用时直接丢弃该变量并说明原因；没装 `watchfiles` 时自动降级为不带 `--reload` 启动
- 参数：`-Port` / `-Bind` / `-LogLevel`（默认取配置里的 `server.*`）、`-EnvFile`、`-Python`、`-NoReload`、`-Install`、`-CheckOnly`（只检查不启动）。脚本只打印密钥的**名字**，从不打印值
- 文档：README / README_CN 新增「Launcher / 启动脚本」一节与参数表；`docs/DEPLOY.md` 在构建/发布脚本旁边记录本机启动脚本；AGENTS.md 在 `scripts/` 目录树中列出两个文件，替换掉失效的本机开发命令，并在常见问题里补一条

### 新增（Web 管理台前端）

- 新增 `web/`：Vue 3 + Vite + Element Plus + axios + ECharts 的管理台单页应用，源码在 `web/src`，`npm run build` 把产物写入 **`app/web`** 并随仓库提交，由 FastAPI 直接以静态文件服务在 **`/admin`**（产物与模块同级，本地 `app/web` 与镜像 `/app/web` 是同一个解析路径；`Dockerfile` 只 `COPY app/web /app/web`，镜像内不含 node 工具链；开发时 `cd web && npm install && npm run dev`，发布前 `npm run build`）
- 八个页面：Dashboard（KPI + 24h/7d 趋势 + 上游/模型/状态三张饼图）、Upstreams（列表展开详情、显存进度条、GPU/部分/纯 CPU 设备标记、不可达上游 503 提示）、Models·LLM（上游模型映射 + 别名 + `num_ctx_overrides`，只读投影自 `config.json`）、Models·Rerank、Models·System One、Tools·Web Search（含参数说明与请求/响应示例）、Requests（时间/上游/模型/状态/路径筛选 + 分页 + 详情抽屉展示截断后的请求/响应体）、Settings（占位）；图表全部用 ECharts 实现，颜色取自 CSS 变量，因此深浅色主题切换时图表同步换色
- 登录页用户固定为 `admin`，密码来自 `ADMIN_PASSWORD`；会话为 12 小时 HttpOnly Cookie，路由守卫在每次进入受保护页面前探测 `/auth/ping`，任何管理接口返回 **401** 都由 axios 拦截器统一登出并跳回登录页（保留 `redirect` 参数，登录后回到原页面）；**503 不当作鉴权失败**——它分别表示上游不可达、`ADMIN_PASSWORD` 未配置（登录页转为「未启用」锁定态）或前端尚未构建
- `request_log.enabled` 为 `false` 时 Dashboard 与 Requests 顶部显示「请求日志已停用」提示条，而不是呈现一张空表；表格在首次加载完成前显示「加载中…」，避免上游探测较慢时被误读成「没有匹配的模型」
- 静态资源服务细节：`/admin` 307 跳转到 `/admin/`，非资源路径回退到 `index.html` 以支持 SPA 前端路由；`assets/*` 内容哈希文件名用 `immutable` 长期缓存、`index.html` 用 `no-cache`；文件路径经 `resolve()` + `is_relative_to()` 校验，`..` 无法跳出构建目录；`app/web` 缺失时返回 **503** 并附带 `npm run build` 提示。静态壳层故意不鉴权（登录页本身要能打开），但页面内每一次数据请求仍受 `require_admin` 保护
- 主题（深色 / 浅色）持久化在 `localStorage`，与 Element Plus 的 `html.dark` 变量一并生效；侧边栏可折叠，版本号取自 `/overview` 的 `gateway_version`
- 测试：`tests/test_management.py` 新增第 12 组「web console static serving」（11 项），覆盖 307 跳转、匿名可取 `index.html` 与 SPA 深链回退、CSP `frame-ancestors 'self'`、`no-cache` / `immutable` 缓存头、目录穿越回退与未构建 503
- 文档与配置：`Dockerfile` 新增 `COPY app/web /app/web`；`.gitignore` 忽略 `web/node_modules/`、`web/.vite/`、`web/.vite-temp/`（`app/web` 相反地**需要提交**）；README / README_CN 补充管理台特性行、`web/` 与 `app/web` 目录、`/admin` 访问地址与构建步骤；`docs/DEPLOY.md` 说明镜像已内置前端与 `/admin` 入口；`docs/API.md` 的管理 API 章节改述为「Cookie 或网关 API Key」；`docs/web-management/web-management-implementation.md` 记录实现状态；同步更新 `docs/TESTCASES.md`（新增测试 41「`/admin` 静态服务」）、`.env.example` 与 AGENTS.md

### 新增（Web 管理台管理 API）

- 新增只读管理 API `/v1/management`（实现在 `app/management.py`），由 `require_admin` 保护：`auth/login|logout|ping`、`overview`、`upstreams[/{name}]`、`models/llm|rerank|systemone`、`tools/websearch`、`requests[/{id}]`、`stats/timeseries`、`stats/distribution`、`stats/failures`。放行两种凭据：**有效的 12 小时 HMAC 会话 Cookie**（登录页签发）或**网关 API Key**（`auth.api_keys`，即 `Bearer` / `X-API-Key`，供脚本与 curl 使用）；`ADMIN_PASSWORD` 只是登录凭据，直接把它当作请求头凭据提交一律 **401**，查询串里的 key 也不接受。未配置 `ADMIN_PASSWORD` 时所有接口返回 **503**
- `overview` / `upstreams` 并行探测各上游（5 秒超时，单个实例离线不会拖慢整页），并按 `size_vram / size` 把 Ollama 运行中模型标记为 GPU / 部分 / 纯 CPU；不可达上游返回 `status: offline`，详情接口返回 **503** 与 `"unreachable": true`
- 配置投影只读且不回显凭据：上游 header 中的密钥、System One 环境变量密钥与 Web Search provider 密钥统一返回 `••••••`（只告知该环境变量是否已设置）；`models/llm` 含别名与 `num_ctx_overrides`，`models/rerank` 额外给出 `upstream_api`（上游协议）与 `on_error`（失败策略），`models/rerank` / `models/systemone` 附带请求/响应示例
- `tools/websearch` 为管理台 Tools 页投影 `websearch` 配置段，并用 `Config.websearch_upstream_obj()` 派生 `base_url` / `upstream_type` / `provider_configured`——与 `WebSearchService` 用的是同一个解析函数，页面不会显示转发器不会调用的地址
- 新增 `app/request_log.py`：SQLite 请求明细库（`llmhub.db`，WAL，`synchronous=NORMAL`），经有界内存队列（10000 条，满时丢弃并告警）由单个后台任务按 100 条批量 `asyncio.to_thread` 落盘，转发主链路不等待磁盘。默认保留 30 天（每小时清理），可用 `REQUEST_LOG_DB` / `REQUEST_LOG_RETENTION_DAYS`（0 = 永久保留）调整
- `requests` 表逐行记录完整交互：时间（`ts` 到达、`ts_end` 完成——流式在流关闭时写入、`duration_ms`、`ttfb_ms`）、请求参数（`method`、`path`、`query`、`requested_model` 客户端写的原名、别名解析后的 `model`、`request_body`、`req_size`、`stream`、`client_ip`）与响应结果（`status`、`upstream`、`response_body`、`res_size`、`tokens`、`error`），外加两个 `*_truncated` 标记。`_init_db` 会用 `PRAGMA table_info(requests)` 与目标结构比对，缺列就 `ALTER TABLE ADD COLUMN`，旧库原地升级
- 新增 `config.json` 配置段 `request_log`，其 `enabled`（默认 `true`）是入库总开关：`ProxyService._logging_on` **逐请求**判定，因此改完保存无需重启即可生效——配置 mtime 每秒最多比对一次，开关在约 1 秒内生效（已对本地 Ollama 实测：关闭期间零新增行，重新开启恰好一行）；关闭时不采集请求/响应体、也不产生任何行。`/v1/management/overview` 与 `/v1/management/requests` 会以 `request_log: {enabled, db, retention_days, dropped}` 回报状态，前端可据此提示「日志已停用」而不是显示空列表
- `proxy.py` 覆盖所有出口埋点：被拦截（403）、请求体过大（413）、无可用上游（404）、流式、非流式，以及连接/读取/HTTP 失败（502/504）；记录方法、路径、解析后的真实模型名、上游、状态码、耗时、TTFB、流式标记、token 用量（`usage.total_tokens` 或 Ollama 的 `prompt_eval_count + eval_count`）、请求/响应字节数、客户端 IP 与截断后的请求/响应体
- 请求/响应体落库前截断至 8 KB，`authorization` / `api_key` / `token` / `password` 等字段替换为 `***`；详情接口返回供前端抽屉展示
- `GET /v1/management/requests` 支持 `start` / `end` / `upstream` / `model` / `status`（2xx\|3xx\|4xx\|5xx）/ `path` / `page` / `page_size`，返回 `stats`（数量、P50/P95/P99、错误率）、`rows` 与 `pagination`；`stats/timeseries` 按小时/天分桶统计数量、错误数与 P95；`stats/distribution?group_by=upstream|model|status` 返回驱动总览页三张饼图的分组计数（`status` 只分 `ok`（<400）与 `error`（≥400），未命中上游的行计入 `-`，因此各项相加恒等于明细总数；其他 `group_by` 取值返回 **400**）；`stats/failures` 给出按状态码、按上游的失败分布及最近样本
- `UpstreamConfig.vram_total_gb`：可选，声明上游主机显存总量，供总览页「已用 / 总量」进度条使用（无法通过 API 探测）
- `/v1/management` 在 catch-all 之前注册，并与 `/admin` 一样不受 `security.path_allowlist` 限制；旧的 `/admin/login|logout|ping` 改为复用同一实现
- 测试：新增 `tests/test_management.py`（158 项，含上一节的 11 项控制台服务检查），使用本地 mock Ollama 覆盖鉴权（会话 Cookie 与网关 API Key 均可 **200**；把密码当作 `Bearer` / `X-API-Key` → **401**；未知 key → **401**；key 放查询串 → **401**；用户名错误 → **401**；用户名大小写与空格会被规范化；未配置 503）、探测与设备判定、掩码投影（含 `tools/websearch` 与 rerank 的 `upstream_api` / `on_error`）、流式/非流式/502 埋点、新增列（到达/完成时间、别名的 `requested_model` 与解析后 `model`、`query`）、筛选、分页、时序、分布（分桶约定、排序、总数与 `group_by` 校验）、`request_log.enabled` 关闭与重新开启，以及旧库 `ALTER TABLE` 升级
- 文档与配置：`docs/API.md` 新增「管理 API（Web 管理台）」章节，含 `requests` 字段清单、`request_log.enabled` 开关说明与 `stats/distribution` / `tools/websearch` 响应示例；README / README_CN 补充特性行、目录、`request_log` 配置段、`ADMIN_PASSWORD` / `REQUEST_LOG_*` 环境变量与 `vram_total_gb` 说明；`docs/TESTCASES.md` 新增测试 34-40；同步更新 `.env.example`、`data/example.config.json`（附带 `request_log`）、`.gitignore`（`data/*.db*`）、`docs/DEPLOY.md` 与 AGENTS.md

### 新增（联网检索）

- 新增端点 `POST /v1/tools/web-search`：由 OpenCode API Gateway 提供关键词网页检索，请求体与上游响应**原样透传**（`query`、`results[]`、拼接后的 `text`、`sources`，`synthesize: true` 时另有 `answer`）
- `POST /v1/tools/web-search` 在 provider 不可达时新增诊断日志：完整异常链 + 逐个地址的 TCP 探测结果（`dns(host) -> ip:port ConnectionRefusedError errno=111`、`TimeoutError`、`gaierror`），因为 httpx 会把它们统一收敛成 `All connection attempts failed`
- 新增 `websearch` 配置段：`enabled`、`upstream`、`path`（默认 `v1/web-search`）、`api_key_env`、`timeout`。`upstream` 按名字选检索方；留空则自动取第一个 `type: "opencode"` 的上游；填了不存在的名字返回 **503**，不会静默回退
- 新增上游类型 `opencode`：模型发现走 OpenAI 兼容的 `/v1/models`（解析 `data[].id`），存活探测走 `/health`。检索方作为普通上游登记后，其模型同时进入聚合的 `/v1/models`，聊天也可路由过去
- 可选的上游签名：`websearch.api_key_env` 指定环境变量名（默认为空 = 不签名，当前上游匿名即可访问）；`OPENCODE_GATEWAY_API_KEY` 已写入 `.env.example` 与 `docs/DEPLOY.md`
- 该端点在 `/docs` 中带完整的请求/响应 schema（`query` 必填，另有 `top_k`、`recency`、`synthesize` 等），仅接受 POST（否则 **405**），并沿用网关 API Key 鉴权（**401** / **403**）
- `data/config.json` 与 `data/example.config.json` 附带 `opencode` 上游（声明 `auto` 与 `opencode/*` 通配）及 `websearch` 段
- 文档：API.md 新增「Web Search（OpenCode 网关代理）」章节；README / README_CN 补特性行、`websearch` 配置详解、curl 示例、`opencode` 上游类型说明与斜杠通配注意点（`opencode/*` 而非 `opencode:*`）
- 测试：`smoke_test.py` +13（共 104）覆盖 `opencode` 类型与 `websearch` 配置解析；`test_gateway_new.py` +22（共 55）覆盖端点鉴权、转发、透传、签名与状态码映射及 OpenAPI schema；`test_api.py` +8（共 28）E2E 用例；docs/TESTCASES.md 新增测试 31-33（含实测响应）

### 新增（配置 / 上下文钳制）

- 新增顶层配置 `num_ctx_overrides`：`{ "<真实模型名>": <num_ctx 整数> }`，网关按 `resolved.target` 匹配后**强制覆盖** Ollama 上游请求体里的 `options.num_ctx`，忽略客户端传入值；仅对 `type=="ollama"` 上游生效（LM Studio / OpenAI 兼容用 `max_tokens`，不受影响）。目的：避免同一模型因客户端传入不同 `num_ctx` 被 Ollama 反复新建实例、显存叠加
- 实现：`config.py` 新增 `Config.num_ctx_overrides` 字段（Pydantic 校验 `dict[str,int]`）；`proxy.py` 在别名解析与参数注入之后、序列化之前执行钳制，覆盖时打印 `num_ctx clamped` 日志
- 文档补齐（此前仅落代码、未补配置说明）：`data/example.config.json` 加入示例；README / README_CN 的「配置详解」新增 `num_ctx_overrides` 专节（键为真实模型名、值为固定 ctx、仅 Ollama 生效、支持热重载）；本条目同步记录

### 新增（构建 / 发布脚本）

- `scripts/docker-build.bat`：构建 `bruce1977/llm-hub:latest` 与 `:<版本号>` 两个标签，版本号取自 `app/main.py` 的 `__version__`（可用参数覆盖：`docker-build.bat [镜像名] [版本号]`）；Docker 不可用时以错误码 1 直接退出
- `scripts/docker-publish.bat`：先构建，成功后才依次推送 `latest` 与 `:<版本号>`；推送失败立即停止并提示 `docker login`。两个脚本都不碰 git：提交与打 tag 请在发布前自行完成
- `docs/DEPLOY.md`：新增「一键构建 / 发布脚本（Windows）」小节

### 变更（仓库结构）

- `API.md`、`DEPLOY.md`、`workflow.md`、`Ranker.md` 与 `tests/TESTCASES.md` 移入 `docs/`（用 `git mv` 保留历史）；`README*`、`CHANGELOG*`、`AGENTS.md` 保持在根目录，相关交叉链接同步更新
- `.example.env` 更名为约定的模板名 `.env.example`；README / README_CN / `docs/DEPLOY.md` / `docs/workflow.md` / `docs/TESTCASES.md` / AGENTS.md 中的拷贝命令全部改用新名字
- 删除两个孤立根文件：`opencode.jsonc`（它指向的 `.opencode/skills` 已在 9a8bfea 删除，如今指向空目录）与 `Modelfile.reranker`（重排器的权威 Modelfile 在仓库外，由 `docs/Ranker.md` 记录）；`data/example.config.json` 里的交叉引用改指向该文档

### 变更（System One 后端）

- **破坏性（部署）**：`LAYA_SERVER_API_KEY` 更名为 `SYSTEMONE_SERVER_API_KEY`——该端点不再只服务 laya-server。请在 `.env` / 容器环境里同步改名；`systemone.api_key_env` 仍可指向任意变量名
- Ollama 0.40+ 原生提供 `POST /v1/systemone`，`systemone.backend` 因此可以直接指向任意 Ollama 端口（如 `http://llm.home:11435`）；网关侧无需改代码，转发仍是纯透传
- 已确认 `systemone.model` **会随请求体发送到后端**：仅当它是具体值且调用方未自带 `model` 时注入，`"local"` 仍是「不注入」哨兵。代码默认值保持 `auto`（laya-server 合法）；`data/config.json` 与 `data/example.config.json` 改为指向 Ollama 并配 `model: "tev1"`，因为 Ollama 对 `auto` 返回 **404**、缺 `model` 返回 **400**
- `app/systemone.py` 的日志与报错文案改为后端中立（「System One backend」），缺环境变量的告警也说明「只有后端要求密钥时才有影响」
- 文档：`docs/API.md` 与 README_CN 新增 laya-server / Ollama 契约对比（实测差异：`model` 取值、Ollama 的 `choice` 必须用非空 `criteria` **object**（数组 → 400）而 `score` 必须是 **2–10 项数组**、未知顶层字段 laya 拒绝 / Ollama 忽略、`routing` 仅 laya 返回），并给出两家都合法的请求示例与 Ollama 实测响应

### 修复

- `/v1/models`、`/api/tags` 与 `/probe` 的模型发现现在会带上 `upstreams[].headers`：此前只有聊天转发（`proxy.py`）和联网检索（`websearch.py`）应用了这些头，需要鉴权的上游（如带密钥的 `opencode`）在发现接口上收到 **401**，其模型因此不出现在聚合列表里——表现为网关只有 `auto`、但聊天和检索都正常
- 测试：`test_gateway_new.py` +4（共 59），用假客户端断言发现请求命中 `v1/models` 且携带 `Authorization`

---

## [1.2.0] - 2026-09-30

### 安全

- 新增 HTTP 方法允许列表（`security.allowed_http_methods`，默认 GET/POST → 其他返回 405）
- 新增正则路径白名单（`security.path_allowlist`，未匹配路径返回 403）
- 新增内存令牌桶限流器（`security.rate_limit`，超量返回 429）
- 所有响应默认携带安全加固响应头（nosniff、X-Frame-Options、CSP 等）
- README 和 README_CN 补充加固配置说明

### 新增（文档 / Swagger）

- Swagger `/docs`：每个端点都注入 `X-API-Key` **请求头参数**，因此**每个 API 的请求区（Parameters）里都有 API Key 输入框**——点 **Try it out** 填写、**Execute** 时随该次请求发送；顶部 **Authorize**（`BearerAuth` / `ApiKeyAuth`，`persistAuthorization` 持久化）仍可用于一次填写、全部请求共用
- `/docs` 改用本地内置的 `favicon.svg`（替代外部 CDN 图标，离线/内网部署同样可用）

### 变更

- `GET /` 改为直接返回系统信息（`{"name": "LLM Hub", "version": "1.2.0"}`），健康探测统一使用 `/health`（返回 `{"ok": true}`）
- Dockerfile 健康检查间隔从 30 秒改为 1 小时（被动网关无需频繁探测）

### 修复

- `/docs` 打开是一片空白：统一的 `default-src 'none'` CSP 拦截了 Swagger UI 的样式表、bundle 与内联启动脚本。现在 `/docs`、`/openapi.json` 使用收窄后的 CSP（`script-src`/`style-src 'self' 'unsafe-inline'`），其余响应仍保持 `default-src 'none'`
- 文档与代码同步：API.md 中 `/probe` 更正为 **仅 GET**（其它方法会落到统一转发 catch-all 被代理到上游），目录补全缺失的 6 个小节；`/`、`/health`、`/v1/models`、`/api/tags`、`/api/ps`、`/probe` 及各错误文案的响应结构已对真实处理器逐项复核

### 移除

- 移除 `/healthz` 健康检查别名（请使用 `/health`）
- 移除无任何调用的死代码：`ConfigManager.snapshot()`、`rerank.softmax_pair()`（`Config._expand_upstream_aliases`、`RerankRequest._coerce_aliases` 等 Pydantic 校验器由框架调用，保留）

### 新增（Rerank）

- 新增 `keep_alive` 配置，管理 Ollama 重排器的 VRAM 占用（0 = 打完即卸，"5m" = 保持 5 分钟，-1 = 服务端默认）
- 新增 `RateLimitConfig` 模型，含 `enabled`、`per_minute`、`burst`、`by`（ip/key）

### 新增（System One / laya-server 代理）

- 新增 `POST /v1/systemone` 端点：将请求原样转发到 `config.json` 的 `systemone.backend`（laya-server），网关用自身 API Key 校验调用方，再以 `systemone.api_key_env`（默认 `LAYA_SERVER_API_KEY`）环境变量密钥对后端签名，请求/响应体透传
- 新增 `SystemOneConfig`：`enabled`、`backend`、`model`（默认 `local` 哨兵值，不注入；改为 `auto`/`multilingual` 才强制覆盖）、`api_key_env`、`path`、`timeout`
- `model` 为 `local` 时不向下游注入模型（laya-server 默认 `auto`），避免非法模型值触发 422

### 新增（模型/进程聚合）

- `GET /v1/models` 与 `GET /api/tags` 现在实时拉取每个上游的真实模型列表并与声明模型、别名合并
- 新增 `GET /api/ps`：聚合所有 Ollama 上游的 `/api/ps`（运行中模型），逐条标注来源 `upstream`，单上游失败不影响整体（错误汇总到 `errors`）

### 文档

- API.md：新增 System One、`/api/ps` 端点说明；`/v1/models` 标注实时聚合
- README_CN.md：新增 System One 集成说明
- 新增 `tests/test_gateway_new.py`（33 项，覆盖聚合、systemone 鉴权/转发/哨兵、/docs 开关）
- 默认 rerank `options.num_ctx` 设为 8192，`keep_alive` 设为 "3m"
- 新增 `Modelfile.reranker` 低显存重排器变体（8192 ctx ≈ 4 GB，原 40960 ctx ≈ 9.3 GB）
- 新增 `Ranker.md` 重排器配置完整指南
- 新增 `tests/verify_security.py` 安全验证测试套件（15 项）

### 文档

- AGENTS.md：补充新安全功能描述
- AGENTS.md：项目结构树补齐 `systemone.py`、内置 `static/swagger/`、四套测试与 `requirements.txt`；类型注解约定更正为 PEP 604 联合类型（`app/` 中 `Optional[...]` 实际 0 处使用）
- `.env.example` / README / README_CN / API.md / TESTCASES.md：示例 API Key 统一替换为明显可辨的占位值（原 `sk-gateway-…` + 十六进制的形式会命中密钥扫描）
- README.md / README_CN.md：新增安全加固配置表和容器健康检查说明

---

## [1.1.0] - 2026-09-09

### 变更

- Rerank API：重构支持 logprobs/embedding 评分模式与降级链
- 新增 `upstream_api`、`raw_prompt`、`disable_thinking` rerank 配置项
- Rerank 请求结构更新（logprobs/top_logprobs 作为 Ollama 顶层字段）

### 新增

- Rerank 降级链：`/api/generate` → `/api/chat` → `/v1/chat/completions` → 文本兜底
- 按模型覆盖 rerank 模式（`model_modes`）
- Rerank 冒烟测试扩展，覆盖 logprobs 路由场景（共 87 项测试）

### 文档

- 同步更新 API.md、DEPLOY.md、README.md、README_CN.md

---

## [1.0.1] - 2026-09-08

### 新增

- `GATEWAY_AUTH_DISABLED` 环境变量，可跳过 API 密钥认证（`true`/`1`/`yes`）
- 对应冒烟测试用例

---

## [1.0.0] - 2026-09-07

### 新增

- LLM Hub 首次发布 —— 统一 Ollama / LM Studio 网关

#### 核心功能

- 多上游按模型名路由，支持优先级选择
- API 密钥认证（Bearer / X-API-Key），恒定时间比较
- Rerank API 实现（`/v1/rerank`）
- 模型别名，自动注入参数（如 `think: false`）
- 流式透传（Ollama 用 NDJSON，OpenAI 兼容用 SSE）
- 配置热重载（文件 mtime 检测）
- `/health` 轻量健康端点（无资源消耗）
- `/probe` 端点，探测上游实例和模型可用性

#### 安全

- 管理端点默认阻止（`api/delete`、`api/pull` 等）
- 客户端 IP 白名单（支持 CIDR）
- 请求体大小限制（防 DoS）
- 启动时弱密钥检测（拒绝占位密钥启动）
- CORS 配置

#### 部署

- Docker 支持，卷挂载
- Docker Compose 配置
- Docker Hub 镜像（`bruce1977/llm-hub:latest`）
- 本地 Python 开发支持（Linux / macOS / Windows）

#### 测试

- 57 项模拟测试，覆盖认证、路由、别名、流式、rerank、安全
- 支持真实 Ollama 实例的 E2E 测试

#### 依赖

- Python 3.12+
- FastAPI、httpx、Pydantic
