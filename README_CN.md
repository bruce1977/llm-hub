# LLM Hub

把多个 Ollama 实例聚合成一个统一的 API 入口：按模型自动路由、统一鉴权、
补齐 Ollama 缺失的 Rerank API，并通过别名机制强制注入请求参数（如关闭思考模式）。

## 特性

| 能力 | 说明 |
|------|------|
| **多上游路由** | 按请求体中的 `model` 字段，转发到对应端口的 Ollama 实例 |
| **API Key 鉴权** | 支持多 Key 并存，常量时间比较，兼容 `Bearer` / `X-API-Key` |
| **Rerank API** | 提供 `/v1/rerank`，支持生成式（`logprobs`）与向量（`embedding`）两种打分 |
| **统一模型列表** | `/v1/models` 聚合配置中所有实例与别名，而非代理到单个实例 |
| **别名 + 参数注入** | `qwen3.5:4b-nothink` → 转发 `qwen3.5:4b` 并强制注入 `think: false` |
| **流式透传** | NDJSON（`/api/chat`）与 SSE（`/v1/chat/completions`）原样转发 |
| **配置热重载** | 修改 `config.json` 约 1 秒内自动生效，无需重启容器 |
| **Docker 外挂** | 配置文件通过 `/data` 卷挂载 |
| **路由优先级** | 同名模型出现在多个上游时，按 `priority` 数值最小者优先 |

## 目录结构

```
llm-hub/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── app/
│   ├── main.py       # FastAPI 入口与特殊端点
│   ├── config.py     # 配置模型、加载与热重载、别名解析
│   ├── auth.py       # API Key 鉴权
│   ├── proxy.py      # 反向代理与流式透传
│   └── rerank.py     # Rerank API 实现
└── data/
    └── config.json   # 配置文件（挂载到容器 /data）
```

## 快速开始

### 方式一：从 Docker Hub 拉取（推荐）

```bash
# 1. 拉取最新镜像
docker pull bruce1977/llm-hub:latest

# 2. 准备配置与密钥
mkdir -p llm-hub/data
cp data/example.config.json llm-hub/data/config.json
cp .example.env llm-hub/.env
# 编辑 llm-hub/data/config.json 和 llm-hub/.env，填入你的配置

# 3. 启动
docker run -d --name llm-hub \
  -p 8888:8000 \
  -v $(pwd)/llm-hub/data:/data \
  --env-file llm-hub/.env \
  --restart unless-stopped \
  bruce1977/llm-hub:latest
```

### 方式二：从源码构建

本项目采用 **`docker build` + `docker run`** 的方式部署（不依赖 compose）。配置目录通过卷挂载，
密钥通过 `--env-file` 注入，二者都不写进镜像。

```bash
cd llm-hub

# 1. 准备配置与密钥（config.json 放到挂载目录，密钥放 .env）
#    notepad data/config.json

# 2. 构建镜像
docker build -t llm-hub:latest .

# 3. 启动：配置目录挂到 /data，源码挂到 /app，密钥经 .env 注入
#    （下面示例把配置放在 ${target folder}，对外映射到 8888）
docker run -d --name llm-hub \
  -p 8888:8000 \
  --add-host host.docker.internal:host-gateway \
  -v ${target folder}:/data \
  -v ${target folder}/app:/app \
  --env-file ${target folder}​/.env \
  llm-hub:latest

# 4. 验证
curl http://localhost:8888/health
```

> **密钥通过 `.env` 提供**：`--env-file` 读取其中的 `GATEWAY_API_KEYS` 并注入容器，
> `config.json` 里因此不需要写任何密钥。请妥善保管 `.env`，切勿提交到版本库；换密钥时只改这一处即可。
> Windows 下 `--env-file` / `-v` 的路径务必使用盘符形式（`${target folder}`），不要写 `/d/...`（docker 找不到文件）。

日志查看：`docker logs -f llm-hub`

改配置（如 `priority`、`upstreams`）后无需重建镜像，执行 `docker restart llm-hub` 即可；
若只改了 `config.json`，约 1 秒后还会被热重载自动生效。

## 配置模板（example 文件）

仓库里附带两份**不带任何真实信息**的模板，复制后填写即可：

| 模板 | 复制为 | 说明 |
|------|--------|------|
| `data/example.config.json` | `data/config.json` | 网关配置样例，含 `upstreams` / `aliases` / `rerank` / `security` 全字段，已示范 `priority` 路由优先级 |
| `.example.env` | `.env` | 密钥样例，只需把 `GATEWAY_API_KEYS` 换成你自己的随机串（≥16 位） |

