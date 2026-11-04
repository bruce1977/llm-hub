# 更新日志

本文件记录项目的所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [未发布]

### 变更（文档：README 精简为落地页 + 新增 USAGE 手册与界面截图）

- **README / README_CN 重写为落地页**：一句话定位 + 客户端/网关/后端示意图 + 6 条核心能力 + 6 张真实截图 + 三步快速启动 + 最小可跑的 `curl` 示例 + 文档索引，篇幅从 1000 行级压到 160 行内，进仓库第一眼就能看清「这是干什么的」
- **新增 `docs/USAGE.md` / `docs/USAGE_CN.md`**：原 README 的全部正文（特性表、目录结构、三种部署方式、配置详解、集成 LM Studio、管理台实现、完整接口与错误码、第二个 Ollama 实例、常见问题、公网安全清单、本地开发）整体迁入，带目录，链接回 README。手册放在 `docs/` 下，仓库根目录只留落地页、更新日志与 `AGENTS.md`；README、`AGENTS.md`、本文件的引用与手册内部的相对链接（`../README.md`、`API.md`）一并改好
- **新增 `docs/screenshots/{en,zh}/`**：每语言 10 张、共 20 张实拍截图（登录门禁、总览、上游、模型 LLM / Rerank / System One、请求与详情、联网检索、设置），来自构建产物 `app/web` 连真实网关的渲染，不是设计稿
- **新增 `docs/web-management/capture-live.js`**：Playwright 截图脚本（`LLMHUB_BASE` + `ADMIN_PASSWORD` 环境变量，`deviceScaleFactor: 2`，JPEG q88），按 `llmhub-console-locale` 逐语言抓取，可随时重跑
- `AGENTS.md` 目录树与文档分工同步（README 落地页 / `docs/USAGE.md` 手册 / `docs/screenshots` 截图）

### 变更（控制台：侧栏与页面标题接入国际化）

- 新增 `nav` 命名空间（侧栏菜单与页面标题共用一套键：`dashboard` / `upstreams` / `requests` / `llm` / `embed` / `rerank` / `systemone` / `webSearch` / `modelsLlm` … / `settings` / `about`），侧栏 8 个菜单项、9 个页面的 `TopBar` 标题、页脚「只读」提示与账户菜单里的 Settings / About 全部改为 `t('nav.*')`
- 账户卡角色文案 `Administrator` 改为 `t('shell.role')`（中文显示「管理员」）；中文侧栏现在是「总览 / 上游 / 请求 / 模型 / 工具 / 联网检索」

### 修复（控制台：模型清单列宽）

- Models · LLM 表格改为 `table-layout: fixed` 并加 `overflow: hidden; text-overflow: ellipsis`，`显存 / 占用 (GiB)` 列由 124px 放宽到 152px：此前 16 GiB 级双值（`16.1 GiB / 16.1 GiB`）在默认 auto 布局下溢出单元格，被右侧「运行态」徽标压住

### 变更（Dockerfile / 构建上下文）

- `Dockerfile`：新增 `ARG VERSION` 与 `org.opencontainers.image.*` 标签（版本、描述、标题），`scripts/docker-build.bat` 通过 `--build-arg VERSION=%VERSION%` 传入 `app/main.py` 的 `__version__`；健康检查间隔由 1h 收紧到 30s（timeout 5s），并注明 `/data` 同时承载 `config.yaml` 与 `llmhub.db`（请求明细 + 测速历史），以及容器为何保持 root（bind mount 的属主问题）
- `.dockerignore` 补齐：排除 `web/`（含 `node_modules`，约 230 MB）、`data/`（配置与 SQLite，改为卷挂载）、`docs/`、`tests/`、`scripts/`、`*.md` 与各类缓存——构建上下文从数百 MB 降到几 MB，且不会再误把本地配置打进上下文
- 功能上无需改 Dockerfile：`bench.py` / `bench_store.py` 已被 `COPY app/*.py` 覆盖，依赖仍只有 `requirements.txt` 中的五个包（SQLite 走标准库）

### 变更（auth：隐式 `.keys`、受信任网络启动）

- **不再需要环境变量就能读 `.keys`**：未设置 `GATEWAY_API_KEYS_FILE` 时，`config.collect_env_api_keys()` 回落到 `config.default_keys_file()`——`CONFIG_PATH` 同目录下的 `.keys`（配置放 `/config` 时即 `/config/.keys`，默认镜像布局是 `/data/.keys`）。显式设置的 `GATEWAY_API_KEYS_FILE` 仍优先，`GATEWAY_API_KEYS` 继续合并
- **受信任网络启动**：原先「没有任何 API Key 就拒绝启动」的自检，现在只在 `auth.allow_access_ip_whitelist` 为空时触发。白名单非空时允许无 Key 启动并打印警告——因为这些地址本来就免 Key 检查，于是「只用 IP 白名单（内网客户端 + /docs，不配 Key）」的部署才成立
- **拼写保护**：`allow_access_ip_whilelist` / `allow_ip_whitelist` 会被纠正为 `allow_access_ip_whitelist` 并打印警告。pydantic 默认忽略未知字段，拼错时白名单会**静默为空**（`/docs` 404、也不免 Key）
- `auth.api_keys` 不再出现在 `config.yaml`（此前已移除）：密钥只存在于运行时，旧配置里残留的块会被丢弃并告警
- `tests/smoke_test.py` 新增两项断言（隐式密钥文件位置、拼写纠正）——共 **135** 项
- `README` / `README_CN` / `docs/DEPLOY.md` / `.env.example` / `AGENTS.md` 同步

