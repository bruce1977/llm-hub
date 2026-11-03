# 更新日志

本文件记录项目的所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [未发布]

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