```bash
cp data/example.config.json data/config.json
cp .example.env .env
# 然后编辑 config.json 填真实的 upstream base_url，编辑 .env 填真实密钥
```

> `config.json` 与 `.env` 已在 `.gitignore` 中忽略，不会被提交；模板 `example.config.json` / `.example.env` 保留在版本库，方便他人直接取用。
> 部署目录（如 `${target folder}`）同样放置了这两份模板，用法一致。

## 构建镜像（docker build）

仓库根目录已提供 `Dockerfile`（基于 `python:3.12-slim`）。除了 `docker compose up`（内部也是先 build 再 up），
也可以直接用 `docker build` 显式构建镜像——更适合自定义标签、指定平台或推送到镜像仓库的场景。

```bash
cd llm-hub

# 构建并打标签
docker build -t llm-hub:1.0.0 -t llm-hub:latest .

# 部署到 Linux/amd64 服务器时，建议显式指定平台，避免拉取到 ARM 镜像
docker build --platform linux/amd64 -t llm-hub:1.0.0 .
```

构建完成后，不依赖 compose 也能直接跑——用 `docker run` 并挂载配置卷：

```bash
docker run -d --name llm-hub \
  -p 8888:8000 \
  --add-host host.docker.internal:host-gateway \
  -v ${target folder}:/data \
  -v ${target folder}/app:/app \
  --env-file ${target folder}/.env \
  llm-hub:1.0.0
```

要点：

- `-v ${target folder}:/data`：把宿主机上的 `config.json` 挂进容器 `/data`，与 `CONFIG_PATH` 默认路径一致（Windows 用盘符路径）
- `-v ${target folder}/app:/app`（可选）：源码目录挂载。容器内所有 `*.py` 直接位于 `/app`（`main.py`、`config.py`、`auth.py`、`proxy.py`、`rerank.py`），挂载后改代码只需重启容器即可生效，不必重新构建镜像
- `--add-host host.docker.internal:host-gateway`：Linux 上让容器访问宿主机 Ollama；Windows / macOS 的 Docker Desktop 自带该解析，可省略
- `--env-file ${target folder}/.env`：用环境变量文件注入密钥，避免明文写在 `config.json`（详见下方「安全」章节）

推送到镜像仓库（可选）：

```bash
docker tag llm-hub:1.0.0 registry.example.com/llm-hub:1.0.0
docker push registry.example.com/llm-hub:1.0.0
```

## 配置详解（config.json）

### `server` —— 网关自身

| 字段 | 默认 | 说明 |
|------|------|------|
| `host` / `port` | `0.0.0.0` / `8000` | 监听地址与端口 |
| `log_level` | `info` | `debug` / `info` / `warning` / `error` |
| `request_timeout` | `600` | 上游默认超时（秒） |
| `max_concurrency` | `64` | HTTP 连接池上限 |

### `auth` —— 鉴权

```json
"auth": {
  "enabled": true,
  "api_keys": [],
  "allow_anonymous_health": true
}
```

- `api_keys`：允许多个 Key 并存，任意一个通过即可。**推荐留空**，改由环境变量提供
- 密钥来源优先级：`config.json` 的 `api_keys` → `GATEWAY_API_KEYS` 环境变量 → `GATEWAY_API_KEYS_FILE` 指向的密钥文件，三者会合并去重
- `GATEWAY_API_KEYS`：`GATEWAY_API_KEYS=key1,key2`（逗号/换行分隔）
- `GATEWAY_API_KEYS_FILE`：指向存放密钥的文件路径，适合 Docker/K8s secrets（`docker inspect` 也不会泄露）
- ⚠️ 启用鉴权（`enabled: true`）却一个 Key 都没有时，网关**拒绝启动**并提示配置方式，避免误暴露无鉴权服务
- `allow_anonymous_health`：`/health` 免鉴权（建议保持 `true`，否则容器健康检查会失败）；`/docs` 等文档是否公开由 `security.allow_docs` 控制（默认 `false`）
- `enabled: false` 可临时关闭鉴权

### `upstreams` —— 后端实例列表（Ollama / LM Studio）