### 新增（控制台：简体中文 / 英文国际化）

- 管理台支持**简体中文（默认）**与**英文**，切换入口在左下角账户菜单「语言 / Language」，选择持久化到 `localStorage`（`llmhub-console-locale`）。首次访问按 `navigator.language` 猜（`en*` → 英文，其余 → 中文），切换时同步更新 `<html lang>` 与 Element Plus 自身的 locale（`el-config-provider`），日期选择器 / 分页 / 空态文案都会跟着变
- 实现为 `vue-i18n@11` 的 Composition 模式（`legacy: false`）：文案在 `web/src/i18n/messages/zh-CN.ts` 与 `en.ts`，按页面分命名空间（`dashboard`、`requests`、`modelsLlm` 等）。`en.ts` 用 `const en: MessageSchema = …` 绑定中文导出的类型，键少一个或多一个都会让 `vue-tsc` 直接报错，而不是静默回落到中文。共 447 个 key；变量统一走具名插值（`{n}`），段落里的行内标签用 `<i18n-t>` 插槽保留 `<code>` / `<b>`
- 所有用户可见文案均已迁移（各页面、TopBar、请求详情抽屉、API 文档、空态、tooltip、`aria-label`、Element Plus 的表格与分页文案）；`utils/format.ts` 与 `api/client.ts` 属于普通模块，直接调用 `i18n.global.t()`。产品名、接口路径、字段名、单位与代码示例保持不译

### 变更（控制台：请求详情正文面板）

- 请求详情里的 请求体 / 响应体 改为**软换行 + 固定 320px 高度 + 纵向滚动**，不再向右溢出。`JsonPane` 新增 `height` / `wrap` 两个 prop（`.code2.wrap` → `white-space: pre-wrap`、`overflow-x: hidden`、`scrollbar-gutter: stable`），并且能解析成 JSON 的正文会先缩进成 2 空格多行——库里存的单行正文即使宽度够也很难读
- 被截断的 8KB 或非 JSON 正文仍按原样显示；`ApiDoc` 的示例保持原来的 `max-height` + 不换行

### 新增（控制台：模型测速）

- Models · LLM 的「上游模型映射」每行新增 `测速` 按钮：点击后 `POST /v1/management/bench`，弹出模态框并每秒轮询 `/bench/{job_id}`，过程中显示「测速中 · 第 x/y 轮」与进度条，结束后展示 Decode / Prefill / TTFT 三张统计卡（均值、±标准差、最小/最大、变异系数）与逐轮明细表（轮次 / 类型 / Decode / Prefill / TTFT / 生成 tokens）。失败时原样展示后端 `error` 并可重新测速；同一时刻只允许一个任务，关闭弹窗即停止轮询
- 鼠标悬停任意模型行（250 ms 防抖）浮出小卡片，展示该 `upstream` + `model` **最近 3 份**记录（时间、计时口径徽标、Decode / Prefill / TTFT），同一行结果在会话内缓存；无记录时提示「暂无测速记录」
- 新增 `app/bench.py`：`bench_llm.mjs` 的 Python 重写（不依赖 Node）——1 轮预热 + N 轮流式请求，Decode/Prefill 优先采信服务端计时（`eval_duration`、`predicted_ms`、`prompt_eval_duration`），缺失时回落客户端墙钟，口径记在 `timing_source`（`server` / `client` / `client*`）；思考模型自动加 `think: false`；测速前 preflight 对齐模型名（精确 → 归一化 → 子串）；内部 `httpx.AsyncClient` 使用 `trust_env=False`（与网关主客户端一致），否则系统 `HTTP_PROXY` 会把打上游的请求绕到代理上，测速直接 `502`
- 测速任务改为在**独立线程 + 自己的事件循环**（`asyncio.run`）里运行，而不是挂在 `POST /bench` 请求的 loop 上：请求作用域一结束，`create_task` 出来的任务会被取消，job 就永远停在 `running`。结果行**先入队再置 `done`**，`/bench/history` 读之前先 `await BenchStore.flush()`，因此「刚看到完成就去查历史」不会读到空表；`BenchStore.record()` 用 `call_soon_threadsafe` 把行交给应用主循环
- 新增 `app/bench_store.py`：`llmhub.db` 里的 `bench_results` 表（与 `requests` 共用 WAL 队列写入），每个 `(upstream, model)` 只保留**最新 3 份**，逐轮明细整体存为 JSON
- `GET /v1/management/bench/history` 注册在 `GET /v1/management/bench/{job_id}` **之前**：反过来的话 FastAPI 会把字面量 `history` 当成 job_id，每次悬停都 404「任务不存在或已过期」
- Models · LLM 表格列宽：模型名自适应吃剩余宽度，其余列给定宽度提示（上游 96 / 类型 88 / num_ctx 78 / 设备 104 / 显存·占用 124 / 运行态 96 / 操作 84 px）并 `nowrap`，`● 运行中` 不再折成两行；`.badge` 全局加 `nowrap`
- `docs/API.md` 补充三个接口、计时口径与 `bench_results` 表结构；控制台实现文档 §2.3 与 `app/web` 同步

### 新增（控制台：关于页面）

