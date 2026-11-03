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

**请求:**

```bash
curl http://localhost:8000/api/generate \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "prompt": "写一首关于编程的诗：",
    "stream": false
  }'
```

**响应 (200 OK):**

```json
{
  "model": "qwen3:8b",
  "response": "代码如诗行，\n逻辑似水流...",
  "done": true,
  "total_duration": 2345678901,
  "eval_count": 50
}
```

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

### 测试 20: 阻止管理端点 (deny 模式)

**请求:**

```bash
curl http://localhost:8000/api/delete \
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

### 测试 21: 阻止模型拉取

**请求:**

```bash
curl http://localhost:8000/api/pull \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "llama3"}'
```

**响应 (403 Forbidden):**

```json
{
  "detail": "Endpoint '/api/pull' is disabled on this public gateway."
}
```

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

**配置:**

```json
{
  "security": {
    "allowed_client_ips": ["10.0.0.0/8"]
  }
}
```

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
cp data/example.config.json data/config.json
cp .env.example .env

# 2. 编辑配置
# 编辑 data/config.json 添加真实的上游实例
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

> 端点由 `config.json` 的 `websearch` 段驱动，检索方是一个 `type: "opencode"` 的上游；
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

> 端点统一挂在 `/v1/management` 下（实现在 `app/management.py`），由 `ADMIN_PASSWORD` 单独鉴权，
> 不占用 `GATEWAY_API_KEYS`。未设置 `ADMIN_PASSWORD` 时所有接口返回 **503**；凭据缺失或错误返回 **401**。
> `/v1/management` 与 `/admin` 一样豁免 `security.path_allowlist`。

### 测试 34: 登录、会话与鉴权方式

```bash
# 登录：校验密码并下发 HttpOnly 会话 Cookie（12 小时）
curl -i -X POST http://localhost:8000/v1/management/auth/login \
  -H "Content-Type: application/json" \
  -d '{"password": "change-me"}'
# HTTP/1.1 200 OK
# Set-Cookie: llmhub_admin=<expiry>.<sig>; HttpOnly; SameSite=lax; Max-Age=43200
# {"ok": true}

# 会话心跳（管理台刷新页面时调用）
curl http://localhost:8000/v1/management/auth/ping \
  -H "Cookie: llmhub_admin=<expiry>.<sig>"
# {"ok": true, "admin": true}

# headless / 脚本：同样先登录，用 Cookie jar 访问（密码不是请求凭据）
curl -c cookie.txt -X POST http://localhost:8000/v1/management/auth/login \
  -H "Content-Type: application/json" -d '{"password": "change-me"}'
curl -b cookie.txt http://localhost:8000/v1/management/overview

# 登出：清除 Cookie
curl -X POST http://localhost:8000/v1/management/auth/logout
# {"ok": true}
```

**断言:**
- 密码错误 -> `401`，且响应带约 100 ms 延迟（钝化在线暴力破解）
- 未带 Cookie，或会话 token 被篡改、过期 -> `401`
- 把密码或网关 API Key 放到 `Authorization: Bearer` / `X-API-Key` -> `401`（只认 Cookie）
- 修改 `ADMIN_PASSWORD` 后旧会话立即失效（签名密钥即密码本身）
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
- `upstreams/{name}`：名字不存在 -> `404`；探测失败 -> `503` 且响应体带 `"unreachable": true`；
  可达时额外返回 `models`（已安装模型）；模型列表拉取失败时带 `models_error`

### 测试 36: 配置投影与凭据脱敏

```bash
curl -b cookie.txt http://localhost:8000/v1/management/models/llm
curl -b cookie.txt http://localhost:8000/v1/management/models/rerank
curl -b cookie.txt http://localhost:8000/v1/management/models/systemone
```

**断言:**
- `models/llm`：`models[]`（`model`、`upstream`、`type`、`alias`、`num_ctx`、`wildcard`、`running`）、
  `aliases[]`、`num_ctx_overrides`；通配条目 `qwen3:*` 原样展示并标 `wildcard: true`
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
     "upstream": "ollama", "status": 200, "duration_ms": 2400, "ttfb_ms": 210, "stream": 1, "tokens": 12}
  ],
  "pagination": {"page": 1, "page_size": 15, "total": 2, "pages": 1},
  "request_log": {"enabled": true, "db": "/data/llmhub.db", "retention_days": 30, "dropped": 0}
}
```