```json
"upstreams": [
  {
    "name": "local-main",
    "type": "ollama",
    "base_url": "http://host.docker.internal:11434",
    "models": ["qwen3:8b", "qwen3.5:4b", "bge-m3:latest"],
    "timeout": 600,
    "description": "本机主实例",
    "headers": {}
  }
]
```

- `type`：后端类型，决定模型发现与健康探测走哪套接口，缺省为 `ollama`
  - `ollama`：模型列表 `/api/tags`、健康检查 `/api/version`
  - `lmstudio`：OpenAI 兼容协议，模型列表 `/v1/models`、健康检查 `/v1/models`
- `models`：**支持前缀通配**，`"qwen3:*"` 可命中 `qwen3:4b`、`qwen3:8b`、`qwen3:14b`
- `model_aliases`：把该实例中冗长的模型 id 映射成短名（见下方 LM Studio 章节）
- `timeout`：覆盖全局超时，大模型建议调大
- `headers`：可选，转发到该实例时附加的自定义请求头（如上游自身也有鉴权）
- `priority`：路由优先级（整数，**默认 `0`**）。当同一个模型名在多个上游中都被声明时，
  网关会选中 `priority` **数值最小**的那个上游；数值相同则按 `upstreams` 列表中的声明顺序优先。
  常用于把同一模型同时挂在 GPU 实例与 iGPU/CPU 实例上，让高优实例先承接、其余作兜底

#### 同名模型命中多个上游时的路由规则

按以下顺序决定最终上游：

1. 若请求的是**别名**（`aliases` 中的键），优先使用别名的 `upstream`；别名未指定 `upstream` 时进入第 2 步
2. 收集所有 `models` 能匹配该模型名的上游（支持精确名与前缀通配 `qwen3:*`）
3. 在这些候选里取 `priority` **最小**者；并列时取 `upstreams` 中**更靠前**的那一个
4. 若没有任何上游匹配且 `routing.strict=false`，退回 `default_upstream`

> 示例：模型 `qwen3:8b` 同时出现在 `gpu`（priority `0`）与 `cpu`（priority `10`）两个上游，
> 请求会固定打到 `gpu`；把 `gpu` 的 `priority` 改成 `20` 即可让 `cpu` 优先。
> 注意：`/v1/models` 聚合列表中该模型的 `upstream` 字段同样显示优先级胜出的那个。

### `routing` —— 路由策略

- `strict: false`（默认）：模型未匹配任何实例时，转发到 `default_upstream`
- `strict: true`：未声明的模型直接返回 404，避免误打到默认实例

### `aliases` —— 别名与参数注入

```json
"aliases": {
  "qwen3.5:4b-nothink": {
    "model": "qwen3.5:4b",
    "params": { "think": false }
  }
}
```

请求 `qwen3.5:4b-nothink` 时，网关会把 `model` 替换为 `qwen3.5:4b`，
并将 `params` 中的字段**强制覆盖**进请求体（同名参数以别名为准）。

## 集成 LM Studio

LM Studio 提供的是 OpenAI 兼容接口，模型 id 通常带发布方前缀、名字很长
（例如 `qwen/qwen3.5-9b`）。网关原生支持它，并提供「短名映射」把它收敛成 `qwen3.5:9b`。

**1. 确认 LM Studio 已开启本地 API 服务**

在 LM Studio 中加载模型并启动 Local Server（默认端口 `1234`），然后确认可访问：

```bash
curl http://127.0.0.1:1234/v1/models
```

**2. 在 `config.json` 里登记为一个上游**

```json
{
  "name": "lmstudio",
  "type": "lmstudio",
  "base_url": "http://host.docker.internal:1234",
  "models": [],
  "model_aliases": {
    "qwen3.5:9b": "qwen/qwen3.5-9b"
  },
  "timeout": 600
}
```

- `type: "lmstudio"`：网关改用 `/v1/models` 去发现模型列表与探测存活（Ollama 用的是 `/api/tags`、`/api/version`）
- `model_aliases`：建立「对外短名 → LM Studio 真实 id」的映射，键是对外暴露的名字，值是上游真实 id

**3. 用短名直接访问**

```bash
curl http://<网关地址>:8000/v1/chat/completions \
  -H "Authorization: Bearer <你的API Key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5:9b","messages":[{"role":"user","content":"hi"}]}'
```