- 新增 **关于 / About** 页面（`/admin/about`，不占侧栏位，入口在账户菜单「设置」旁）：项目简介、两列「主要功能」网格（多上游路由、别名与参数注入、嵌入/重排、System One、Web 搜索、安全与配额、请求明细与统计、只读管理台、热重载与流式透传）、版本信息与相关链接
- 版本信息为**动态**内容：`gateway_version` 取自 `/v1/management/overview`（未进过 Dashboard 时也会顺带填上侧栏版本号），同卡显示上游在线数、运行中模型、今日请求 / token、请求明细库状态与数据时间。版本前的空心圆点在探测进行中呼吸闪烁、拿到结果后填充实心；`↻` 重新探测
- 附上源码仓库（https://github.com/bruce1977/llm-hub）、Docker 镜像与 `/docs` 链接，以及微信公众号二维码：图片放在 `web/public/wechat-qr.jpg`，构建后经 `/admin/wechat-qr.jpg` 同源提供（`src` 由 `import.meta.env.BASE_URL` 拼接，图片缺失时显示补齐说明而不是破图）
- `docs/web-management/web-management-implementation.md` 新增 §2.10，`AGENTS.md` 记录该页与菜单入口，`dashboard-mockup.html` 同步；`app/web` 已重新构建

### 新增（控制台：模块功能介绍）

- Models · Embed、Models · Rerank、Models · System One 与 Tools · Web Search 都在模型清单 Tab 的最前面新增 **功能介绍** 卡片：约 200 字说明该模块的用途（语义检索 / 聚类 / 去重 / RAG、召回后精排、低延迟判定、外部实时知识）以及网关承担的部分。四页共用 `.feature-intro`（`web/src/styles/theme.css`），长段落与更密的 `api-note` 区分开、更易读
- `docs/web-management/web-management-implementation.md`（§2.3b / 2.4 / 2.5 / 2.9）与 `AGENTS.md` 记录该章节；`dashboard-mockup.html` 四页同步该卡片；`app/web` 已重新构建

### 变更（控制台：Web Search 分栏与可用模型）

