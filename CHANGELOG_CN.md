# 更新日志

本文件记录项目的所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [未发布]

### 安全

- 新增 HTTP 方法允许列表（`security.allowed_http_methods`，默认 GET/POST → 其他返回 405）
- 新增正则路径白名单（`security.path_allowlist`，未匹配路径返回 403）
- 新增内存令牌桶限流器（`security.rate_limit`，超量返回 429）
- 所有响应默认携带安全加固响应头（nosniff、X-Frame-Options、CSP 等）
- README 和 README_CN 补充加固配置说明

### 变更

- Dockerfile 健康检查间隔从 30 秒改为 1 小时（被动网关无需频繁探测）

### 新增（Rerank）

- 新增 `keep_alive` 配置，管理 Ollama 重排器的 VRAM 占用（0 = 打完即卸，"5m" = 保持 5 分钟，-1 = 服务端默认）
- 新增 `RateLimitConfig` 模型，含 `enabled`、`per_minute`、`burst`、`by`（ip/key）
- 默认 rerank `options.num_ctx` 设为 8192，`keep_alive` 设为 "3m"
- 新增 `Modelfile.reranker` 低显存重排器变体（8192 ctx ≈ 4 GB，原 40960 ctx ≈ 9.3 GB）
- 新增 `Ranker.md` 重排器配置完整指南
- 新增 `tests/verify_security.py` 安全验证测试套件（15 项）

### 文档

- AGENTS.md：补充新安全功能描述
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