网关收到 `qwen3.5:9b` 后，会把请求体里的 `model` 换成 `qwen/qwen3.5-9b` 再转发给
LM Studio，客户端完全无感。`/v1/models` 聚合时也会自动展开这些别名，所以
Dify / RAGFlow / WorkBuddy 等客户端看到的模型列表是整齐的短名。

> 从容器里访问**宿主机**的 LM Studio 用 `host.docker.internal:1234`；
> 如果 LM Studio 跑在别的机器，直接填那台机器的内网 IP。

`params` 可写任意 Ollama 支持的参数，例如：

```json
"fast": {
  "model": "qwen3:4b",
  "upstream": "local-main",
  "params": { "think": false, "num_ctx": 8192, "temperature": 0.2 }
}
```

### `rerank` —— 重排配置

```json
"rerank": {
  "enabled": true,
  "mode": "logprobs",
  "models": ["qwen3-reranker:4b"],
  "normalize": false,
  "max_concurrency": 8,
  "return_documents": true,
  "model_modes": { "bge-m3:latest": "embedding" }
}
```

两种打分模式：

| 模式 | 适用模型 | 原理 |
|------|----------|------|
| `logprobs` | Qwen3-Reranker 等生成式重排模型 | 构造 yes/no 二分类 prompt，读取首个 token 的 logprobs 做 softmax |
| `embedding` | bge-m3 等向量模型 | 分别取 query 与文档向量，计算余弦相似度 |

`mode` 是全局默认打分方式；当 reranker 混用不同类型模型（如生成式 `qwen3-reranker:4b` + 向量 `bge-m3:latest`）时，
可用 `model_modes` 为单个模型单独指定模式，例如 `{"bge-m3:latest": "embedding"}`。

## API 使用

以下示例均假设网关运行在 `http://localhost:8000`。

### 鉴权

两种方式任选其一：

```bash
-H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
-H "X-API-Key: sk-gateway-9f2c8a1b4d5e6f70"
```

### 错误响应

所有错误响应遵循统一格式：

```json
{
  "detail": "描述问题的错误信息"
}
```

| 状态码 | 错误 | 说明 |
|--------|------|------|
| `400` | Bad Request | 无效 JSON、缺少必填字段或请求格式错误 |
| `401` | Unauthorized | 缺少 API 密钥（启用鉴权时） |
| `403` | Forbidden | API 密钥无效、IP 不在白名单或端点被阻止 |
| `404` | Not Found | 未知端点或模型在所有上游中均未找到 |
| `405` | Method Not Allowed | 不支持的 HTTP 方法（如对 `/v1/rerank` 发起 GET） |
| `413` | Request Entity Too Large | 请求体超过 `security.max_body_bytes`（默认 32MB） |
| `422` | Unprocessable Entity | 请求体是有效 JSON 但验证失败 |
| `502` | Bad Gateway | 上游不可达或返回错误 |
| `504` | Gateway Timeout | 上游未在配置的超时时间内响应 |

**示例：**

```bash
# 缺少 API 密钥
curl http://localhost:8000/v1/models
# 401 {"detail":"Missing API key. Provide it via 'Authorization: Bearer <key>' or 'X-API-Key: <key>' header."}

# 无效的 API 密钥
curl http://localhost:8000/v1/models -H "Authorization: Bearer wrong-key"
# 403 {"detail":"Invalid API key."}

# 模型未找到（当 routing.strict=true 时）
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-key" \
  -d '{"model":"unknown-model","messages":[{"role":"user","content":"hi"}]}'
# 404 {"detail":"No upstream configured for model 'unknown-model'."}

# 被阻止的管理端点
curl http://localhost:8000/api/delete \
  -H "Authorization: Bearer sk-key" \
  -d '{"name":"model"}'
# 403 {"detail":"Endpoint '/api/delete' is disabled on this public gateway."}
```

### 转发请求（与 Ollama 完全兼容）

```bash
# 原生 API —— 自动路由到配置了该模型的实例
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.6:35b-pruned","messages":[{"role":"user","content":"你好"}],"stream":false}'

# OpenAI 兼容接口
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"你好"}]}'

# 向量
curl http://localhost:8000/api/embed \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-m3:latest","input":["你好世界"]}'
```

### 别名关闭思考模式

```bash
# 完整转发，保留 think 默认值
curl .../api/chat -d '{"model":"qwen3.5:4b","messages":[...]}'

# 命中别名，网关自动注入 think:false
curl .../api/chat -d '{"model":"qwen3.5:4b-nothink","messages":[...]}'
```