- Tools · Web Search 与 Models · Rerank 对齐：拆成 **模型清单**（配置信息卡 + 可用模型）与 **API 用法**（`ApiDoc`）两个 Tab，切换不发请求；一次 `load()` 并发拉取 `/tools/websearch` 与 `/models/llm`
- 新增 **可用模型** 卡片，列出 Provider 上游（`websearch.upstream`）的模型：行取自 `/models/llm` 中该上游的行，因此别名显示为 `display`、通配 `opencode/*` 展开来的行保留 `wildcard` 角标，托管 provider 探测有效时显示 `● Cloud` / `● 在线`。卡片说明检索能力来自后台配置的 **opencode-port**（[github.com/bruce1977/opencode-port](https://github.com/bruce1977/opencode-port)）：需要一个 `type: "opencode"` 的 upstream 指向它（默认 `127.0.0.1:10000`），并让 `websearch.upstream` 指向该 upstream；请求的 `model` 可以不填
- `model` 参数标注为 opencode-port 支持的模型名（不是 llm-hub 配置的模型）且可省略：省略时由 opencode-port 使用它自己的默认模型。`docs/API.md`、控制台设计文档、AGENTS.md 与 `dashboard-mockup.html` 同步同一套措辞
- `app/web` 已重新构建

### 变更（控制台：侧栏细节与固定账户菜单）

- 账户卡改为**固定在面板左下角**（`.user-dock`，`position: fixed`，对齐居中的 1180px 壳层），不再随侧栏内容滚走，Requests 这类长页面也不会把它顶出视野；`.app` 保持 `min-height: max(640px, calc(100vh - 48px))`，短页面下账户卡仍落在面板内部而不是悬在面板下方。头像改为渐变圆形加双层描边，首字母更醒目
- 点击账户卡弹出账户菜单：**语言 / Language**（右侧**子菜单**，不撑开当前菜单；`中文` 可选，`English` 标 `soon` 占位，等界面翻译完成再启用）、**深色模式开关**（现在是唯一的主题入口，侧栏与折叠态浮动按钮均已移除）、**设置**（`/admin/settings`，侧栏该项已删除）与**退出登录**。点击外部区域或按 `Esc` 关闭；语言子菜单在指针离开后延迟 150ms 关闭，便于跨过两者之间的间隙
- Rerank 图标改为 `⇅`、Web Search 改为 `◎`，与侧栏其余字形统一；导航为 Dashboard / Upstreams / Requests / Models(LLM · Embed · Rerank · System one) / Tools(Web Search)
- `docs/web-management/dashboard-mockup.html` 同步新的账户卡、菜单、子菜单、导航顺序与图标，并补上 `#view-models-embed` 区块让 Embed 链接可跳转；`app/web` 已重新构建

### 变更（`auth` 配置：密钥只来自环境，新增 IP 白名单）

- **移除 `auth.api_keys`**：密钥不再写进 `config.yaml`，只从 `GATEWAY_API_KEYS` 与 `GATEWAY_API_KEYS_FILE`（如 `data/.keys`）读取并合并去重。`ConfigManager` 加载后把结果放进模块级运行时存储，`cfg.auth.api_keys` 仍可只读访问，因此 `auth.py`、`admin_auth.py`、`main.py` 与启动脚本无需改调用点。旧配置里残留的 `api_keys` 由 `model_validator` 去掉并打印一条忽略警告；`merge_env_api_keys()` 随之删除
- **新增 `auth.allow_access_ip_whitelist`**：字符串数组，按 `re.fullmatch` 匹配 ASGI 客户端地址（直连即 **TCP 对端**，`request.client.host`）。命中者可以打开 `/docs` 与 `/openapi.json`，调用网关 API 时也免 API Key 检查。**网关代码本身不读取 `X-Forwarded-For`**——它可被调用方伪造；不过 uvicorn 的 `ProxyHeadersMiddleware` 在受信代理（`--forwarded-allow-ips`，默认仅 `127.0.0.1`）转发时会先用转发头改写 `request.client`，所以本机反代部署下命中判定看到的是真实来源（已实测：本机请求带 `X-Forwarded-For: 8.8.8.8` 时 `/docs` 变 404）。非法正则只记警告、不放开任何请求（fail closed）
- **移除 `security.allow_docs`**：`/docs` 的可见性改由 `auth.allow_access_ip_whitelist` 决定，未命中的地址一律 **404**（与旧的「关闭即 404」状态码一致）；`auth.py` 新增 `peer_ip()` / `ip_pattern_allowed()` / `docs_allowed()`，中间件的文档门控改调 `docs_allowed()`
- 测试：`smoke_test.py` 新增 `[16b]`（空白名单 → `/docs` 404 且 API 401；`^testclient$` → `/docs`、`/openapi.json` 与无 Key 的 `/v1/models` 均 200；伪造 `X-Forwarded-For` 不给任何放行；坏正则 fail closed），并断言模板不含 `api_keys`、`SecurityConfig` 不再有 `allow_docs`——共 **133** 项；`test_gateway_new.py` 的文档门控改切 `auth.allow_access_ip_whitelist`
- 文档同步：README / README_CN（`auth` 段、安全表、公网 checklist、`/docs` 说明）、`docs/API.md`、`docs/TESTCASES.md`、`docs/web-management/web-management-implementation.md`、`docs/workflow.md`、AGENTS.md 与 `data/config.example.yaml` / `data/config.example.json` / `.env.example`

### 新增（以配置为准的模型清单、别名显示与今日 token 合计）

- 控制台展示的模型**名字**改为取自 `config.yaml`（`upstreams[].models`），不再取自实例自己上报的清单：装了却没声明的模型本来就路由不到，也就不该出现在表里。`GET /v1/management/models/llm` 每个上游按声明名逐行返回
- 前缀通配是唯一需要问上游的声明：`qwen3:*`、`opencode/*` 会用 `main.fetch_upstream_models()`——网关路由用的同一份 60s 缓存；因为 `main` 已在模块级导入 `management`，该函数在处理函数内部延迟导入——展开成命中的具体模型，这些行带 `wildcard: true`。只有真的声明了通配的上游才会被调用；展开不到任何模型时（含上游离线）通配本身仍保留成一行且 `running: false`，既不静默消失，也不谎称一个模式串在线
- 每行都带 `model`（真实上游 id）、`display`（配了别名时是别名，否则就是声明名）与 `alias`（未配别名时为 `null`），来源是 `_alias_by_target()`——把 `Config.aliases` 反转成 `(上游, 真实 id) → 别名`。Dashboard 的「运行中模型」、Upstreams 展开面板与 Models · LLM 都以别名显示并加 `alias` 角标，鼠标悬停给出真实 id，因为别名才是调用方实际发送的名字
- `opencode`（托管）上游没有 `/api/ps`：只要它的健康探测有效，它声明的**每一个**模型都算可用——`device` 为 `"cloud"`、`device_ratio` / `size_gib` / `vram_gib` 为 `null`，控制台显示 `Cloud` 与 `-`——并且这些行会进入 `GET /v1/management/overview` 的 `running_models` 与 KPI。`_hosted_models()` 同时供 `/overview` 和 `upstreams/{name}` 使用，两个视图不会互相矛盾；provider 离线时一行都不加
- `GET /v1/management/upstreams/{name}` 返回声明名（别名优先、通配已展开）与 `running_models`（Ollama 取 `/api/ps`，托管上游则列它声明的模型）。此前的 `models_endpoint` 一并删除：清单不再来自实例，再标一个发现端点反而误导
- `GET /v1/management/overview` 的 KPI 新增 **`tokens_today`**：今天（UTC）已记录请求的 token 合计，优先取 `tokens`、缺失时回退 `tokens_in + tokens_out`，由 `request_log._today_summary_sync` 里新增的聚合算出。上游没回报用量的行按 0 计
- 测试：`tests/test_management.py` 覆盖「行只来自配置」、每行的 `display` / `alias`（配别名的显示别名、没配的保留 id）、通配被替换成命中的具体模型且各自带上运行态、展开不到的模式保留且不算运行、托管上游（探测有效即声明模型全部 running 且 `device: "cloud"`、内存为 `null`、进入 Dashboard 列表与 KPI；离线不加行）、`/api/ps` 的设备与内存列、死掉的上游不贡献运行态行、`running_models` 与 `tokens_today`，并检查构建后的壳层声明了 `/admin/favicon.svg`、静态路由以 `image/svg+xml` 提供它而不是回退到 SPA 壳——共 **198** 项

### 变更（控制台：导航高亮、刷新可见性、图表、筛选）

- 运行时配置文件改为 **`config.yaml`**（`data/config.yaml`，模板 `data/config.example.yaml`），由 PyYAML 解析——`requirements.txt` 新增该依赖。YAML 1.2 是 JSON 的超集，因此旧的 `config.json` 内容照样能读：`ConfigManager._active_path()` 会回退到同目录的 `config.json` 并打一条警告，`CONFIG_PATH` 无论扩展名都按显式路径生效，`scripts/start-local.ps1` 复刻了同样的回退（并把 `yaml` 加进依赖预检）。文档、示例、`.env.example`、Dockerfile 与启动脚本统一改名
- 侧栏高亮改用 `.nav-item.router-link-exact-active`，不再用 `router-link-active`。Dashboard 挂在 `/`，是所有路由的祖先，旧选择器会让它在每一页都亮着（浏览器里已复现：在 `/admin/upstreams` 时 Dashboard 仍带 active 类）
- Dashboard 与 Upstreams 的自动刷新改为**每 60 秒一次**（`POLL_MS = 60000`），不再是 5 秒 / 10 秒：每轮都要并发探测所有上游、每个 5s 预算，旧频率几乎是在连续占住网关探测
- 刷新过程可见。`TopBar` 接收 `loading` prop，为真时 `↻` 持续旋转并禁用、栏底走一条不确定状态的 2px 进度条、副标题换成带小号 spinner 的「刷新中…」、右侧「最后更新」文字变灰；Dashboard 还会让 KPI 卡降透明度、给四张图表蒙上薄纱，首帧数据未到时显示骨架屏。每个视图持有一个 `refreshing` ref（重入直接返回，`finally` 复位）；`TopBar` 原来那个 600ms 的自计时假旋转已删除——它会在上游探测还在跑的时候就宣称刷新完成。Rerank / System One / Web Search / Requests 一并接上
- 「今日」图块改为 `今日请求 / Token 消耗`：请求数旁边是 token 合计，由新增的 `fmtKiloTokens()` 以 `k` 为单位、保留两位小数格式化（40 → `0.04k`）
- 三张分布图最终是**实心饼图**（中途试过圆环，按要求改回）：饼上不画任何标签（长模型 id 原本压住图形），名称与计数收在**图形下方一行一条**的可滚动图例里（9.5px 字号、10.5px 行高、超 16 字符截断），圆盘上移，图例行永不压到扇区
- Models · LLM 表格变为 模型名 / 上游 / 类型 / num_ctx / 设备 / 显存 · 占用 (GiB) / 运行态——去掉 `别名` 列，由 `wildcard` 角标说明这一行是通配展开来的；`别名映射` 表格只保留 别名 / 目标模型 / 上游 三列
- Models · LLM 页拆成两个 Tab：**模型清单**（上面那三张表）与 **API 用法**。后者沿用 Rerank 页的 `ApiDoc` 版式，说明当前支持的两个生成端点——`POST /api/generate`（参数表与 `app/main.py` 里那份 `openapi_extra` schema 一一对应：model / prompt / system / template / raw / context / images / format / stream / keep_alive / logprobs / top_logprobs / options）与 `POST /v1/chat/completions`（model / messages / temperature / max_tokens / stream / tools），各带请求与响应示例。两处都写明**缺省即流式**（不写 `stream` 时分别走 NDJSON 与 SSE）、`model` 先过别名解析、`options.num_ctx` 仍被 `num_ctx_overrides` 裁剪、非 POST 一律 405。Tab 切换不发请求，`↻` 刷新仍只走 `/v1/management/models/llm`
- 设备角标风格统一：`.badge.cpu` 去掉 1px 边框和加粗字重，与 `gpu` / `part` 一致——旧样式让 CPU 那一行比其他行高 2px；`dashboard-mockup.html` 同步改掉。徽标只显示设备类型（**GPU / 部分 / CPU / Cloud**），不再带占用百分比
- 托管（`opencode`）模型现在标注设备 **Cloud**：后端把托管行的 `device` 从 `null` 改为 `"cloud"`（`_hosted_models()` 与 `_llm_row()` 同步），前端新增紫罗兰色 `.badge.cloud`，Dashboard、Upstreams 与 Models · LLM 三处一致显示 `● Cloud`，显存/占用仍是 `-`
- 侧栏把 **Requests** 移到 **Upstreams** 正下方；新增 **Models · Embed**（位于 Rerank 上方）：沿用 Rerank 页版式（信息卡 + 端点对照 + `ApiDoc`），说明 `POST /api/embed`（Ollama 原生）与 `POST /v1/embeddings`（OpenAI 兼容）的字段与示例。页面不请求任何接口——网关没有 `embed` 配置段，两端都是原样透传
- Upstreams 行展开改为读取自身详情接口返回的 `running_models`，不再从 `/overview` 重新分组；每次展开都重新探测；左栏标题按类型标注 `/api/ps` 或 `在线（探测有效）`，右栏标注 `config.yaml · upstreams[].models`；没有声明任何模型的上游会直说，而不是一张空表格
- Requests 的**模型**下拉跟随**上游**收敛：选项是所选上游在配置里声明的名字（通配已展开），再加上别名——别名继承其目标模型所在的每个上游（`ModelChoice.upstreams: string[]`）；标签变成 `模型 · <上游>`；切换上游后原已选而新上游供不了的模型会被清空，而不是静默筛出空列表
- 新增 `web/public/favicon.svg`（蓝→紫渐变的圆角方块 + 汇聚节点标志），`web/index.html` 加 `<link rel="icon">`；Vite 把 `public/` 原样复制进 `app/web` 并把 href 改写成 `/admin/favicon.svg`，由已有的 `/admin/{asset_path}` 静态路由以 `image/svg+xml` 提供——无需新增路由，不依赖 CDN，且符合 CSP
- 重新构建 `app/web`，并在真实网关（真 Ollama + OpenCode 上游）上逐页复验：按别名解析的运行中模型列表（`qwen3.6:35b`、`opencode/auto` 以别名显示）、13 行 LLM 中 8 行算在线（其中 4 行是托管模型，设备显示 `Cloud`、显存 `-`）、`opencode/*` 展开成 provider 的真实 id、饼图图例、`/admin/upstreams` 的导航高亮、运行时设备与内存列以及按上游收敛的模型下拉。四个 mock 套件依旧全绿（133 / 63 / 198 / 15）
- 文档：`docs/API.md` 接口表、`docs/TESTCASES.md` 测试 35 / 测试 36（响应示例与断言）、`docs/web-management/web-management-implementation.md`（§2.1-2.3、§2.6、§3.2-3.3、§5）、AGENTS.md（管理 API、控制台、目录树与测试数量）以及 README / README_CN 的特性表、接口表与控制台段落——另含 README / README_CN、`docs/DEPLOY.md`、`docs/API.md` 里配置文件名与 YAML 的措辞
- 备注：控制台一度走过相反方向（发现结果与配置取并集、声明但缺失标 `installed: false`、用 `models_endpoint` 标明发现来源）。该实验已回退：显示的就是配置内容，只有在配置自己声明开放式前缀时才去调用上游模型接口

### 新增（请求日志的 `tokens_in` / `tokens_out` 两列）

- `requests` 表按**方向**记录 token 用量：`tokens_in` 存输入（提示）数，`tokens_out` 存输出（生成）数，`tokens` 继续保存总量。两种响应格式都能拆——OpenAI 兼容响应的 `usage.prompt_tokens` / `completion_tokens`，Ollama 原生响应的 `prompt_eval_count` / `eval_count`
- 总量仍然优先采用上游自己回报的 `usage.total_tokens`：后端把推理 token 计入总量时，它可能大于输入与输出之和
- `request_log.extract_tokens()` 改为 `extract_token_usage()`，返回 `(输入, 输出, 总量)` 三元组。上游没有回报用量时三列都是 `NULL`，与真实的 `0` 可区分；松散 provider 传来的数字字符串会被转换，布尔值不计入
- 旧库依旧在启动时原地升级，但 `_ADDED_COLUMNS` 现在把每个后加列与它的**声明类型**配对，因此 `tokens_in` / `tokens_out` 以 `INTEGER` 补上，而不是前三个迁移列使用的 `TEXT`
- `GET /v1/management/requests` 的行与 `GET /v1/management/requests/{id}` 详情都带上两个方向的计数，管理台详情抽屉改为「Token 输入 / 输出」并在旁边显示合计（`web/src/types/api.ts` 补了字段，拆分因此受编译期校验）
- 流式响应目前网关取不到用量，这一类行三列仍为空——本次改动没有改变这一点，也正是拆分只在非流式路径上采集的原因
- 测试：`tests/test_management.py` 新增 Ollama 拆分（5 + 7 = 12）、OpenAI 拆分（4 + 8 = 12）、无用量响应写入 `NULL`、列表行同样带拆分、旧库升级后新列声明为 `INTEGER`，共 **166** 项
- 文档：`docs/API.md`、`docs/TESTCASES.md`（测试 37 / 测试 39）、`docs/web-management/web-management-implementation.md`、AGENTS.md 与 README / README_CN 的字段表和示例全部同步

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

- 新增 `scripts/start-local.ps1`（外加 `scripts/start-local.bat`，一个用 `-ExecutionPolicy Bypass` 调用它的 cmd 包装）：定位 python（先 `python`，再 `py -3`，可用 `-Python` 指定）、读取 `.env`、找到 `config.yaml`，并在启动前**跑一遍应用自己的规则**，因此检查不会与实现走偏——`config.yaml` 由真实的 Pydantic 模型解析，密钥由 `auth.find_weak_keys` 筛查
- 启动前检查覆盖本地常见的失败原因：缺依赖（并提示用 `-Install`）、配置被校验器拒绝、开了 `auth.enabled` 却没有任何 Key、弱/占位 Key（只有 `security.fail_on_weak_keys` 判定会导致启动失败时才报错退出，否则只告警）、`ADMIN_PASSWORD` 未设置（管理台与 `/v1/management` 会返回 503）、`app/web/index.html` 缺失（需要重新构建产物）、端口被占用（打印占用者的 PID 与进程名）
- 启动方式按扁平模块的要求来：`--app-dir app`，且工作目录设为 `app/`，与镜像的 `WORKDIR /app` 一致，因此 `.env` 里的相对路径（`../data/config.yaml`、`../data/.keys`）在宿主机和容器里解析到同一份文件。原先文档里的 `python -m uvicorn app.main:app` 已从 AGENTS.md **删除**——它根本跑不起来，模块之间用裸名互相 import（`ModuleNotFoundError: No module named 'admin_auth'`）
- 配置解析优先级：`-Config`（相对调用者所在目录）> `.env` 里的 `CONFIG_PATH`（相对 `app/`，仅在文件存在时采用）> `<repo>\data\config.yaml`（不存在时从 `data\config.example.yaml` 复制并告警）。shell 中已导出的变量优先于 `.env`；`GATEWAY_API_KEYS_FILE` 读不到而内联 `GATEWAY_API_KEYS` 可用时直接丢弃该变量并说明原因；没装 `watchfiles` 时自动降级为不带 `--reload` 启动
- 参数：`-Port` / `-Bind` / `-LogLevel`（默认取配置里的 `server.*`）、`-EnvFile`、`-Python`、`-NoReload`、`-Install`、`-CheckOnly`（只检查不启动）。脚本只打印密钥的**名字**，从不打印值
- 文档：README / README_CN 新增「Launcher / 启动脚本」一节与参数表；`docs/DEPLOY.md` 在构建/发布脚本旁边记录本机启动脚本；AGENTS.md 在 `scripts/` 目录树中列出两个文件，替换掉失效的本机开发命令，并在常见问题里补一条

### 新增（Web 管理台前端）

- 新增 `web/`：Vue 3 + Vite + Element Plus + axios + ECharts 的管理台单页应用，源码在 `web/src`，`npm run build` 把产物写入 **`app/web`** 并随仓库提交，由 FastAPI 直接以静态文件服务在 **`/admin`**（产物与模块同级，本地 `app/web` 与镜像 `/app/web` 是同一个解析路径；`Dockerfile` 只 `COPY app/web /app/web`，镜像内不含 node 工具链；开发时 `cd web && npm install && npm run dev`，发布前 `npm run build`）
- 八个页面：Dashboard（KPI + 24h/7d 趋势 + 上游/模型/状态三张饼图）、Upstreams（列表展开详情、显存进度条、GPU/部分/纯 CPU 设备标记、不可达上游 503 提示）、Models·LLM（上游模型映射 + 别名 + `num_ctx_overrides`，只读投影自 `config.yaml`）、Models·Rerank、Models·System One、Tools·Web Search（含参数说明与请求/响应示例）、Requests（时间/上游/模型/状态/路径筛选 + 分页 + 详情抽屉展示截断后的请求/响应体）、Settings（占位）；图表全部用 ECharts 实现，颜色取自 CSS 变量，因此深浅色主题切换时图表同步换色
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
- 新增 `config.yaml` 配置段 `request_log`，其 `enabled`（默认 `true`）是入库总开关：`ProxyService._logging_on` **逐请求**判定，因此改完保存无需重启即可生效——配置 mtime 每秒最多比对一次，开关在约 1 秒内生效（已对本地 Ollama 实测：关闭期间零新增行，重新开启恰好一行）；关闭时不采集请求/响应体、也不产生任何行。`/v1/management/overview` 与 `/v1/management/requests` 会以 `request_log: {enabled, db, retention_days, dropped}` 回报状态，前端可据此提示「日志已停用」而不是显示空列表
- `proxy.py` 覆盖所有出口埋点：被拦截（403）、请求体过大（413）、无可用上游（404）、流式、非流式，以及连接/读取/HTTP 失败（502/504）；记录方法、路径、解析后的真实模型名、上游、状态码、耗时、TTFB、流式标记、token 用量（`usage.total_tokens` 或 Ollama 的 `prompt_eval_count + eval_count`）、请求/响应字节数、客户端 IP 与截断后的请求/响应体
- 请求/响应体落库前截断至 8 KB，`authorization` / `api_key` / `token` / `password` 等字段替换为 `***`；详情接口返回供前端抽屉展示
- `GET /v1/management/requests` 支持 `start` / `end` / `upstream` / `model` / `status`（2xx\|3xx\|4xx\|5xx）/ `path` / `page` / `page_size`，返回 `stats`（数量、P50/P95/P99、错误率）、`rows` 与 `pagination`；`stats/timeseries` 按小时/天分桶统计数量、错误数与 P95；`stats/distribution?group_by=upstream|model|status` 返回驱动总览页三张饼图的分组计数（`status` 只分 `ok`（<400）与 `error`（≥400），未命中上游的行计入 `-`，因此各项相加恒等于明细总数；其他 `group_by` 取值返回 **400**）；`stats/failures` 给出按状态码、按上游的失败分布及最近样本
- `UpstreamConfig.vram_total_gb`：可选，声明上游主机显存总量，供总览页「已用 / 总量」进度条使用（无法通过 API 探测）
- `/v1/management` 在 catch-all 之前注册，并与 `/admin` 一样不受 `security.path_allowlist` 限制；旧的 `/admin/login|logout|ping` 改为复用同一实现
- 测试：新增 `tests/test_management.py`（158 项，含上一节的 11 项控制台服务检查），使用本地 mock Ollama 覆盖鉴权（会话 Cookie 与网关 API Key 均可 **200**；把密码当作 `Bearer` / `X-API-Key` → **401**；未知 key → **401**；key 放查询串 → **401**；用户名错误 → **401**；用户名大小写与空格会被规范化；未配置 503）、探测与设备判定、掩码投影（含 `tools/websearch` 与 rerank 的 `upstream_api` / `on_error`）、流式/非流式/502 埋点、新增列（到达/完成时间、别名的 `requested_model` 与解析后 `model`、`query`）、筛选、分页、时序、分布（分桶约定、排序、总数与 `group_by` 校验）、`request_log.enabled` 关闭与重新开启，以及旧库 `ALTER TABLE` 升级
- 文档与配置：`docs/API.md` 新增「管理 API（Web 管理台）」章节，含 `requests` 字段清单、`request_log.enabled` 开关说明与 `stats/distribution` / `tools/websearch` 响应示例；README / README_CN 补充特性行、目录、`request_log` 配置段、`ADMIN_PASSWORD` / `REQUEST_LOG_*` 环境变量与 `vram_total_gb` 说明；`docs/TESTCASES.md` 新增测试 34-40；同步更新 `.env.example`、`data/config.example.yaml`（附带 `request_log`）、`.gitignore`（`data/*.db*`）、`docs/DEPLOY.md` 与 AGENTS.md

### 新增（联网检索）

- 新增端点 `POST /v1/tools/web-search`：由 OpenCode API Gateway 提供关键词网页检索，请求体与上游响应**原样透传**（`query`、`results[]`、拼接后的 `text`、`sources`，`synthesize: true` 时另有 `answer`）
- `POST /v1/tools/web-search` 在 provider 不可达时新增诊断日志：完整异常链 + 逐个地址的 TCP 探测结果（`dns(host) -> ip:port ConnectionRefusedError errno=111`、`TimeoutError`、`gaierror`），因为 httpx 会把它们统一收敛成 `All connection attempts failed`
- 新增 `websearch` 配置段：`enabled`、`upstream`、`path`（默认 `v1/web-search`）、`api_key_env`、`timeout`。`upstream` 按名字选检索方；留空则自动取第一个 `type: "opencode"` 的上游；填了不存在的名字返回 **503**，不会静默回退
- 新增上游类型 `opencode`：模型发现走 OpenAI 兼容的 `/v1/models`（解析 `data[].id`），存活探测走 `/health`。检索方作为普通上游登记后，其模型同时进入聚合的 `/v1/models`，聊天也可路由过去
- 可选的上游签名：`websearch.api_key_env` 指定环境变量名（默认为空 = 不签名，当前上游匿名即可访问）；`OPENCODE_GATEWAY_API_KEY` 已写入 `.env.example` 与 `docs/DEPLOY.md`
- 该端点在 `/docs` 中带完整的请求/响应 schema（`query` 必填，另有 `top_k`、`recency`、`synthesize` 等），仅接受 POST（否则 **405**），并沿用网关 API Key 鉴权（**401** / **403**）
- `data/config.yaml` 与 `data/config.example.yaml` 附带 `opencode` 上游（声明 `auto` 与 `opencode/*` 通配）及 `websearch` 段
- 文档：API.md 新增「Web Search（OpenCode 网关代理）」章节；README / README_CN 补特性行、`websearch` 配置详解、curl 示例、`opencode` 上游类型说明与斜杠通配注意点（`opencode/*` 而非 `opencode:*`）
- 测试：`smoke_test.py` +13（共 104）覆盖 `opencode` 类型与 `websearch` 配置解析；`test_gateway_new.py` +22（共 55）覆盖端点鉴权、转发、透传、签名与状态码映射及 OpenAPI schema；`test_api.py` +8（共 28）E2E 用例；docs/TESTCASES.md 新增测试 31-33（含实测响应）

### 新增（配置 / 上下文钳制）

- 新增顶层配置 `num_ctx_overrides`：`{ "<真实模型名>": <num_ctx 整数> }`，网关按 `resolved.target` 匹配后**强制覆盖** Ollama 上游请求体里的 `options.num_ctx`，忽略客户端传入值；仅对 `type=="ollama"` 上游生效（LM Studio / OpenAI 兼容用 `max_tokens`，不受影响）。目的：避免同一模型因客户端传入不同 `num_ctx` 被 Ollama 反复新建实例、显存叠加
- 实现：`config.py` 新增 `Config.num_ctx_overrides` 字段（Pydantic 校验 `dict[str,int]`）；`proxy.py` 在别名解析与参数注入之后、序列化之前执行钳制，覆盖时打印 `num_ctx clamped` 日志
- 文档补齐（此前仅落代码、未补配置说明）：`data/config.example.yaml` 加入示例；README / README_CN 的「配置详解」新增 `num_ctx_overrides` 专节（键为真实模型名、值为固定 ctx、仅 Ollama 生效、支持热重载）；本条目同步记录

### 新增（构建 / 发布脚本）

- `scripts/docker-build.bat`：构建 `bruce1977/llm-hub:latest` 与 `:<版本号>` 两个标签，版本号取自 `app/main.py` 的 `__version__`（可用参数覆盖：`docker-build.bat [镜像名] [版本号]`）；Docker 不可用时以错误码 1 直接退出
- `scripts/docker-publish.bat`：先构建，成功后才依次推送 `latest` 与 `:<版本号>`；推送失败立即停止并提示 `docker login`。两个脚本都不碰 git：提交与打 tag 请在发布前自行完成
- `docs/DEPLOY.md`：新增「一键构建 / 发布脚本（Windows）」小节

### 变更（仓库结构）

- `API.md`、`DEPLOY.md`、`workflow.md`、`Ranker.md` 与 `tests/TESTCASES.md` 移入 `docs/`（用 `git mv` 保留历史）；`README*`、`CHANGELOG*`、`AGENTS.md` 保持在根目录，相关交叉链接同步更新
- `.example.env` 更名为约定的模板名 `.env.example`；README / README_CN / `docs/DEPLOY.md` / `docs/workflow.md` / `docs/TESTCASES.md` / AGENTS.md 中的拷贝命令全部改用新名字
- 删除两个孤立根文件：`opencode.jsonc`（它指向的 `.opencode/skills` 已在 9a8bfea 删除，如今指向空目录）与 `Modelfile.reranker`（重排器的权威 Modelfile 在仓库外，由 `docs/Ranker.md` 记录）；`data/config.example.yaml` 里的交叉引用改指向该文档

### 变更（System One 后端）

- **破坏性（部署）**：`LAYA_SERVER_API_KEY` 更名为 `SYSTEMONE_SERVER_API_KEY`——该端点不再只服务 laya-server。请在 `.env` / 容器环境里同步改名；`systemone.api_key_env` 仍可指向任意变量名
- Ollama 0.40+ 原生提供 `POST /v1/systemone`，`systemone.backend` 因此可以直接指向任意 Ollama 端口（如 `http://llm.home:11435`）；网关侧无需改代码，转发仍是纯透传
- 已确认 `systemone.model` **会随请求体发送到后端**：仅当它是具体值且调用方未自带 `model` 时注入，`"local"` 仍是「不注入」哨兵。代码默认值保持 `auto`（laya-server 合法）；`data/config.yaml` 与 `data/config.example.yaml` 改为指向 Ollama 并配 `model: "tev1"`，因为 Ollama 对 `auto` 返回 **404**、缺 `model` 返回 **400**
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