**断言:**
- 每笔经网关的请求都会落库：方法、路径、模型、上游、状态码、耗时、TTFT、是否流式、token 数、
  请求/响应大小、客户端 IP、错误信息；流式请求在流结束后落库
- `status` 只接受档位 `2xx` / `3xx` / `4xx` / `5xx`（传具体数字 -> `400`）
- `page < 1`、`page_size` 不在 `1..200`、`status` 非法 -> `400`；时间戳非法或 `start > end` -> `400`
- 记录不存在 -> `404`
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
- `group_by=status` 只分两桶：`ok`（`status < 400`）与 `error`（`status >= 400`）
- 未命中上游的行（被拦截 403 / 过大 413 / 无可用上游 404）在 `upstream`、`model` 分组里计入 `-`，
  因此 `items` 相加恒等于同期 `/requests` 的 `pagination.total`
- `failures` 只统计 `status >= 400` 的请求，按状态码与上游聚合，另附最近 20 条样本
- 数据存于同一 SQLite 库（默认 `llmhub.db`，可用 `REQUEST_LOG_DB` 覆盖），
  `REQUEST_LOG_RETENTION_DAYS` 控制保留天数（`0` = 永久，默认 30 天，按小时清理）

### 测试 39: 明细字段与入库开关

`requests` 表逐笔记录三段信息（时间 / 请求参数 / 响应结果）：

| 分组 | 字段 |
|------|------|
| 时间 | `ts` 到达、`ts_end` 完成（流式在流关闭时写入）、`duration_ms`、`ttfb_ms`；`ts`/`ts_end` 精确到秒，亚秒耗时以 `duration_ms` 为准 |
| 请求 | `method`、`path`、`query`、`requested_model`（客户端写的名字）、`model`（别名解析后）、`request_body`、`req_size`、`stream`、`client_ip` |
| 结果 | `status`、`upstream`、`response_body`、`res_size`、`tokens`、`error` |

```bash
# 别名调用的详情：requested_model 保留别名，model 是解析后的真实模型
curl -b cookie.txt "http://localhost:8000/v1/management/requests/<id>"
# {"requested_model": "q35", "model": "qwen3:8b", "ts_end": "...", "query": null, ...}

# GET 带查询串时，path 与 query 分开存
curl -b cookie.txt "http://localhost:8000/api/version?format=json" -H "Authorization: Bearer <API_KEY>"
curl -b cookie.txt "http://localhost:8000/v1/management/requests/<id>"   # "query": "format=json"
```

> 无 `model` 的 GET 无法路由，网关回 `404` 且 `error` 写明原因，但这一行**仍会入库**
> （本地 Ollama 实测：`status=404`、`requested_model=null`、`query="format=json&verbose=1"`）。

**入库开关（`config.json` -> `request_log.enabled`，默认 `true`，逐请求读取）:**

```bash
# 关闭：把 "request_log": {"enabled": false} 写进 config.json 并保存
# 配置的 mtime 每秒最多比对一次，等约 1 秒再验证下一条请求
curl -b cookie.txt http://localhost:8000/v1/management/overview
# {"request_log": {"enabled": false, "db": "/data/llmhub.db", "retention_days": 30, "dropped": 0}, ...}
```

**断言:**
- 关闭后请求照常转发（仍返回 `200`），但明细总数不再增加，请求体也不再采集
- `/overview` 与 `/requests` 的 `request_log.enabled` 同步回报 `false`，前端可提示「日志已停用」
- 改回 `true` 后，越过约 1 秒的热加载窗口，下一条请求即入库（无需重启容器）
- 自动化用例里直接调用 `ConfigManager.reload()` 跳过该窗口；对真实服务需等待或先发一次探测请求
- 旧版本留下的 `llmhub.db` 在启动时自动 `ALTER TABLE ADD COLUMN` 补上新列，历史行不受影响

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
- 与其他 `/v1/management/*` 一样是只读 GET，只认管理台会话 Cookie