两条请求打到的是同一个模型，但后者不会输出 `<think>` 思考过程。

### 统一模型列表

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
```

```json
{
  "object": "list",
  "data": [
    { "id": "qwen3:8b", "object": "model", "owned_by": "ollama",
      "upstream": "local-main", "type": "upstream", "wildcard": false },
    { "id": "qwen3.5:4b-nothink", "object": "model", "owned_by": "ollama",
      "upstream": "local-main", "type": "alias", "target": "qwen3.5:4b" }
  ]
}
```

`GET /api/tags` 同样返回聚合结果，但采用 Ollama 原生格式，
便于让 Ollama 官方客户端 / Open WebUI 等直接把网关当作单一实例使用。

### Rerank

```bash
curl http://localhost:8000/v1/rerank \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-reranker:4b",
    "query": "如何冲泡手冲咖啡？",
    "documents": [
      "手冲咖啡需要控制水温在 90 度左右",
      "Python 是一种编程语言",
      "咖啡豆的研磨度会影响萃取速度"
    ],
    "top_n": 3,
    "return_documents": true
  }'
```

响应：

```json
{
  "id": "rerank-9f2c8a1b4d5e6f70",
  "model": "qwen3-reranker:4b",
  "object": "list",
  "results": [
    { "index": 0, "relevance_score": 0.91, "rank": 0, "document": { "text": "手冲咖啡需要..." } },
    { "index": 2, "relevance_score": 0.63, "rank": 1, "document": { "text": "咖啡豆的研磨度..." } },
    { "index": 1, "relevance_score": 0.02, "rank": 2, "document": { "text": "Python 是..." } }
  ],
  "usage": { "total_tokens": 0, "prompt_tokens": 0, "rerank_count": 3 }
}
```

请求字段：`model`、`query`、`documents`（字符串数组或 `{text:...}` 对象数组）、
`top_n`、`return_documents`、`instruction`（覆盖默认指令）。

### 健康检查

```bash
curl http://localhost:8000/health
```

返回各上游的连通性、模型数量与 Ollama 版本。任一上游不可达时返回 503。**无需 API 密钥**（当 `auth.allow_anonymous_health` 为 `true` 时，默认即为此值）。

## 启动第二个 Ollama 实例

单台机器上跑多个 Ollama 实例，需要指定不同的端口和数据目录：

```powershell
# Windows PowerShell
$env:OLLAMA_HOST = "0.0.0.0:11435"
$env:OLLAMA_MODELS = "D://path//to//models"
ollama serve
```

```bash
# Linux / macOS
OLLAMA_HOST=0.0.0.0:11435 OLLAMA_MODELS=/data/ollama-big ollama serve
```

然后在 `config.json` 里把 `base_url` 指向 `http://host.docker.internal:11435`。

## 常见问题

**容器里访问不到宿主机的 Ollama？**

- Windows / macOS 的 Docker Desktop：直接用 `host.docker.internal`，已在 compose 中配置
- Linux：需要 `extra_hosts: ["host.docker.internal:host-gateway"]`（已在 compose 中配置）
- 若网关部署在独立机器上，直接填该机器的内网 IP，如 `http://<host-ip>:11434`

**健康检查一直失败？**

若把 `allow_anonymous_health` 设为 `false`，`/health` 也需要鉴权，
容器内置的 HEALTHCHECK 会因 401 判定失败。请保持该项为 `true`。

**`think: false` 不生效？**

确认 Ollama 版本支持 `think` 参数（0.9+）。较老版本可在别名参数里改用
`{"stop": ["<think>"]}` 作为临时方案。

**Rerank 分数全是 0？**

说明未能从上游响应中提取到 logprobs。把 `log_level` 调成 `debug` 观察原始响应，
或改用 `embedding` 模式（需向量模型）。

**改了配置没生效？**

配置热重载依赖文件 mtime 变化。某些编辑器或网络挂载（NFS / SMB）可能不更新 mtime，
此时执行 `docker restart llm-hub` 即可。

**工具调用返回 400 / `cannot unmarshal object into ... arguments of type string`？**

部分 OpenAI 兼容客户端在流式拼接工具调用时，会把 `tool_calls[].function.arguments`
发成 JSON **对象**（如 `{"path":"main.py"}`）而非 OpenAI 规定的 JSON **字符串**。
Ollama 的 gin 解析器会严格拒绝此类请求并报 400。网关在转发前会自动把对象/数组
重新序列化为字符串，因此**只要客户端走网关（而非直连 Ollama:11434）即可规避**。
若客户端直连 Ollama，需确保其 SDK 始终以字符串形式发送 `arguments`。

