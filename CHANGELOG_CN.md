# 更新日志

本文件记录项目的所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [未发布]

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
- `.example.env` / README / README_CN / API.md / TESTCASES.md：示例 API Key 统一替换为明显可辨的占位值（原 `sk-gateway-…` + 十六进制的形式会命中密钥扫描）
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
