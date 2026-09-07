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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
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
  -H "X-API-Key: sk-gateway-9f2c8a1b4d5e6f70"
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
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
cp .example.env .env

# 2. 编辑配置
# 编辑 data/config.json 添加真实的上游实例
# 编辑 .env 设置真实的 API 密钥

# 3. 启动服务
docker compose up -d

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
ab -n 100 -c 10 -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  http://localhost:8000/v1/models
```

### 测试 30: 流式响应延迟

**测试脚本 (Python):**

```python
import time
import httpx

url = "http://localhost:8000/api/chat"
headers = {"Authorization": "Bearer sk-gateway-9f2c8a1b4d5e6f70"}
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
