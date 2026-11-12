# LLM Hub - 测试用例文档

## 目录

- [健康检查](#健康检查)
- [实例探测](#实例探测)
- [API 认证](#api-认证)
- [模型列表](#模型列表)
- [聊天补全](#聊天补全)
- [文本补全](#文本补全)
- [嵌入向量](#嵌入向量)
- [重排序 API](#重排序-api)
- [流式响应](#流式响应)
- [别名参数注入](#别名参数注入)
- [安全测试](#安全测试)
- [联网检索](#联网检索)
- [管理 API（Web 管理台）](#管理-apiweb-管理台)

---

## 健康检查

### 测试 1: 获取健康状态

**说明:** `/health` 端点默认无需 API 密钥，仅返回 `{"ok":true}`，不消耗任何资源。

**请求:**

```bash
curl http://localhost:8888/health
```

**响应 (200 OK):**

```json
{
  "ok": true
}
```

---

## 实例探测

### 测试 2: 探测所有上游实例状态

**说明:** `/probe` 端点需要 API 密钥，返回所有配置的上游实例及其模型的可用性状态。

**请求:**

```bash
curl http://localhost:8888/probe \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**响应 (200 OK):**

```json
{
  "upstreams": [
    {
      "name": "local-main",
      "type": "ollama",
      "base_url": "http://host.docker.internal:11434",
      "reachable": true,
      "version": "0.32.13",
      "models": [
        {
          "name": "qwen3:8b",
          "declared": true,
          "available": true
        },
        {
          "name": "qwen3.5:4b",
          "declared": true,
          "available": true
        },
        {
          "name": "qwen3:*",
          "declared": true,
          "available": true,
          "wildcard": true
        }
      ]
    },
    {
      "name": "remote-lmstudio",
      "type": "lmstudio",
      "base_url": "http://192.168.1.100:1234",
      "reachable": false,
      "error": "Connection refused",
      "models": [
        {
          "name": "qwen3.5:9b",
          "declared": true,
          "available": false
        }
      ]
    }
  ]
}
```

### 测试 3: 未授权访问 /probe

**请求:**

```bash
curl http://localhost:8888/probe
```

**响应 (401 Unauthorized):**

```json
{
  "detail": "Missing API key. Provide it via 'Authorization: Bearer <key>' or 'X-API-Key: <key>' header."
}
```

---

## API 认证

### 测试 3: 缺少 API 密钥

**请求:**

```bash
curl http://localhost:8000/v1/models
```

**响应 (401 Unauthorized):**

```json
{
  "detail": "Missing API key. Provide it via 'Authorization: Bearer <key>' or 'X-API-Key: <key>' header."
}
```

### 测试 4: 使用 Bearer 认证

**请求:**

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**响应 (200 OK):**

```json
{
  "object": "list",
  "data": [...]
}
```

### 测试 5: 使用 X-API-Key 认证

**请求:**

```bash
curl http://localhost:8000/v1/models \
  -H "X-API-Key: sk-gateway-example-key"
```

**响应 (200 OK):**

```json
{
  "object": "list",
  "data": [...]
}
```

### 测试 6: 无效的 API 密钥

**请求:**

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer invalid-key"
```

**响应 (403 Forbidden):**

```json
{
  "detail": "Invalid API key."
}
```

---

## 模型列表

### 测试 7: 获取 OpenAI 格式模型列表

**请求:**

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**响应 (200 OK):**

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen3:8b",
      "object": "model",
      "created": 0,
      "owned_by": "ollama",
      "upstream": "local-main",
      "type": "upstream",
      "wildcard": false
    },
    {
      "id": "qwen3.5:4b-nothink",
      "object": "model",
      "created": 0,
      "owned_by": "ollama",
      "upstream": "local-main",
      "type": "alias",
      "target": "qwen3.5:4b"
    }
  ]
}
```

### 测试 8: 获取 Ollama 格式模型列表

**请求:**

```bash
curl http://localhost:8000/api/tags \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**响应 (200 OK):**

```json
{
  "models": [
    {
      "name": "qwen3:8b",
      "model": "qwen3:8b",
      "modified_at": "2026-09-07T06:00:00Z",
      "size": 0,
      "digest": "sha256:gateway",
      "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "gateway",
        "families": ["gateway"],
        "parameter_size": "",
        "quantization_level": ""
      },
      "gateway": {
        "upstream": "local-main",
        "type": "upstream",
        "target": null
      }
    }
  ]
}
```

---

## 聊天补全

### 测试 9: Ollama 原生聊天 API

**请求:**

```bash
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [
      {"role": "user", "content": "你好，请介绍一下自己"}
    ],
    "stream": false
  }'
```

**响应 (200 OK):**

```json
{
  "model": "qwen3:8b",
  "message": {
    "role": "assistant",
    "content": "你好！我是一个AI助手..."
  },
  "done": true,
  "total_duration": 1234567890,
  "eval_count": 42
}
```

### 测试 10: OpenAI 兼容聊天 API

**请求:**

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [
      {"role": "system", "content": "你是一个有帮助的助手"},
      {"role": "user", "content": "什么是机器学习？"}
    ],
    "temperature": 0.7,
    "max_tokens": 500
  }'
```

**响应 (200 OK):**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1694000000,
  "model": "qwen3:8b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "机器学习是人工智能的一个分支..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 100,
    "total_tokens": 125
  }
}
```

### 测试 11: 带工具调用的聊天

**请求:**

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [
      {"role": "user", "content": "读取 main.py 文件"}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "read_file",
          "description": "读取文件内容",
          "parameters": {
            "type": "object",
            "properties": {
              "path": {"type": "string", "description": "文件路径"}
            },
            "required": ["path"]
          }
        }
      }
    ]
  }'
```

**响应 (200 OK):**

```json
{
  "id": "chatcmpl-def456",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "read_file",
              "arguments": "{\"path\":\"main.py\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

---

## 文本补全

### 测试 12: Ollama 原生补全 API

**说明:** `/api/generate` 是显式注册的文档化路由（`/docs` 里能看到它的请求/响应 schema），
转发仍走通用代理：按 `model` 路由（含别名解析与参数注入）、缺省即流式、其他方法 **405**。

**请求:**

```bash
curl http://localhost:8000/api/generate \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "prompt": "写一首关于编程的诗：",
    "raw": true,
    "format": "json",
    "keep_alive": "3m",
    "options": {"temperature": 0, "num_predict": 256},
    "stream": false
  }'
```

**响应 (200 OK):**

```json
{
  "model": "qwen3:8b",
  "response": "代码如诗行，\n逻辑似水流...",
  "done": true,
  "done_reason": "stop",
  "total_duration": 2345678901,
  "eval_count": 50
}
```

**流式（`stream` 省略或为 `true`）:** 返回 `application/x-ndjson`，每行一个片段，最后一行 `done: true`。

```bash
curl -N http://localhost:8000/api/generate \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","prompt":"数到 5：","raw":true}'
```

**非 POST 方法被网关拒绝（不会打到上游）:**

```bash
curl -i http://localhost:8000/api/generate -H "Authorization: Bearer sk-gateway-example-key"
# HTTP/1.1 405 Method Not Allowed
# allow: POST
# {"detail":"/api/generate only accepts POST."}
```

**别名同样生效:** `{"model":"qwen3.5:4b-nothink", ...}` 转发时模型名换成 `qwen3.5:4b` 并注入
`think: false`；`num_ctx_overrides` 里声明的模型还会被强制改写 `options.num_ctx`。

> 对应的 mock 断言在 `python tests/smoke_test.py` 的 `[18] Ollama-native text generation` 分组
> （路由、字段透传、别名注入、显式/缺省流式、401、405），
> `/docs` schema 断言在 `python tests/test_gateway_new.py` 的 `[3]` 分组。

---

## 嵌入向量

### 测试 13: 生成嵌入向量

**请求:**

```bash
curl http://localhost:8000/api/embed \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-m3:latest",
    "input": ["你好世界", "机器学习入门"]
  }'
```

**响应 (200 OK):**

```json
{
  "model": "bge-m3:latest",
  "embeddings": [
    [0.123, 0.456, 0.789, ...],
    [0.987, 0.654, 0.321, ...]
  ]
}
```

---

## 重排序 API

### 测试 14: Logprobs 模式重排序

**请求:**

```bash
curl http://localhost:8000/v1/rerank \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-reranker:4b",
    "query": "如何冲泡手冲咖啡？",
    "documents": [
      "手冲咖啡需要90°C左右的水温",
      "Python是一种编程语言",
      "研磨度影响萃取速度"
    ],
    "top_n": 3,
    "return_documents": true
  }'
```

**响应 (200 OK):**

```json
{
  "id": "rerank-abc123def456",
  "model": "qwen3-reranker:4b",
  "object": "list",
  "results": [
    {
      "index": 0,
      "relevance_score": 0.91234567,
      "rank": 0,
      "document": {
        "text": "手冲咖啡需要90°C左右的水温"
      }
    },
    {
      "index": 2,
      "relevance_score": 0.63456789,
      "rank": 1,
      "document": {
        "text": "研磨度影响萃取速度"
      }
    },
    {
      "index": 1,
      "relevance_score": 0.02345678,
      "rank": 2,
      "document": {
        "text": "Python是一种编程语言"
      }
    }
  ],
  "usage": {
    "total_tokens": 0,
    "prompt_tokens": 0,
    "rerank_count": 3
  }
}
```

### 测试 15: Embedding 模式重排序

**请求:**

```bash
curl http://localhost:8000/v1/rerank \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-m3:latest",
    "query": "机器学习算法",
    "documents": [
      "监督学习和无监督学习是两大类",
      "今天天气很好",
      "神经网络是一种深度学习模型"
    ]
  }'
```

**响应 (200 OK):**

```json
{
  "id": "rerank-xyz789abc123",
  "model": "bge-m3:latest",
  "object": "list",
  "results": [
    {
      "index": 0,
      "relevance_score": 0.87654321,
      "rank": 0
    },
    {
      "index": 2,
      "relevance_score": 0.76543210,
      "rank": 1
    },
    {
      "index": 1,
      "relevance_score": 0.12345678,
      "rank": 2
    }
  ],
  "usage": {
    "total_tokens": 0,
    "prompt_tokens": 0,
    "rerank_count": 3
  }
}
```

---

## 流式响应

### 测试 16: Ollama 原生流式聊天

**请求:**

```bash
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

**响应 (NDJSON 流):**

```json
{"model":"qwen3:8b","message":{"role":"assistant","content":"你"},"done":false}
{"model":"qwen3:8b","message":{"role":"assistant","content":"好"},"done":false}
{"model":"qwen3:8b","message":{"role":"assistant","content":"！"},"done":false}
{"model":"qwen3:8b","message":{"role":"assistant","content":"有什么"},"done":false}
{"model":"qwen3:8b","message":{"role":"assistant","content":"可以"},"done":false}
{"model":"qwen3:8b","message":{"role":"assistant","content":"帮你的"},"done":false}
{"model":"qwen3:8b","done":true,"done_reason":"stop"}
```

### 测试 17: OpenAI 兼容流式聊天

**请求:**

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

**响应 (SSE 流):**

```
data: {"id":"chatcmpl-abc","choices":[{"delta":{"role":"assistant","content":""},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"你"},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"好"},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"！"},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"finish_reason":"stop","index":0}]}

data: [DONE]
```

---

## 别名参数注入

> 根 `aliases:` 的每一项都是**一个独立模型**（有自己的固化参数），Web 管理台会把它单独列成一行；
> 只想给某个已有模型改个短名、不带参数，用 `upstreams[].model_aliases`。

### 测试 18: 使用禁用思考的别名

**请求:**

```bash
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5:4b-nothink",
    "messages": [{"role": "user", "content": "1+1等于几？直接回答"}],
    "stream": false
  }'
```

**说明:** 
- 别名 `qwen3.5:4b-nothink` 会自动映射到 `qwen3.5:4b`
- 同时注入 `think: false` 参数，禁用思考过程

**响应 (200 OK):**

```json
{
  "model": "qwen3.5:4b",
  "message": {
    "role": "assistant",
    "content": "2"
  },
  "done": true
}
```

### 测试 19: 自定义上游别名

**请求:**

```bash
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "big-nothink",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

**说明:**
- 别名 `big-nothink` 映射到 `big-model:72b`
- 指定上游 `mock-b`
- 注入 `think: false` 和 `num_ctx: 8192`

---

## 安全测试

### 测试 20: 外网监听阻止管理端点 (deny 模式)

管理端点黑名单只作用于外网监听（`security.public.admin_endpoints`）。`allow` 除此之外还会在 public 监听上
打开**管理面**（`/admin*` 与 `/v1/management/*`，前缀规则、不走允许清单，但仍需会话 Cookie），
`deny` / `readonly` 下这两个前缀恒 404；在内网监听上该字段完全不生效。要验证 Ollama 端点那一半，先让外网监听放行
`/api/*`，请求才会走到这一层：

```yaml
security:
  public:
    port: 8001
    path_allowlist:
      POST:
      - ^/api/.*$
    admin_endpoints: deny
```

**请求:**

```bash
curl http://localhost:8001/api/delete \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "some-model"}'
```

**响应 (403 Forbidden):**

```json
{
  "detail": "Endpoint '/api/delete' is disabled on this public gateway."
}
```

### 测试 21: 内网监听放行管理端点

同一请求打到内网监听（`server.port`），网关**不做**管理端点拦截，请求被转发到上游 Ollama；
模型不存在时得到 Ollama 自己的 404，而不是上面那条网关文案。

**请求:**

```bash
curl http://localhost:8000/api/delete \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "some-model"}'
```

**响应:** 上游结果（`200` 或 Ollama 的 `404 {"error":"model 'some-model' not found"}`），
绝不会是 `"disabled on this public gateway"`。

### 测试 22: 阻止 API 文档 (默认)

**请求:**

```bash
curl http://localhost:8000/docs
```

**响应 (404 Not Found):**

```json
{
  "detail": "Not found"
}
```

### 测试 23: 请求体大小限制

**请求 (超过 32MB):**

```bash
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","data":"...超大数据..."}'
```

**响应 (413 Request Entity Too Large):**

```json
{
  "detail": "Request body too large."
}
```

### 测试 24: IP 白名单拒绝

> **本节已废弃（2026-11-11）**：`security.allowed_client_ips` 客户端 IP 围栏已移除。
> 内外网用监听端口分区，公网一律要 Key，没有「来自某网段即免鉴权」的特权；
> `X-Forwarded-For` / `X-Real-IP` 现在一律不采信，限流分桶与审计行只用真实 TCP 对端
> （`auth.peer_ip()`）。现行断言见 `test_xff_is_not_a_decision_input`（伪造 XFF 不改变准入）与
> `test_retired_security_knobs`（已移除的配置项不会复活）。
> 下方保留历史用例描述，仅作背景。

**请求 (从非白名单 IP):**

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**响应 (403 Forbidden):**

```json
{
  "detail": "Client address is not permitted to access this gateway."
}
```

### 测试 24A: IP 信任链与双分区（2026-11-10）

对应 `tests/verify_security.py` 的 `test_zone_uses_kernel_socket_not_headers` /
`test_internal_anonymous_toggle` / `test_public_allowlist_and_key` /
`test_public_only_instance` / `test_public_never_exposes_management_or_console`，
以及 `tests/smoke_test.py` 的 `[2b]` 分组。端口与分区策略只来自 `config.yaml`
（`server.port` / `security.public.port`），没有端口环境变量。

| 场景 | 期望 |
|---|---|
| 直连请求携带伪造 `X-Forwarded-For: 192.168.10.5` | `peer_ip()` 仍是真实 TCP 对端，分区判定不变，按该区规则处理 |
| ASGI `scope["client"]` 端口为 0 且带转发头（uvicorn 改写指纹） | `peer_ip()` 返回 `None`，限流 / 审计记为 `unknown`；`zone()` 仍只看内核监听 socket |
| 伪造 XFF 链 + `X-Real-IP`，或前置反代 | 记录的地址始终是**真实 TCP 对端**；没有可信代理列表，也没有客户端 IP 白名单 |
| 旧配置残留 `allow_query_api_key: true` / `trusted_proxies: [...]` | 两个键被忽略并给出启动告警；`SecurityConfig` 里没有这两个字段，`?api_key=` 仍是 `401` |
| `security.public.port` 指定的公网监听端口（如 8001，双区） | 只有 `security.public.path_allowlist` 里「方法 + 正则全匹配」的组合才存在，测试配置列出 `GET /health`、`GET /v1/models`、`POST /v1/chat/completions`。列出的路径无 Key 401、无效 Key 403、伪造内网 XFF 仍 401；未列出的 `/`、`/probe`、`/api/tags`、`/api/ps`、`/api/version`、`/docs`、`/openapi.json`、`/healthz` 一律 404（带有效 Key 也是 404）；`/v1/management/*`、`/admin*` 以及含 `..` 段或 NUL 的路径恒 404 |
| 双区模式的内网监听端口（`server.port: 8000`，`security.public.port: 8001`） | `/` 与 `/health` 始终匿名（探针指向这里）；`security.internal.allow_anonymous: true` 时其它业务 API 免 Key（带无效 Key 也放行）、`/docs`（由 `internal.expose_docs` 控制）与 `/admin` 可达；`false` 时业务 API 需 Key 或会话 Cookie，但 `/`、`/health` 仍匿名；`/v1/management/*` 始终无 Cookie 401 |
| 公网监听端口上的存活探针 | 公网分区没有任何匿名 HTTP 路径：`/health` 只有显式列入 `security.public.path_allowlist` 且带 Key 才可达。默认模板未列出它（公网 `/health` 为 404）；测试配置显式列出 `GET /health`（带 Key 200、无 Key 401），而 `/healthz` 未列出恒 404。容器 HEALTHCHECK 必须指向内网端口，纯公网实例（`security.public.port == server.port`）改用 TCP 端口探测 |
| 公网监听端口上的管理台与探查路由 | `/v1/management/*`、`/admin*` 是硬性 404（管理台只属于内网端口）；`/docs`、`/openapi.json`、`/probe`、`/api/ps`、`/api/version` 等未列入 `security.public.path_allowlist` 的探查路径一律 404，带有效 Key 也一样，从公网无法执行管理或探查操作 |
| `security.internal.allow_anonymous: false` 且 `api_keys` 表无可用 Key | 失败关闭，拒绝启动；运行中的实例 `/v1/models` 无 Key 401。公网分区永远要求 Key，不存在全局匿名模式 |
| 分区判定只认内核监听 socket | `zone()` 由 `scope["server"]` 的本地端口决定，与客户端 IP、XFF、API Key 无关；XFF 也影响不了限流 / 审计地址，更不改变分区或准入。两个宿主端口映射到一个容器端口不会产生两个分区 |

前置条件：镜像以 `python serve.py` 启动（`proxy_headers=False`）。裸 `python -m uvicorn main:app`
会因端口 0 指纹被判为不可信，属于预期的失败关闭行为。注意「两个宿主端口映射到一个容器端口」
不会产生两个分区，双区要求容器内真正存在第二个监听 socket；两个端口都只来自 `config.yaml`。

### 启动脚本的分区检查（Windows）

`scripts\start-local.ps1` / `.bat` 在启动前用应用自己的模块做预检，并按所选入口输出分区布局。
旧版 `-Port` / `-Bind` / `-LogLevel` / `-PublicPorts` / `-PublicAdmin` 命令行开关均已移除，
端口与分区完全来自 `data/config.yaml`（`server.port` / `security.public.port`）。用 `-CheckOnly`
可以只检查、不绑定端口：

| 用例 | 命令 / 配置 | 期望 |
| --- | --- | --- |
| 默认仅内网监听 | `start-local.bat -CheckOnly`（`security.public.port: null`） | `public=none`；入口为 `uvicorn --no-proxy-headers --http ip_guard_protocol:GuardProtocol`；打印 gateway/docs/console/models；设了 `blocked_ips` 时额外打印生效规则 |
| 双区 | 在 `config.yaml` 设 `security.public.port: 18081` 后 `start-local.bat -CheckOnly` | `public=18081`；入口为 `python serve.py`；打印 internal/public 两行分区地址 |
| 公网上的管理接口 | 双区下访问公网端口的 `/v1/management/*`、`/admin`（`admin_endpoints` 保持默认 `deny`） | 公网这两个路径恒 404；管理操作走内网端口、VPN 或 SSH 隧道（`admin_endpoints: allow` 时二者会被路由到真实处理器，但仍需登录 `/admin` 换会话 Cookie，除静态外壳与登录接口外一律 401） |
| 端口被占用 | `server.port` 或 `security.public.port` 已被监听 | 报错并指出 pid，退出码 1；提示编辑 `config.yaml` |
| 无任何凭据 | `api_keys` 表为空、`ADMIN_PASSWORD` 未设置、`security.internal.allow_anonymous: false` | 输出 fail-closed 拒绝启动：没有可用 Key、没有管理台凭据，也没有可匿名的内网分区（公网分区永远不能匿名）；只缺 Key 而 `ADMIN_PASSWORD` 已设置时不拒绝启动，需要 Key 的请求返回 401 |
| 环境里的旧 Key 变量 | `.env` 残留 `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE` | 启动脚本主动从环境删除并告警：调用方 Key 只能在 `/admin` 签发进 `api_keys` 表，环境变量里的一律不生效 |
| `-NoReload` | `start-local.bat -NoReload -CheckOnly` | 入口为 `python serve.py`，无自动重载 |

默认单监听路径下 uvicorn 必须带 `--no-proxy-headers`（否则 `ProxyHeadersMiddleware` 会用
`X-Forwarded-For` 改写 `scope["client"]`，使请求日志里的客户端 IP 可被伪造）与
`--http ip_guard_protocol:GuardProtocol`（让 `security.blocked_ips` 在 ASGI 之前切断连接）。
当 `security.public.port` 与 `server.port` 不同（或传 `-NoReload`）时脚本自动切到 `serve.py`：
它在同一进程绑定所有监听端口，因此内存中的 Key 缓存、限流桶、登录锁定在两区共享。
若 `.env` 里残留旧的端口变量，脚本仅将其当作一次性迁移提示并警告要求删除，显式配置优先。

### 测试 24B: 只转发声明过的 API 路径（2026-11-10）

对应 `tests/smoke_test.py` 的 `[10b]` / `[10c]` 分组、`tests/verify_security.py` 的
`test_admin_endpoints_never_opens_a_path` /
`test_public_credential_is_a_zone_rule_not_a_route_rule` /
`test_cors_preflight_is_bodyless_and_precedes_the_key_check`，以及 `tests/test_gateway_new.py` 的
`[3]` 分组。可转发集合来自 `proxy.FORWARDABLE_PATHS` + `FORWARDABLE_PREFIXES`（代码常量，不是配置），
公网暴露面来自 `security.public.path_allowlist`，公网凭据检查在 `app/bridge.py` 里与路径判断分成两步。

| 场景 | 期望 |
|---|---|
| 集合内路径 | `api/generate`、`api/chat`、`api/embed`、`api/embeddings`、`api/show`、`api/version`、`v1/chat/completions`、`v1/completions`、`v1/embeddings` 与模型管理 `api/pull`/`api/push`/`api/create`/`api/delete`/`api/copy` 均可转发；`api/blobs/<digest>` 按前缀命中，裸 `api/blobs` 不算路径 |
| 网关自己回答的路径 | `api/tags`、`api/ps`、`v1/models`、`v1/rerank`、`admin/login` 等永不出现在上游请求里（`is_forwardable()` 为假） |
| 未声明路径（内网端口） | **404** `Endpoint '/<path>' is not part of this gateway's API…`，不再是「上游碰巧提供就透传」；GET 与 POST 一样 |
| 未声明路径（公网端口） | 先由 `path_allowlist` 回答 **404** `Not found.`，带着有效 Key 也一样 |
| 列进白名单但网关不提供的路径 | 公网仍然是 proxy 自己那句 **404**：列出路径不等于发明路径 |
| `security.blocked_paths` | 只对确实是网关 API 的路径产生 **403**（例：屏蔽 `api/show` → 403）；未知路径先 404，屏蔽它没有变化 |
| `admin_endpoints` 三种模式 | `deny` 拦 `api/pull`/`api/delete`/`api/blobs/*`；`readonly` 放行 `pull`、仍拦 blobs；`allow` 连 `api/blobs/*` 一起转发（修复前 blobs 在 `allow` 下仍被拦） |
| `admin_endpoints` 与白名单的关系 | 对 Ollama 端点，模式**不打开路径**：`allow` + 未列出的 `api/pull` → 404；`allow` + 列出 `POST ^/api/pull$` 与 `^/api/blobs/.*$` → 通过管理员过滤（非 403）；内网分区从不应用该模式 |
| `allow` 打开公网管理面 | 公网端口 `admin_endpoints: allow`：`/admin/` 与 `/v1/management/*` 不再 404，而是路由到真实处理器且**不参考** `path_allowlist`（前缀规则，在 `bridge_denial()` 里先于允许清单判定）；`/admin/` 静态外壳与 `POST /v1/management/auth/login` 免凭据，其余 `/v1/management/*` 无会话 Cookie → 401、登录后 → 200，网关 API Key 仍然是 401；`deny` / `readonly` 下这三个请求全为 404；内网端口上该字段完全不生效 |
| `routing.strict: true` 与无 `model` 的调用 | 带 `name` 的管理调用（`api/pull`）照常落到 `default_upstream` 并转发，不再死在 `No upstream configured for model ''`；未知**具名**模型仍然 404 |
| 公网凭据检查归属分区而非路由 | 注册一个**完全没有鉴权依赖**的路由并列入白名单：公网端口无 Key → **401**（带 `WWW-Authenticate: Bearer`）、无效 Key → **403**、有效 Key → 200；同一内网路由在 `allow_anonymous: true` 下仍 200（内网不做分区级凭据检查，控制台静态资源要保持匿名） |
| CORS 预检 | 列表内 origin 的 `OPTIONS` → **204** 且 **body 长度为 0**（修复前用 `JSONResponse({}, 204)`，Starlette 对 204 不写 `content-length` 却仍然发送 body，uvicorn 抛 `Response content longer than Content-Length` 直接断线）；未列出的 origin 既拿不到 204 也拿不到 CORS 头；公网端口未列出的 `OPTIONS` → **404**（带有效 Key 也一样），列出后无需任何凭据即 204；公网 **401** 仍带 `Access-Control-Allow-Origin`，浏览器读到的是拒绝而不是模糊的 CORS 故障 |
| 文档可见性 | `/openapi.json` 含 `POST /api/generate`、`/api/chat`、`/v1/chat/completions`、`/v1/completions`、`/api/embed`、`/v1/embeddings` 六个带 schema 的路由：请求 schema 里有 `model`（`api/embed` 另有 `input`+`truncate`，`/api/chat` 有 `messages`），200 分别声明 NDJSON+JSON / JSON+SSE / JSON，并列出 404 与 405 |
| 只允许 POST 的路由 | 上述 8 条（含 `/v1/messages`、`/v1/message`、`/v1/rerank` 等）在 GET / PUT 上一律 **405** 且响应头 `Allow: POST`，而不是被 catch-all 透传 |

前置条件：公网分区的判定仍只看内核监听 socket（见测试 24A）。分区级的凭据检查排在限流之后、CORS
预检应答之后，所以被限速的调用者先看到 429，预检请求不带凭据也能拿到 204。但预检对第 1 步来说仍然
是一条 method+path：公网端口要让浏览器可用，必须在 `path_allowlist` 里为对应路径补上 `OPTIONS:` 条目。

---

## 错误处理

### 测试 25: 无效的 JSON 请求体

**请求:**

```bash
curl http://localhost:8000/v1/rerank \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '这是无效的 JSON'
```

**响应 (400 Bad Request):**

```json
{
  "detail": "Request body must be valid JSON."
}
```

### 测试 26: 缺少必需字段

**请求:**

```bash
curl http://localhost:8000/v1/rerank \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3-reranker:4b"}'
```

**响应 (400 Bad Request):**

```json
{
  "detail": "'query' must not be empty."
}
```

### 浨�型不存在

**请求:**

```bash
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nonexistent-model",
    "messages": [{"role": "user", "content": "hi"}]
  }'
```

**响应 (404 Not Found):**

```json
{
  "detail": "No upstream configured for model 'nonexistent-model'. Add it to an upstream's 'models' list, or set 'default_upstream'."
}
```

---

## Docker 部署测试

### 测试 27: Docker Compose 部署

**步骤:**

```bash
# 1. 准备配置
cp data/config.example.yaml data/config.yaml
cp .env.example .env

# 2. 编辑配置
# 编辑 data/config.yaml 添加真实的上游实例
# 编辑 .env 设置真实的 API 密钥

# 3. 启动服务
docker run -d --name llm-hub -p 8888:8000 -v ./data:/data --env-file .env bruce1977/llm-hub:latest

# 4. 验证
curl http://localhost:8888/health
curl http://localhost:8888/v1/models -H "Authorization: Bearer YOUR_API_KEY"
```

### 测试 28: Docker 部署

**步骤:**

```bash
# 方式一: 从 Docker Hub 拉取 (推荐)
docker pull bruce1977/llm-hub:latest

# 运行容器
docker run -d --name llm-hub \
  -p 8888:8000 \
  -v ./data:/data \
  --env-file .env \
  --restart unless-stopped \
  bruce1977/llm-hub:latest

# 方式二: 从源码构建
docker build -t llm-hub:latest .

# 运行容器
docker run -d --name llm-hub \
  -p 8888:8000 \
  -v ./data:/data \
  --env-file .env \
  llm-hub:latest

# 查看日志
docker logs -f llm-hub

# 验证
curl http://localhost:8888/health
```

---

## 性能测试

### 测试 29: 并发请求

**使用 ab (Apache Bench):**

```bash
# 安装 ab
# Ubuntu/Debian: sudo apt-get install apache2-utils
# macOS: brew install httpd

# 测试并发请求
ab -n 100 -c 10 -H "Authorization: Bearer sk-gateway-example-key" \
  http://localhost:8000/v1/models
```

### 测试 29A: 推理并发闸门（排队不丢请求，2026-11-11）

`server.max_concurrency` 不是限流：超出的请求排队等空位，不会 429 拒绝。
自动化验证在 `tests/verify_security.py::test_inference_concurrency_gate`：

- 两个出站池：推理池容量 == `max_concurrency`，控制面池（System One / web search /
  探针 / 管理台）不受闸门约束；
- 用一个故意 sleep 的本地 HTTP 后端同时发 8 个请求：全部返回 200，后端观测到的
  峰值并发 ≤ `max_concurrency`；
- 把 `upstream_queue_timeout` 调到 0.05 秒、闸门设为 2，排不到的请求抛
  `httpx.PoolTimeout`；网关在缓冲与流式两条路径上都映射为 503 + `Retry-After: 5`。

手工验证（先设 `max_concurrency: 2` 并重启网关）：

```bash
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/generate \
    -d '{"model":"qwen3:8b","prompt":"hi","stream":false}' &
done; wait
```

6 个请求应全部完成，只有 2 个同时在 Ollama 侧执行；把 `upstream_queue_timeout`
调小后，等不到空位的请求返回 503 并带 `Retry-After: 5`。

### 测试 29B: 已移除的安全配置项（2026-11-11）

`allow_query_api_key` 与 `trusted_proxies` 都已删除，行为固定为「Key 只在请求头」
与「转发头一律不采信」。自动化验证在
`tests/verify_security.py::test_retired_security_knobs`：

- `SecurityConfig` 的字段表、`build_default_config()`、`config.example.yaml` 都不含这两个键；
- 旧配置文件里残留（甚至带非默认值）仍能正常加载，但字段不会复活，启动时给出告警；
- 公网区 `?api_key=<合法 Key>` -> `401`，`Authorization: Bearer` -> `200`；
- 伪造的 `X-Forwarded-For` / `X-Real-IP` 链不改变 `peer_ip()`，也不改变分区。

### 测试 29C: `security.blocked_ips`（请求直接中断，2026-11-12）

需求是"配置的 IP 发起的请求直接中断掉"，不是返回一个 HTTP 错误码。自动化验证见
`tests/verify_security.py` 的 `test_blocked_ips_matching` /
`test_blocked_ips_config_and_hot_reload` / `test_blocked_ips_interrupts_request` /
`test_blocked_ips_socket_drop`：

- **规则解析**：单 IP、CIDR、IPv4 / IPv6、`203.0.113.7/24` 简写、`192.0.2.5` 前后空白、
  重复项合并；非法条目与非字符串条目告警后跳过；`configure()` 是替换而非追加，
  `reset()` 清空；IPv4 与 IPv6 家族互不误判。
- **配置与热加载**：`SecurityConfig` / 默认配置 / 示例配置都有 `blocked_ips: []`；
  `ConfigManager` 每次成功加载都发布不可变快照；`reload()` 换名单无需重启；配置被校验器
  拒绝时保留上一份可用名单。启动日志区分「TCP 层已装载」（INFO）与「仅 ASGI 兜底」（WARNING）。
- **ASGI 兜底**：`BlockedIPMiddleware` 必须是**最外层**用户中间件（Starlette 的
  `add_middleware` 是 `insert(0)` 且逆序建栈，所以要在模块末尾注册）。被拒请求无 body、
  只输出一条 `deny <ip>`、请求记录表零行；允许的请求正常 200 且带真实对端 IP。
- **真实 socket 验证**：起一个真的 `uvicorn.Server`（`http=GuardProtocol`），从
  `127.0.0.2` 绑定源地址发请求 —— 收到 **0 字节**、ASGI 命中数为 0、
  `uvicorn.access` 无该 IP 的行、没有 "ASGI callable returned without starting response"
  或 500 兜底、仅一条 `deny 127.0.0.2`；从 `127.0.0.1` 发请求得到 `HTTP/1.1 200`
  且产生访问日志与请求记录。同时验证 CLI 的 `--http ip_guard_protocol:GuardProtocol`
  导入路径、`serve.py` 的 `http=GuardProtocol` 与 `start-local.ps1` 的 `--http` 参数。
- **零流量热加载**：服务器运行中把名单从 `127.0.0.2` 改成 `127.0.0.3`，约 1 秒后
  旧 IP 恢复 200、新 IP 被丢弃；后台配置轮询让被拒连接无需任何其它请求也能生效。

手工验证（Linux / macOS，先设 `security.blocked_ips: ["127.0.0.2"]` 并以 `python serve.py` 启动）：

```bash
curl --interface 127.0.0.2 -v http://127.0.0.1:8000/health   # 连接直接被断开，无 HTTP 响应
curl http://127.0.0.1:8000/health                            # 200 {"ok":true}
# 服务端日志只多一行：WARNING | llm_hub | deny 127.0.0.2
```

再编辑 `config.yaml` 把名单改成 `["127.0.0.3"]`，不重启、不发其它请求，等约 1 秒后重复：
`127.0.0.2` 恢复 200，`127.0.0.3` 被丢弃。

### 测试 29D: Settings「请求」请求记录清理（2026-11-12）

自动化验证见 `tests/test_management.py` 的 `[16] request log cleanup`（33 项断言），使用独立的
临时 SQLite 库，避免污染前面各组写入的请求记录。

- **鉴权与分区**：未登录时 GET 预览 / POST 删除都是 401；把 TestClient 的端口声明为公网后，
  即便配置了 allowlist，cleanup 仍在管理面红线内（401 / 404）。两个路由必须注册在
  `/requests/{id}` **之前**，否则 Starlette 会先把 `cleanup` 当成 id 解析并返回 422。
- **截止日期**：纯 `YYYY-MM-DD` 归一化为 UTC `00:00:00`；带时区的
  `2026-10-12T08:00:00+08:00` 归一化到同一时刻；空值 / 无法解析 / 未来时间 → 400；
  POST 缺 `before` 或空串 → 422。
- **预览**：`count` 是 `ts < cutoff` 的行数，`total` 是全表行数，`oldest` / `newest` 是待删
  区间的最早 / 最晚时间，`db_bytes` 是 SQLite 主文件 + WAL 大小。
- **删除**：严格 `<`，恰好等于截止时刻的行保留；待删的旧行里含一条 `/health`（非 API 行，
  列表与图表隐藏它）也要被清掉；返回 `deleted` / `remaining` / `oldest` /
  `db_bytes_before` / `db_bytes_after`。第二次预览 `count=0`。
- **VACUUM**：默认 `false`；无行可删时即便传 `true` 也不执行；插入一条旧行后再删则
  `vacuum=true`，且删除数与剩余数仍正确。批量插入、每小时保留期清理与手工删除共用
  `RequestLog._io_lock`，避免大 `DELETE` 与 `executemany` 撞出 `database is locked`。

手工验证（先登录拿 Cookie）：

```bash
BASE=http://127.0.0.1:8000/v1/management
curl -c jar -X POST "$BASE/auth/login" -H 'Content-Type: application/json' -d '{"password":"..."}'
curl -b jar "$BASE/requests/cleanup?before=2026-10-12"
# {"before":"2026-10-12T00:00:00","count":123,"total":456,"oldest":"...","newest":"...","db_bytes":...}
curl -b jar -X POST "$BASE/requests/cleanup" -H 'Content-Type: application/json' \
  -d '{"before":"2026-10-12","vacuum":true}'
# {"ok":true,"deleted":123,"remaining":333,...}
```

界面路径：Settings → 请求（第三个 Tab，非 config 编辑器、隐藏保存栏）→ 默认一个月前并自动
预览 → 点「清理记录」→ 确认弹窗显示准确条数 → 成功后刷新预览，并展示删除数 / 剩余数 / 数据库大小变化。

### 测试 29E: Settings「安全」配置编辑（2026-11-12）

自动化验证见 `tests/test_management.py` 的 `[17] settings security node (PUT /config)`
（24 项断言），与 `[16]` 共用临时 config 文件，`finally` 恢复原文并 `manager.reload()`。

- **鉴权**：未登录时 GET /config 与 PUT /config 都是 **401**；管理会话登录后正常读写。
- **完整往返**：GET /config 返回 `security` 全部 7 个顶层键（`internal` / `public` /
  `blocked_paths` / `blocked_ips` / `cors_origins` / `max_body_bytes` / `rate_limit`），
  两个区与限流器的子字段也都在。PUT 后磁盘上的 `security` 仍是完整节点（含默认值），
  标量、列表、`path_allowlist` 的「方法 → 正则数组」映射逐项一致；其余顶层段
  （`upstreams` / `rerank` / `systemone` / `websearch` / `server` / `routing` 等）原样保留。
- **热加载**：`manager.reload()` 后 `manager.config.security` 立即反映新值，
  `blocked_ips` 快照同步进 `ip_guard`；非法 IP 条目被跳过，合法 IP 命中。
- **校验**：`public.port=70000`、`admin_endpoints=bogus`、`rate_limit.by=bogus`、
  `path_allowlist` 含未知方法 `TRACE`、`per_minute="many"` 都返回 **422**，
  `detail.segments` 里明确是 `security`，且磁盘文件逐字节不变。
- **空 PUT**：`{"config": {}}` 不清空 `security`。
- **旧配置回归**：磁盘放一个顶层 `auth: {enabled: true}` 并删掉
  `security.internal.allow_anonymous`，加载后该字段由 `auth.enabled` 推断为 false；
  界面读回、改成 true 并保存，完整写回使 `Config._migrate_auth_node` 不再覆盖它，
  磁盘与运行时都为 true；顶层 `auth` 原样保留（其它顶层键 verbatim）。

手工验证（先登录拿 Cookie）：

```bash
BASE=http://127.0.0.1:8000/v1/management
curl -c jar -X POST "$BASE/auth/login" -H 'Content-Type: application/json' -d '{"password":"..."}'
curl -b jar "$BASE/config" | python -m json.tool | head -40
curl -b jar -X PUT "$BASE/config" -H 'Content-Type: application/json' \
  -d '{"config":{"security":{...完整节点...}}}'
# {"ok":true,"reloaded":true}
```

界面路径：Settings → 安全 → 修改任意字段 → 「保存配置」→ 确认弹窗提示整体覆盖 `security` 节点。
`blocked_ips` 非空时表单给出自锁警告；IP / 路径 / 限流约 1 秒热加载，`public.port` 需重启。
其中「允许外网访问管理后台」（`admin_endpoints`）控制公网 socket 是否开放 `/admin` 与
`/v1/management/*`：`deny` 全拒、`readonly` 仅放行模型 pull 而后台仍关闭、`allow` 开放后台；
管理路由仍需在 `path_allowlist` 放行并登录管理会话。

### 测试 29F: Settings「服务」server 节点编辑（2026-11-12）

自动化验证见 `tests/test_management.py` 的 `[18] settings server node (PUT /config)`
（12 项断言），与 `[17]` 共用临时 config 文件，`finally` 恢复原文并 `manager.reload()`。

- **完整往返**：GET /config 返回 `server` 全部 7 个字段（`host` / `port` / `log_level` /
  `request_timeout` / `max_concurrency` / `upstream_queue_timeout` / `upstream_keepalive_expiry`）。
  PUT 后磁盘上的 `server` 仍是完整节点（含默认值），逐字段一致；其余顶层段原样保留。
- **热加载**：`manager.reload()` 后 `manager.config.server` 立即反映新值。
- **校验**：`port=70000`、`max_concurrency=0`、`request_timeout="many"`、
  `upstream_queue_timeout=-1` 都返回 **422**，`detail.segments` 里明确是 `server`，磁盘不变。
- **空 PUT**：`{"config": {}}` 不清空 `server`。

界面路径：Settings → 服务（第一个 Tab，默认页）→ 修改监听 / 日志 / 超时 / 并发闸门 →
「保存配置」→ 确认弹窗提示整体覆盖 `server` 节点且需重启网关 → 成功后提示端口 / 日志 / 超时 /
并发的变更待重启生效。

### 测试 30: 流式响应延迟

**测试脚本 (Python):**

```python
import time
import httpx

url = "http://localhost:8000/api/chat"
headers = {"Authorization": "Bearer sk-gateway-example-key"}
payload = {"model": "qwen3:8b", "messages": [{"role": "user", "content": "你好"}], "stream": True}

start = time.time()
with httpx.stream("POST", url, json=payload, headers=headers) as response:
    first_token_time = None
    for line in response.iter_lines():
        if line and first_token_time is None:
            first_token_time = time.time() - start
            print(f"首 token 延迟: {first_token_time:.3f}s")
            break
```

---

## 联网检索

> 端点由 `config.yaml` 的 `websearch` 段驱动，检索方是一个 `type: "opencode"` 的上游；
> 请求体与上游响应均原样透传。以下响应为对 `vm.gateway:11430` 的实测结果。

### 测试 31: 关键词检索网页段落

**请求:**

```bash
curl -X POST http://localhost:8000/v1/tools/web-search \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "Ollama keep_alive 默认值", "top_k": 2}'
```

**响应 (200 OK):**

```json
{
  "query": "Ollama keep_alive 默认值",
  "model": "ollama-local/qwen3.5:9b",
  "engine": "opencode-websearch",
  "truncated": false,
  "answer": null,
  "text": "Documentation Index\nFetch the complete documentation index at: /llms.txt\n\nI want the model to stay loaded on the GPUs for an extended period of time, ...",
  "sources": [
    "https://docs.ollama.com/faq",
    "https://www.reddit.com/r/ollama/comments/1cnxnrv/how_to_set_keepalive_1_on_ollama_linux/"
  ],
  "results": [
    {
      "title": "FAQ",
      "url": "https://docs.ollama.com/faq",
      "snippet": "Alternatively, you can change the amount of time all models are loaded into memory by setting the OLLAMA_KEEP_ALIVE environment variable when starting the ...",
      "paragraphs": ["Documentation IndexFetch the complete documentation index at: /llms.txt"]
    }
  ]
}
```

**断言:**
- `200`，`engine` 非空，`results` 或 `text` 至少一项有内容
- `query` 与请求一致（透传，网关不改写）
- `synthesize: true` 时 `answer` 才非 null

### 测试 32: 联网检索的鉴权、方法与参数校验

**请求与期望:**

```bash
# 缺少 API Key -> 401
curl -X POST http://localhost:8000/v1/tools/web-search \
  -H "Content-Type: application/json" -d '{"query": "test"}'

# 只接受 POST -> 405
curl http://localhost:8000/v1/tools/web-search \
  -H "Authorization: Bearer sk-gateway-example-key"

# 缺少 query -> 上游校验打回 400（响应体原样透传）
curl -X POST http://localhost:8000/v1/tools/web-search \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" -d '{"top_k": 2}'
# {"error":{"message":"body must have required property 'query'", ...}}

# 请求体不是 JSON 对象 -> 网关直接 400
curl -X POST http://localhost:8000/v1/tools/web-search \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" -d 'not json'
```

**开关与故障映射:**

| 场景 | 状态码 |
|------|--------|
| `websearch.enabled = false` | 404 |
| `websearch.upstream` 指向上游名但不存在 | 503 |
| 上游不可达 | 502 |
| 上游超过 `websearch.timeout` | 504 |

### 测试 33: 检索上游同时提供模型与聊天

`type: "opencode"` 的上游除检索外，其免费模型也会进入聚合列表并可路由聊天：

```bash
# /v1/models 中可见 auto、opencode/mimo-v2.6-flash-free、opencode/deepseek-v4.1-flash-free、opencode/big-pickle
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "opencode/big-pickle", "messages": [{"role": "user", "content": "Say OK"}], "stream": false}'
```

> 上游模型 id 用斜杠分隔（`opencode/big-pickle`），`upstreams[].models` 里的通配必须写成
> `"opencode/*"`，`"opencode:*"` 匹配不到任何模型。


---

## 管理 API（Web 管理台）

> 端点统一挂在 `/v1/management` 下（实现在 `app/management.py`），由 `require_admin_session` 保护，
> **只接受登录换来的会话 Cookie**。网关 API Key 会分发给每个调用方，不能等同于管理员凭据：
> 用它访问管理接口一律 **401**（否则可读取完整配置、导出请求正文、改写上游）。
> `ADMIN_PASSWORD` 只是登录凭据，当作请求头凭据提交仍返回 **401**。未设置 `ADMIN_PASSWORD`
> 时所有接口返回 **503**；凭据缺失或错误返回 **401**。`/api-keys` 三个接口同样只认会话 Cookie。
> 同一来源连续 5 次登录失败锁定 15 分钟，返回 **429** 并带 `Retry-After`。
> 公网分区上 `/v1/management/*` 与 `/admin*` 是硬性 404，没有「豁免」这一说——全局
> `security.path_allowlist` 已移除，公网只认 `security.public.path_allowlist`。管理台前端由网关
> 在内网端口的 `/admin` 直接提供静态服务（构建产物在 `app/web`）。

### 测试 34: 登录、会话与鉴权方式

```bash
# 登录：校验用户名与密码并下发 HttpOnly 会话 Cookie（12 小时）
curl -i -X POST http://localhost:8000/v1/management/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "change-me"}'
# HTTP/1.1 200 OK
# Set-Cookie: llmhub_admin=<expiry>.<sig>; HttpOnly; SameSite=lax; Max-Age=43200
# {"ok": true}
# username 可省略，默认就是 admin

# 会话心跳（管理台刷新页面时调用）
curl http://localhost:8000/v1/management/auth/ping \
  -H "Cookie: llmhub_admin=<expiry>.<sig>"
# {"ok": true, "admin": true}

# headless / 脚本方式一：登录 + Cookie jar
curl -c cookie.txt -X POST http://localhost:8000/v1/management/auth/login \
  -H "Content-Type: application/json" -d '{"password": "change-me"}'
curl -b cookie.txt http://localhost:8000/v1/management/overview

# headless / 脚本方式二：直接带上网关 API Key（复用已有的 key，无需登录）
curl -H "Authorization: Bearer sk-gateway-example-key" http://localhost:8000/v1/management/overview
curl -H "X-API-Key: sk-gateway-example-key" http://localhost:8000/v1/management/overview

# 登出：清除 Cookie
curl -X POST http://localhost:8000/v1/management/auth/logout
# {"ok": true}
```

**断言:**
- 密码错误 -> `401`，且响应带约 100 ms 延迟（钝化在线暴力破解）；用户名不是 `admin` 同样 `401`
- 用户名比较会先 `strip()` 再转小写，所以 ` Admin ` 也能登录
- 未带 Cookie 也未带网关 Key，或会话 token 被篡改、过期 -> `401`
- 把 `ADMIN_PASSWORD` 放到 `Authorization: Bearer` / `X-API-Key` -> `401`（它只是登录凭据，
  不在网关 Key 列表里）；不在 `api_keys` 表中的随机 Key 同样 `401`
- 网关 API Key 只接受 `Authorization: Bearer` / `X-API-Key` 请求头，放在查询串
  （`?api_key=...`）-> `401`；该行为不可配置（`allow_query_api_key` 已移除）
- 管理接口在任何配置下都只认会话 Cookie：即使内网 `security.internal.allow_anonymous: true`
  让业务 API 免 Key，或首次启动时 `api_keys` 表里一行都没有（还没在 `/admin` 签发过任何 Key），
  管理接口也绝不会变成匿名可访问，网关 Key 打不开它
- 修改 `ADMIN_PASSWORD` 或进程重启（随机 seed 轮换）后旧会话立即失效；签名密钥由
  `ADMIN_PASSWORD` + session id + 进程随机 seed 派生
- Cookie 为 HttpOnly + SameSite=Lax，仅在 HTTPS 下加 `Secure`
- 未配置 `ADMIN_PASSWORD` -> 登录与所有受保护接口均为 `503`

### 测试 35: 总览与上游探测

```bash
curl -b cookie.txt http://localhost:8000/v1/management/overview
curl -b cookie.txt http://localhost:8000/v1/management/upstreams
curl -b cookie.txt http://localhost:8000/v1/management/upstreams/ollama
```

**响应 (200 OK, 节选):**

```json
{
  "kpi": {
    "upstreams_online": 1,
    "upstreams_total": 2,
    "running_models": 2,
    "vram_used_gib": 9.6,
    "vram_total_gib": 32.0,
    "requests_today": 128,
    "tokens_today": 3142,
    "p95_ms": 740,
    "error_rate": 0.0234
  },
  "upstreams": [
    {
      "name": "ollama",
      "type": "ollama",
      "base_url": "http://127.0.0.1:11434",
      "status": "online",
      "reachable": true,
      "probe_ms": 3.2,
      "loaded": 2,
      "vram_used_gib": 9.6,
      "vram_total_gib": 32.0,
      "ps_supported": true,
      "error": null
    }
  ],
  "running_models": [
    {
      "model": "qwen3:8b",
      "upstream": "ollama",
      "device": "gpu",
      "device_ratio": 1.0,
      "num_ctx": 40960,
      "size_gib": 5.2,
      "vram_gib": 5.2,
      "expires_at": "2026-11-03T12:30:00Z"
    }
  ],
  "generated_at": "2026-11-03T12:00:00"
}
```

**断言:**
- `overview` 并行探测所有上游；单个上游不可达不影响整体，仍返回 `200`
- 设备分类看 `size_vram / size`：`>= 0.999` -> `gpu`，`> 0` -> `partial`，`0` -> `cpu`
- `num_ctx` 取 `/api/ps` 的 `context_length`，缺失时回退 `num_ctx_overrides`
- 只有 `type: "ollama"` 会探测 `/api/ps`（`ps_supported: true`），其他类型只做健康检查
- `vram_total_gib` 来自 `upstreams[].vram_total_gb`；所有上游都没配时为 `null`
- `tokens_today` 是今天（UTC）已记录请求的 token 合计，优先取 `tokens`，缺失时回退 `tokens_in + tokens_out`；
  没有 usage 的行（如流式响应）不计入
- `upstreams/{name}`：名字不存在 -> `404`；探测失败 -> `503` 且响应体带 `"unreachable": true`；
  可达时额外返回 `models`（该上游在 `config.yaml` 声明的模型，`model_aliases` 短名作为显示名，前缀通配用
  `main.fetch_upstream_models()` 展开成命中的具体模型，末尾追加归属于该上游的根 `aliases:` 派生模型名）与
  `running_models`（Ollama 为 `/api/ps` 的运行态，含设备、显存/占用与别名；`opencode` 类型探测有效时列出
  它声明的全部模型，设备与显存为 `null`；派生模型行 `derived: true`，设备沿用目标模型、`size_gib`/`vram_gib`
  为 `null`，只在目标驻留或上游为可达的托管 provider 时才出现）

### 测试 36: 配置投影与凭据脱敏

```bash
curl -b cookie.txt http://localhost:8000/v1/management/models/llm
curl -b cookie.txt http://localhost:8000/v1/management/models/rerank
curl -b cookie.txt http://localhost:8000/v1/management/models/systemone
```

**断言:**
- `models/llm`：行只来自 `config.yaml` 的 `upstreams[].models`——声明的普通名字原样成行（实例里装了
  但没声明的模型不列，网关本来就路由不到它们），前缀通配（`qwen3:*`、`opencode/*`）复用
  `main.fetch_upstream_models()`（60s 缓存）展开成命中的具体模型并标 `wildcard: true`；展开不到任何
  模型时保留通配本身成一行且 `running: false`。根 `aliases:` 的派生模型是**独立一行**，紧跟在它的目标
  模型之后（目标未被任何上游声明时排在末尾）。`models[]` 每项含 `model`（真实 id，派生行则是别名名）、
  `display`（`model_aliases` 短名，否则即声明名）、`alias`、`derived`、`upstream`、`type`、`wildcard`、
  `running`、`device`、`device_ratio`、`num_ctx`、`size_gib`、`vram_gib`，派生行另带 `target` / `params` /
  `description`；只有驻留行才有设备与内存，`opencode` 上游探测有效
  即其声明的模型全部 `running: true`、`device: "cloud"`、内存为 `null`，离线上游只贡献声明名且 `running: false`；
  派生行的设备沿用目标模型的运行态、`size_gib`/`vram_gib` 恒为 `null`。
  另返回 `aliases[]`（别名 / `kind`（`rename` 或 `derived`）/ 目标 / 上游 / `params` / `description`）与
  `num_ctx_overrides`
- `overview`：`running_models` 列表在驻留模型与托管模型之后追加根 `aliases:` 的派生行，而
  `kpi.running_models` 只数前者，所以「列表比 KPI 多几行」是设计——派生模型与目标共用同一个实例，
  计入 KPI 或显存都会重复计数
- `models/rerank`：`enabled`、`endpoint`、`mode`、`upstream_api`（`auto` / `generate` / `chat`）、
  `on_error`（`score_zero` / `fail`）、`models[]`、`scoring`、`limits`，外加可直接复制的
  `request_example` / `response_example`
- `models/systemone`：`backend`、`path`、`linked_model`、`api_key_env`、`timeout` 及请求示例
- 任何投影都不回显明文凭据：上游 / rerank / systemone 的 Key 显示为 `••••••`，环境变量缺失时为 `null`
- 这些接口均为 GET 只读投影，不提供写操作

### 测试 37: 请求日志查询与详情

```bash
# 最近记录（默认 page=1、page_size=15）
curl -b cookie.txt "http://localhost:8000/v1/management/requests"

# 组合筛选（时间 + 上游 + 模型 + 状态档位 + 路径）
curl -b cookie.txt "http://localhost:8000/v1/management/requests?start=2026-11-03T00:00:00&end=2026-11-03T23:59:59&upstream=ollama&model=qwen3:8b&status=5xx&path=/v1/chat/completions&page=2&page_size=50"

# 单条详情（含请求体 / 响应体）
curl -b cookie.txt "http://localhost:8000/v1/management/requests/42"
```

**响应 (200 OK):**

```json
{
  "stats": {"count": 2, "p50_ms": 1180, "p95_ms": 2400, "p99_ms": 2400, "error_rate": 0.5},
  "rows": [
    {"id": 42, "ts": "2026-11-03T12:02:11", "ts_end": "2026-11-03T12:02:13", "method": "POST",
     "path": "/v1/chat/completions", "query": null, "requested_model": "q35", "model": "qwen3:8b",
     "upstream": "ollama", "status": 200, "duration_ms": 2400, "ttfb_ms": 210, "stream": 1,
     "tokens_in": 5, "tokens_out": 7, "tokens": 12}
  ],
  "pagination": {"page": 1, "page_size": 15, "total": 2, "pages": 1},
  "request_log": {"enabled": true, "db": "/data/llmhub.db", "retention_days": 30, "dropped": 0}
}
```

**断言:**
- 每笔 API 调用恰好一行：方法、路径、模型、上游、状态码、耗时、TTFT、是否流式、输入/输出/总 token 数、
  请求/响应大小、客户端 IP、错误信息；流式请求在流结束后落库
- **代理看不到的调用也补齐**（对应 `tests/verify_security.py::test_request_log_coverage`）：
  经代理的调用认领 ASGI scope 标记，独立 handler 与被 middleware 拒绝的请求由 middleware 写**同一行**，
  不会被重复写两行。独立 handler 分两类：
  - **有实质内容的工具 API**（`/v1/tools/web-search`、`/v1/systemone`、`/v1/rerank`、`/v1/messages`）：
    handler 通过 `request_log.attach_request_detail()` 上报已解析的 model/upstream/原始请求体，
    middleware 再统一捕获响应体并合并成**富字段行**——`requested_model`、`model`、`upstream`、
    `request_body`、`response_body`、`tokens_in/out/tokens`、`stream` 都有值，详情抽屉能直接看 req/res。
    `/v1/messages` 的流式 SSE 响应体中，token 用量由生成器 `finally` 在末块到达后补记。
    web-search 无自身模型时 `model` 取 body 内的 `model`（若有），模型列由前端回退到稳定工具标签/上游。
  - **纯发现探针**（`/v1/models`、`/api/tags`、`/api/ps`、`/health`）与**被 middleware 拒绝的请求**
    （公网未带 Key 的 401、白名单外的 404）：仍写**仅元数据行**，body/token/upstream/model 列为 `NULL`，
    `error` 保存拒绝原因（如 `Missing API key: POST /v1/systemone`）。这样控制台高频轮询 `/v1/models`
    不会用 8 KB 响应快照塞满 SQLite；详情富字段只在 handler 主动上报时才采集。
  `/v1/management/*`、`/admin/*`、docs 与静态资源 **完全不写库**（控制台轮询不会污染表），
  `HEAD` 与 `OPTIONS` 也不记录
- `status` 只接受档位 `2xx` / `3xx` / `4xx` / `5xx`（传具体数字 -> `400`）
- `page < 1`、`page_size` 不在 `1..200`、`status` 非法 -> `400`；时间戳非法或 `start > end` -> `400`
- 记录不存在 -> `404`
- **只统计 API 调用**：列表、`stats`、分页 `total` 一律带
  `path LIKE '/v1/%' OR path LIKE '/api/%'`（`request_log.API_PATH_SCOPE`）。非 API 路径（如 `/metrics-probe`、
  `/health`）的请求照常 `200` 并**确实写进库**（直接查 sqlite 能看到行数增加），但列表里查不到、
  用 `?path=metrics-probe` 也搜不出来；而它的 `/requests/{id}` 深链仍然 `200`（详情接口故意不加这个条件）。
  注意这里说的是**非 API 业务路径**；management / console / docs / 静态资源是另一回事——它们根本不写库
- 请求/响应体各截断到 8 KB，并用 `request_body_truncated` / `response_body_truncated` 标记；
  `api_key`、`authorization`、`password` 等字段递归替换为 `***`

### 测试 38: 统计、分布与失败归因

```bash
curl -b cookie.txt "http://localhost:8000/v1/management/stats/timeseries?bucket=hour&start=2026-11-03T00:00:00&end=2026-11-03T23:59:59"
curl -b cookie.txt "http://localhost:8000/v1/management/stats/distribution?group_by=upstream"
curl -b cookie.txt "http://localhost:8000/v1/management/stats/failures?start=2026-11-03T00:00:00&end=2026-11-03T23:59:59"
```

**响应 (200 OK, 节选):**

```json
{
  "bucket": "hour",
  "start": "2026-11-03T00:00:00",
  "end": "2026-11-03T23:59:59",
  "points": [{"ts": "2026-11-03T12:00", "count": 18, "errors": 1, "p95": 2400}]
}
```

```json
{
  "group_by": "upstream",
  "start": "2026-11-02T12:00:00",
  "end": "2026-11-03T12:00:00",
  "items": [{"key": "ollama", "count": 14}, {"key": "lmstudio", "count": 5}, {"key": "-", "count": 1}]
}

// group_by=status：2xx -> ok，>= 500 -> failed，其余（含 4xx）-> warning
{
  "group_by": "status", "start": "...", "end": "...",
  "items": [{"key": "ok", "count": 14}, {"key": "warning", "count": 5}, {"key": "failed", "count": 1}]
}

// group_by=model：display 是别名短名，key 保持解析后的真实 id
{
  "group_by": "model", "start": "...", "end": "...",
  "items": [{"key": "bge-m3:latest", "count": 460, "display": "bge-m3"},
            {"key": "qwen3.5:9b", "count": 348, "display": "qwen3.5:9b"}]
}
```

```json
{
  "total": 20,
  "ok": 19,
  "error_rate": 0.05,
  "by_code": {"502": 1},
  "by_upstream": {"ollama": 1},
  "samples": [{"ts": "2026-11-03T12:02:11", "path": "/v1/chat/completions",
               "model": "qwen3:8b", "upstream": "ollama", "status": 502, "duration_ms": 30}]
}
```

**断言:**
- `bucket` 只接受 `hour` / `day`（其他 -> `400`）；`start` / `end` 缺省为最近 24 小时
- 时间桶格式：`hour` 为 `YYYY-MM-DDTHH:00`，`day` 为 `YYYY-MM-DD`
- P95 为**桶内**分位数（窗口函数），不是整段时间的全局值
- `distribution` 的 `group_by` 只接受 `upstream` / `model` / `status`（其他 -> `400`），按 `count` 倒序返回
- `group_by=status` 分**三桶**：`ok`（2xx）、`failed`（`>= 500`）、`warning`（3xx / 4xx / NULL / 其他）。
  `failed` 仍与错误率、趋势图错误柱、失败统计共用 `>= 500` 阈值；三桶相加等于同期 `/requests` 的
  `pagination.total`。4xx 现在落在 `warning`，不再被算作 `ok`（这是「饼图只有一片绿色 ok」的根因）
- `group_by=model` 的每项额外带 `display`：`model_aliases` 短名优先，没有别名就等于真实 id；
  `key` 仍是解析后的真实模型 id，所以筛选与详情链接不受影响。根 `aliases:` 派生模型是独立模型，
  不会反过来给它的目标改名
- 未命中上游的行（被拦截 403 / 过大 413 / 无可用上游 404）在 `upstream`、`model` 分组里计入 `-`，
  因此 `items` 相加恒等于同期 `/requests` 的 `pagination.total`
- `failures` 只统计 `status >= 500` 的请求，按状态码与上游聚合，另附最近 20 条样本
- 数据存于同一 SQLite 库（默认 `llmhub.db`，可用 `REQUEST_LOG_DB` 覆盖），
  `REQUEST_LOG_RETENTION_DAYS` 控制保留天数（`0` = 永久，默认 30 天，按小时清理）

### 测试 39: 明细字段与入库开关

`requests` 表逐笔记录三段信息（时间 / 请求参数 / 响应结果）：

| 分组 | 字段 |
|------|------|
| 时间 | `ts` 到达、`ts_end` 完成（流式在流关闭时写入）、`duration_ms`、`ttfb_ms`；`ts`/`ts_end` 精确到秒，亚秒耗时以 `duration_ms` 为准 |
| 请求 | `method`、`path`、`query`、`requested_model`（客户端写的名字）、`model`（别名解析后）、`request_body`、`req_size`、`stream`、`client_ip` |
| 结果 | `status`、`upstream`、`response_body`、`res_size`、`tokens_in`、`tokens_out`、`tokens`、`error` |

token 用量按方向拆成两列入库：`tokens_in` 是输入（提示）、`tokens_out` 是输出（生成），
`tokens` 仍是总量。来源有两处——OpenAI 兼容响应的 `usage.prompt_tokens` / `completion_tokens`
（总量优先取上游回报的 `total_tokens`），以及 Ollama 原生响应的 `prompt_eval_count` / `eval_count`。

```bash
# 别名调用的详情：requested_model 保留别名，model 是解析后的真实模型
curl -b cookie.txt "http://localhost:8000/v1/management/requests/<id>"
# {"requested_model": "q35", "model": "qwen3:8b", "ts_end": "...", "query": null, ...}

# GET 带查询串时，path 与 query 分开存
curl -b cookie.txt "http://localhost:8000/api/version?format=json" -H "Authorization: Bearer <API_KEY>"
curl -b cookie.txt "http://localhost:8000/v1/management/requests/<id>"   # "query": "format=json"

# token 拆分：Ollama 原生（prompt_eval_count / eval_count）与 OpenAI 兼容（usage.*）两个来源
curl -X POST http://localhost:8000/api/chat -H "Authorization: Bearer <API_KEY>" \
  -d '{"model":"qwen3:8b","stream":false,"messages":[{"role":"user","content":"ping"}]}'
curl -b cookie.txt "http://localhost:8000/v1/management/requests/<id>"
# 数值取自上游回报：`tests/test_management.py` 的 mock 回 5+7=12，OpenAI 兼容分支回 4+8=12
```

> 无 `model` 的 GET 无法路由，网关回 `404` 且 `error` 写明原因，但这一行**仍会入库**
> （本地 Ollama 实测：`status=404`、`requested_model=null`、`query="format=json&verbose=1"`）。

**入库开关（`config.yaml` -> `request_log.enabled`，默认 `true`，代理与安全 middleware 逐请求共同读取）:**

```bash
# 关闭：把 "request_log": {"enabled": false} 写进 config.yaml 并保存
# 配置的 mtime 每秒最多比对一次，等约 1 秒再验证下一条请求
curl -b cookie.txt http://localhost:8000/v1/management/overview
# {"request_log": {"enabled": false, "db": "/data/llmhub.db", "retention_days": 30, "dropped": 0}, ...}
```

**断言:**
- `tokens_in` / `tokens_out` 与 `tokens` 三列同时入库，OpenAI 兼容与 Ollama 原生两个来源都能拆开
- 上游没有回报用量时三列都是 `NULL`（与真实的 `0` 可区分）；`/requests` 的 `rows` 也带这三列
- 关闭后请求照常转发（仍返回 `200`），但代理与 middleware 两个写入点都不再写库，明细总数不增加，
  请求体也不再采集
- `/overview` 与 `/requests` 的 `request_log.enabled` 同步回报 `false`，前端可提示「日志已停用」
- 改回 `true` 后，越过约 1 秒的热加载窗口，下一条请求即入库（无需重启容器）
- 自动化用例里直接调用 `ConfigManager.reload()` 跳过该窗口；对真实服务需等待或先发一次探测请求
- 旧版本留下的 `llmhub.db` 在启动时自动 `ALTER TABLE ADD COLUMN` 补上新列，历史行不受影响
- 独立 handler 分两类：工具 API（`/v1/tools/web-search`、`/v1/systemone`、`/v1/rerank`、`/v1/messages`）
  写**富字段行**（model/upstream/req/res/token 由 handler 经 `request.state.request_log_detail` 上报，
  middleware 合并后一次落库，含流式响应）；纯发现探针（`/v1/models`、`/api/tags`、`/api/ps`、`/health`）
  与 middleware 拒绝的请求写**仅元数据行**：`request_body` / `response_body` / `tokens` / `upstream` /
  `model` 为 `NULL`，拒绝请求 `error` 带原因（如 401 的 `Missing API key`）。两类都能在控制台
  Requests 页看到；`/v1/management/*` 与 `/admin/*` 不写库
- **`HEAD` 与 `OPTIONS` 只转发不记录**：两者都由 catch-all 的 `HTTP_METHODS` 接受（已无全局方法白名单），
  `HEAD` 在内网端口正常返回 `200`、`OPTIONS` 预检由 middleware 回 `204`，但 `/requests` 里都没有这一行
  （同样的 `GET` 会有一行）；公网分区只有 `security.public.path_allowlist` 显式列出对应
  方法+路径时才会存在，否则 404。只有 `POST_ONLY_PATHS` 里的业务路径对非 POST
  方法仍返回 `405`（带 `Allow: POST`），这与旧的方法白名单无关

### 测试 40: Web Search 配置投影

```bash
curl -b cookie.txt http://localhost:8000/v1/management/tools/websearch
```

**响应 (200 OK):**

```json
{
  "enabled": true,
  "endpoint": "/v1/tools/web-search",
  "upstream": "opencode",
  "upstream_type": "opencode",
  "path": "v1/web-search",
  "base_url": "https://api.opencode.ai",
  "api_key_env": "OPENCODE_API_KEY",
  "api_key": "••••••",
  "timeout": 300.0,
  "provider_configured": true
}
```

**断言:**
- `upstream` / `base_url` / `upstream_type` 由 `Config.websearch_upstream_obj()` 解析：
  `websearch.upstream` 显式点名时用该上游，留空时取第一个 `type: "opencode"` 上游——
  投影与真实转发共用同一个解析器，管理台不会显示网关其实不会调的地址
- `endpoint` 固定为 `/v1/tools/web-search`（对外路径不可配置），`path` 才是提供方侧的路径
- `api_key` 只在 `api_key_env` 命名的环境变量确有值时返回掩码 `••••••`，缺失时为 `null`；
  响应里永不出现密钥明文，`api_key_env` 只回显变量名
- 点名了不存在的上游，或配置里根本没有 `opencode` 上游：`provider_configured: false`、
  `base_url` / `upstream_type` 为 `null`（前者 `upstream` 保留配置的名字），实际调用会返回 **503**
- `enabled: false` 时投影照常返回配置（前端据此提示「联网检索已停用」），实际调用返回 **404**
- 与其他 `/v1/management/*` 一样是只读 GET，凭据同样是会话 Cookie 或网关 API Key

### 测试 41: Web 管理台静态服务（`/admin`）

前端在 `web/` 里开发，`npm run build` 把产物写进 **`app/web`**（该目录随仓库提交）。它与模块同级，
所以本地 `app/web` 和镜像 `/app/web` 走同一个解析路径，网关把它当作静态文件挂在 `/admin`，
镜像里不需要 node。

```bash
# 1. 无尾斜杠 -> 307 跳转到 /admin/
curl -i http://localhost:8000/admin -X GET
# HTTP/1.1 307 Temporary Redirect
# location: /admin/

# 2. 壳层（登录页要能匿名打开，所以静态资源本身不鉴权）
curl -i http://localhost:8000/admin/
# HTTP/1.1 200 OK
# content-type: text/html; charset=utf-8
# cache-control: no-cache
# content-security-policy: ... frame-ancestors 'self' ...
# 响应体含 <div id="app">

# 3. SPA 深链：前端路由的路径没有对应文件，回退到同一个壳层
curl -i http://localhost:8000/admin/requests

# 4. 内容哈希命名的构建产物：可长期缓存
curl -i http://localhost:8000/admin/assets/index-<hash>.js
# cache-control: public, max-age=31536000, immutable

# 5. 目录穿越：拿不到构建目录之外的文件，只会回退到壳层
curl -i "http://localhost:8000/admin/%2e%2e%2f%2e%2e%2fapp%2fmain.py"

# 6. 尚未构建（app/web/index.html 不存在）
curl -i http://localhost:8000/admin/
# HTTP/1.1 503 Service Unavailable
# {"detail":"Web console is not built: run `npm install && npm run build` in web/, or use an image that ships app/web."}

# 7. 壳层匿名，但页面内每一次数据请求仍然要求凭据
curl -i http://localhost:8000/v1/management/overview
# HTTP/1.1 401 Unauthorized
```

**断言:**
- `/admin` 307 -> `/admin/`；`/admin/` 返回 `index.html`，`content-type` 为 `text/html`
- 壳层带 `Cache-Control: no-cache`（改版后不会被旧 HTML 卡住），`assets/*` 带 `immutable`
- 壳层沿用 `/admin` 作用域的 CSP（含 `frame-ancestors 'self'`，禁止被外站 iframe 嵌入）
- 任何非文件路径（SPA 深链、编码后的 `../`）都回退到壳层，`resolve()` + `is_relative_to()`
  保证 `..` 无法跳出 `app/web`，绝不会把同包里的 `app/main.py` 之类的源码发出去
- `app/web` 缺失时是 **503** + 构建提示，而不是 404 或空白页——前端把 503 与「未配置
  `ADMIN_PASSWORD`」区分处理
- 静态壳层故意不鉴权（否则打不开登录页），`/v1/management/*` 每一次调用仍受 `require_admin_session` 保护
- `/admin` 静态壳层只在内网端口提供，公网分区 `/admin*`、`/v1/management/*` 一律 404；
  它不受 `security.public.path_allowlist` 约束（那是公网分区的清单），`DELETE /admin/` 返回
  `405` 只因为静态路由只注册了 GET
- 前端只在 **401** 时登出并跳回登录页；`503` 视为业务答复（上游不可达 / 密码未配置 / 未构建）

### 测试 42: 接口测试窗口（`api-test`）与 `api_models`

管理台每个「API 用法」标签页里，「请求参数」标题右侧的**测试**按钮会打开浮层里的测试窗口，
请求交给网关自己的服务执行（实现在 `app/api_test.py`，路由在 `app/management.py`）。

```bash
# 1. 每类 API 能选哪些模型、默认值、是否需要额外密钥
curl -b cookie.txt http://localhost:8000/v1/management/api-test/models

# 2. 用根 `aliases:` 里的派生模型发一笔 /api/generate（qwen3:8b-nothink → qwen3:8b + think:false）
curl -b cookie.txt http://localhost:8000/v1/management/api-test \
  -H "Content-Type: application/json" \
  -d '{"kind": "generate", "body": {"model": "qwen3:8b-nothink", "prompt": "hi", "stream": true}}'

# 3. 白名单之外的 kind
curl -b cookie.txt http://localhost:8000/v1/management/api-test \
  -H "Content-Type: application/json" -d '{"kind": "delete", "body": {}}'
```

**`/api-test/models` 响应 (200 OK，节选):**

```json
{
  "kinds": {
    "generate": {
      "kind": "generate", "method": "POST", "endpoint": "/api/generate",
      "family": "generate", "model_required": true, "free_model": false,
      "enabled": true, "source": "upstreams[].models",
      "models": [
        { "model": "qwen3:8b", "display": "q35", "alias": "q35", "upstream": "ollama-gpu",
          "type": "ollama", "running": true, "device": "gpu", "api_key": null, "unknown": false },
        { "model": "qwen3.5:4b-nothink", "display": "qwen3.5:4b-nothink", "derived": true,
          "target": "qwen3.5:4b", "running": true, "device": "gpu", "unknown": false }
      ],
      "default_model": "qwen3:8b", "api_key": null, "api_key_env": null
    },
    "websearch": {
      "kind": "websearch", "endpoint": "/v1/tools/web-search", "family": "websearch",
      "model_required": false, "free_model": true, "enabled": true,
      "source": "upstreams[].models (search provider)",
      "default_model": null, "api_key": "••••••", "api_key_env": "OPENCODE_API_KEY"
    }
  },
  "max_response_bytes": 131072
}
```

**`POST /api-test` 响应 (200 OK，节选):**

```json
{
  "kind": "generate", "method": "POST", "endpoint": "/api/generate", "stream": true,
  "requested_model": "qwen3:8b-nothink", "model": "qwen3:8b", "upstream": "ollama-gpu",
  "injected": { "think": false },
  "status": 200, "ok": true, "duration_ms": 812, "ttft_ms": 260,
  "content_type": "application/x-ndjson",
  "body": null, "text": "{\"model\":\"qwen3:8b\",\"response\":\"h\"}\n...",
  "truncated": false, "error": null
}
```

**断言:**

- `KINDS` 是**封闭白名单**：`generate` / `chat` / `embed_ollama` / `embed_openai` / `rerank` /
  `systemone` / `websearch`。其它 `kind` → **400** 并列出可选值，绝不会变成一个任意 URL 的转发器
- `model_required` 的家族没给 `model`（或给了空白串）→ 路由层 **400** `'…' needs a model; pick one
  in the selector.`；`systemone` / `websearch` 的 `model` 可省略，所以它们 `model_required: false`
  且 `free_model: true`（前端选择框允许直接输入配置里没有的名字）
- 请求走的是网关自己的服务：别名解析与参数注入（`injected`）、`num_ctx_overrides` 收敛、被屏蔽的
  管理路径、请求明细落库都与真实客户端一致；`requested_model` / `model` / `upstream` 把「实际发出去
  的是什么」摊开写清楚，而不是让调用方猜
- `stream` 是**网关选定的传输方式**（省略 `stream` 时即 `STREAM_BY_DEFAULT` 的结果），与请求明细里
  的 `stream` 列同一个值，网关不会往请求体里注入这个字段；响应到底真是分块回来的，看
  `content_type`（`application/x-ndjson` / `text/event-stream`），前端只在它命中时才显示流式与首
  token 徽标
- 端点自己给出的拒绝答复（如上游不可达的 **502**、功能已停用的 **404**）作为**结果数据**返回，
  信封里带着它的 `status` 与 `body`，而不是让 `/api-test` 报错——测试窗口显示的正是外部客户端会看到
  的东西
- 响应体上限 `max_response_bytes = 128 KB`，超出时 `truncated: true`；流式响应读完即关闭生成器，
  所以下游那一笔的日志行照常写入
- 凭据不外泄：需要额外签名的家族只返回固定掩码 `••••••` 与变量名 `api_key_env`；被代理调用的上游
  如果是请求头签名，掩码逐模型行给（`models[].api_key`），值永远不出服务器
- `api_models.<family>` 决定选项：留空按 `rerank.models` → `systemone.model` → 可调用池的顺序推导，
  `source` 回报这次用了哪个来源；`models[].display` 有别名显示别名、没有就显示配置名，但选择框里
  **只显示这个名字**，上游/设备/运行状态放在悬停提示；写了没有任何上游服务的名字时该行保留并标
  `unknown: true`（配置错误要显示出来而不是被藏掉）
- **非空列表是封闭集合**：`source = api_models.<family>`，既不被 `websearch.upstream` 的 provider
  过滤改写（`test_management.py` 断言 `websearch: ["qwen3:8b"]` 时 `source` 仍是
  `api_models.websearch`），也把该家族的 `free_model` 置为 `false`。`- ''` / 纯空白行被忽略，
  不会出现空白 `<option>`；整列都是占位符时等同留空，继续走推导
- 页内其它模型展示同样读这份数据：`Models · Embedding` / `Models · SystemOne` 的「可用模型」表、
  `Models · LLM` 三个请求/响应示例里的 `model`、`Tools · Web Search` 响应示例里的 `model`，
  都不再写死模型名，改配置后同步变化
- `api_models.generate` 为空时，被**其它家族显式认领**的名字（连同解析后的目标名）不会出现在
  generate/chat 里：`api_models.embed` / `rerank` / `systemone` 与 `rerank.models` 都算，空字符串
  条目不算认领；匹配忽略结尾的 `:latest`（`systemone: ["tev1"]` 能挡住声明的 `tev1:latest`）。
  `api_models.websearch` **不算认领**——搜索端点本质是聊天透传，托管 provider 的路由模型
  （`opencode/auto`）与它的别名必须继续出现在 chat 里（`test_management.py` 用
  `websearch: ["qwen3:8b"]` 断言 `qwen3:8b`、`q35` 仍在，同时 websearch 家族自己的列表照常是
  `qwen3:8b`）。兜底来源（如 `systemone.model`）同样不算认领。显式写出来的
  `api_models.generate` 原样使用
- `/api/generate` 与 `/api/embed` 是 **Ollama 原生方言**，`lmstudio` / `opencode` 上游只实现 `/v1`
  一侧，所以这两个家族按空列表推导时只保留 `type: "ollama"` 的行：`opencode` 模型出现在
  `chat` / `embed_openai` 的选择框里，但不会出现在 `generate` / `embed_ollama` 里（否则一发就是
  上游 404）。`test_management.py` 用一个 `type: "lmstudio"`、`models: ["lm-model"]` 的上游断言
  两个方向；显式 `api_models` 列表仍按原样返回，写错的名字作为可见的配置问题保留
- 表单**一打开就带着初始值**：有 `def` 的字段直接填入，`select` 没有默认值时显示「（未设置）」
  而不是空白；「恢复默认值」重新填入、「清空」清空全部字段与模型选择。**有 `def` 的布尔参数
  渲染为复选框，勾选/不勾选都显式发送 `true` / `false`**（不勾选 ≠ 不发送），没有 `def` 的布尔
  仍是三态下拉（System One 的可选 `stream` 因此还能留空）。两个 LLM 窗口的 `stream` 默认 `false`，
  请求体预览里能直接看到 `"stream": false`；generate 窗口的 `think` 默认 `false`，chat 窗口的
  `reasoning_effort` 默认不设置（`none` 在 Ollama 上关闭思考）。其余清空的字段不会进入请求体
- **模型用真正的下拉框选择**：打开时选中 `default_model`，选项即该家族的候选列表；**留空推导**的
  `free_model` 家族（System One / Web Search）在下拉末尾多一项「自定义模型…」，选中后在下方出现手输
  输入框（自动聚焦、带 `<datalist>` 候选），再选回列表里的名字就收起；一旦 `api_models` 明确列出该
  家族的名字，`free_model=false`，这一项消失。旧版把默认值填在输入框里，
  Chromium 按框内文字过滤 datalist，其它候选根本看不见
- **模型可以清除**：字段旁的 ×（或 select 里的空白项）把它清掉后，请求体预览里没有 `model`；
  `model_required` 的家族在发送前被本地拦下（「请先选择模型」），`free_model` 家族则把请求
  发出去交给端点决定；「恢复默认值」重新选中 `default_model`，「清空」也清掉模型
- System One 配置投影里的 `request_example` 用 **object** 形式的 `choice.criteria`——Ollama 0.40+
  收到数组会回 **400**（`"criteria" must be a non-empty object`），laya-server 两种都接受，所以
  object 是两个后端都能直接跑通的写法
- 五个页面共用 `useApiTestContext()` 的模块级缓存，`/api-test/models` 一次探测所有上游，所以切换
  标签页不会反复压上游；只有首次进入与点击「重新探测」才会再取一次

### 测试 43: 调用方 API Key 管理（`api-keys`）

```bash
# 列表：只有登录会话 Cookie 能看（网关 Key 不行）
curl -b cookie.txt http://localhost:8000/v1/management/api-keys

# 生成：明文只在这一次返回
curl -b cookie.txt -X POST http://localhost:8000/v1/management/api-keys \
  -H "Content-Type: application/json" -d '{"label": "backend-a"}'
# HTTP/1.1 201 Created
# {"item": {"id": 3, "masked": "sk-hub-7Kp2…Q9df", "label": "backend-a",
#            "source": "console", "created_at": "2026-11-03T12:04:07Z"},
#  "secret": "sk-hub-7Kp2…（47 字符，只有这里有）"}

# 撤销（界面上唯一能做的删除）
curl -b cookie.txt -X DELETE http://localhost:8000/v1/management/api-keys/3

# 新生成的 Key 立刻能当调用方凭据用
curl -H "Authorization: Bearer sk-hub-7Kp2…" http://localhost:8000/api/tags
```

**断言:**

- 三条路由都只认会话 Cookie（`require_admin_session`）：先 `logout` 再拿网关 API Key 分别请求
  `GET` / `POST` / `DELETE /api-keys` -> **全部 401**，登录回来才 `200`。理由写死在
  `admin_auth.py` 的注释里：网关 Key 是任何客户端都已持有的东西，接受它就等于让客户端给自己发权限
- 环境变量不再提供 Key：`GATEWAY_API_KEYS` 已移除，空表是正常的首次启动状态（需要 Key 的请求 401），
  第一把 Key 由 `POST /api-keys` 签出，列表里 `source` 为 `console`、`label` 必填（旧库中
  `source: "file"` 的行只是历史标记，不会再新增）
- 任何接口都取不到明文：`GET /api-keys` 与新建响应的 `item` 里只有掩码，随后所有列表都不含那串明文
  （`secret not in json.dumps(...)`），表里连 `hash` 列都不出现在响应字段中——
  `listing` 每行只有 `id` / `masked` / `label` / `source` / `created_at` 五列
- 格式固定：`sk-hub-` + 40 位 base62（`string.ascii_letters + digits`），总长 `KEY_LENGTH = 47`；
  连取 50 个互不重复。掩码是 `sk-hub-` + 头 4 + `…` + 尾 4；旧式（非该前缀）Key 掩码头 8 尾 4，
  长度 ≤ 12 的整体用 `••••••` 覆盖，绝不泄露任何一位
- 存储是 `sha256(明文)` 的小写十六进制（`hash_key`：同值同摘要、异值异摘要、长度 64）。
  校验走 `ApiKeyStore.verify()` 比较摘要，所以数据库整包被拷走也拿不到可用 Key；
  改一位明文的 Key 立刻验不过
- `label` 进库前 `strip()`；路由要求非空（空白串 -> **400**
  `A label is required, so the key is recognisable later.`），超过 `LABEL_MAX_CHARS = 100`
  由 pydantic 拦下 -> **422**。`ApiKeyStore.create()` 本身允许空标签并按上限截断，
  这一层是给导入和将来复用的余量
- 新建的 Key 立即生效（无需重启）：`GET /api/tags` 与 `/v1/management/auth/ping` 都 `200`——
  每次写表都会重载进程内的 `frozenset` 缓存，请求路径上不碰 sqlite
- 撤销：真实 id -> **200** `{"deleted": id}`；同一个 id 再删 -> **404**；不存在的 id -> **404**；
  列表里该行消失。撤销后（已登出，只测 Key 本身）该 Key 走网关 -> **403**、走管理接口 -> **401**，
  而最早导入的环境 Key 仍然 `200`
- 未带任何凭据时列表关闭 -> **401**
- 单元测试（`test_management.py` 第 [14] 段）覆盖 `ApiKeyStore` 本体：空表 `count() == 0`、
  `import_plaintext` 跳过重复（三个含一个重样的输入只进 2 行，重复导入同一批增加 0 行）、
  导入行 `source == "file"` 且能验过、`unserved_env_keys()` 只报未服务的 Key 且返回掩码、
  `delete()` 对真实 id 返回 `True` 并让 `verify()` 立刻转 `False`、未知 id 返回 `False`、
  列表按新到旧排序且 `count()` 与表行数一致