## 安全（公网暴露必读）

网关默认按「公网不可信」设计。新增的 `security` 段统一收口加固项：

| 字段 | 默认 | 说明 |
|------|------|------|
| `admin_endpoints` | `deny` | `deny` 拦截 pull/push/create/delete/copy/blobs；`readonly` 额外放行 `pull`；`allow` 全部转发（仅内网） |
| `blocked_paths` | `[]` | 额外封禁的路径前缀，如 `["api/show"]` 或 `["api/*"]` |
| `allow_docs` | `false` | 是否公开 `/docs`、`/redoc`、`/openapi.json`（公网建议关） |
| `cors_origins` | `[]` | 显式允许的跨域来源；留空则完全不返回 CORS 头（不再有 `*`） |
| `max_body_bytes` | `33554432` | 请求体上限（32MB），超过返回 413，防大 body 打挂 |
| `allow_query_api_key` | `false` | 是否接受 `?api_key=` 查询参数（默认关，避免密钥进日志/浏览器历史） |
| `expose_health_details` | `false` | `/health` 是否回显 `config_path` 与上游 `base_url`（默认脱敏） |
| `fail_on_weak_keys` | `true` | 启动时检测到弱密钥/占位密钥直接拒绝启动 |
| `allowed_client_ips` | `[]` | 客户端 IP 白名单（支持 CIDR，如 `10.0.0.0/8`），留空不限制 |

### 把密钥移出磁盘（推荐）

把 `api_keys` 明文留在 `config.json` 等于把密钥落盘。更安全的做法是在运行环境注入：

```bash
# 容器 / 进程环境变量，逗号或换行分隔多个 Key
export GATEWAY_API_KEYS="sk-gateway-9f2c8a1b4d5e6f70,sk-gateway-2b3c4d5e6f7a8b9c"
```

网关启动时会把环境变量里的 Key 合并进 `auth.api_keys`，`config.json` 里即可只写 `[]` 或不写。

### 公网部署 checklist

- [ ] `admin_endpoints` 保持 `deny`（或至少 `readonly`），绝不 `allow`
- [ ] 所有 `api_keys` ≥ 16 位随机串；或用 `GATEWAY_API_KEYS` 环境变量注入
- [ ] 前面有反向代理（Nginx/Caddy）终止 TLS，并带上 `X-Forwarded-For`
- [ ] 如需限制来源，`allowed_client_ips` 填可信网段
- [ ] `allow_docs` 保持 `false`
- [ ] 不要暴露 11434/11435 等上游端口，只暴露网关 8000

## 本地开发（不依赖 Docker）

### 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备配置文件
cp data/example.config.json data/config.json
# 编辑 data/config.json，填入真实的上游地址

# 3. 设置 API 密钥（必须，或写在 config.json 的 auth.api_keys 中）
export GATEWAY_API_KEYS="${your_secret_api_key}"

# 4. 启动服务
cd app
python -m uvicorn main:app --reload --port 8000
```

### Windows PowerShell 启动

```powershell
# 1. 安装依赖
pip install -r requirements.txt

# 2. 设置环境变量（必须）
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.json"
$env:GATEWAY_API_KEYS="${your_secret_api_key}"

# 3. 启动服务
cd app
python -m uvicorn main:app --reload --port 8000
```

### 指定配置文件运行

```bash
CONFIG_PATH=./data/config.json GATEWAY_API_KEYS="${your_secret_api_key}" python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

```powershell
# Windows PowerShell
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.json"
$env:GATEWAY_API_KEYS="${your_secret_api_key}"
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 后台运行（Linux/macOS）

```bash
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > llm-hub.log 2>&1 &
```

### 注册为 Windows 服务（使用 NSSM）

```powershell
# 安装 NSSM: choco install nssm
nssm install LLMHub "C:\path\to\python.exe" "-m" "uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8000"
nssm set LLMHub AppDirectory "D:\workspace\github\llm-hub\app"
nssm set LLMHub AppEnvironmentExtra "CONFIG_PATH=D:\workspace\github\llm-hub\data\config.json"
nssm start LLMHub
```

交互式文档：<http://localhost:8000/docs>（需设置 `security.allow_docs: true`）
