# LLM Hub API 文档

## 目录

- [基础信息](#基础信息)
- [认证方式](#认证方式)
- [端点列表](#端点列表)
  - [健康检查](#健康检查)
  - [实例探测](#实例探测)
  - [模型列表](#模型列表)
  - [重排序](#重排序)
  - [聊天补全](#聊天补全)
  - [文本补全](#文本补全)
  - [嵌入向量](#嵌入向量)
  - [模型管理](#模型管理)
- [错误响应](#错误响应)

---

## 基础信息

| 项目 | 值 |
|------|-----|
| 基础 URL | `http://localhost:8888` |
| 协议 | HTTP/HTTPS |
| 数据格式 | JSON |
| 字符编码 | UTF-8 |

---

## 认证方式

大部分端点需要 API 密钥认证，支持以下两种方式：

```bash
# 方式一：Authorization 头
-H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"

# 方式二：X-API-Key 头
-H "X-API-Key: sk-gateway-9f2c8a1b4d5e6f70"
```

**例外：** `/health` 端点默认无需认证。

---

## 端点列表

### 健康检查

| 项目 | 值 |
|------|-----|
| 路径 | `/health`, `/healthz`, `/` |
| 方法 | `GET`, `POST`, `PUT`, `DELETE`, ... |
| 认证 | ❌ 不需要 |
| 说明 | 轻量级健康检查，不消耗任何资源 |

**请求示例：**

```bash
curl http://localhost:8888/health
```

**响应 (200 OK)：**

```json
{
  "ok": true
}
```

---

### 实例探测

| 项目 | 值 |
|------|-----|
| 路径 | `/probe` |
| 方法 | `GET`, `POST`, ... |
| 认证 | ✅ 需要 |
| 说明 | 探测所有上游实例的连接状态和模型可用性 |

**请求示例：**

```bash
curl http://localhost:8888/probe \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
```

**响应 (200 OK)：**

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
          "name": "qwen3:*",
          "declared": true,
          "available": true,
          "wildcard": true
        }
      ]
    }
  ]
}
```

**响应字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `upstreams[].name` | string | 上游实例名称 |
| `upstreams[].type` | string | 后端类型：`ollama` 或 `lmstudio` |
| `upstreams[].base_url` | string | 上游基础 URL |
| `upstreams[].reachable` | boolean | 是否可达 |
| `upstreams[].version` | string | Ollama 版本（仅 Ollama） |
| `upstreams[].error` | string | 错误信息（不可达时） |
| `upstreams[].models[].name` | string | 模型名称 |
| `upstreams[].models[].declared` | boolean | 是否在配置中声明 |
| `upstreams[].models[].available` | boolean | 是否在上游中可用 |
| `upstreams[].models[].wildcard` | boolean | 是否为通配符声明 |

---

### 模型列表

| 项目 | 值 |
|------|-----|
| 路径 | `/v1/models`, `/models` |
| 方法 | `GET` |
| 认证 | ✅ 需要 |
| 说明 | 返回所有可用模型（包括别名） |

**请求示例：**

```bash
curl http://localhost:8888/v1/models \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
```

**响应 (200 OK)：**

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

---

### Ollama 原生模型列表

| 项目 | 值 |
|------|-----|
| 路径 | `/api/tags` |
| 方法 | `GET` |
| 认证 | ✅ 需要 |
| 说明 | 返回 Ollama 格式的模型列表 |

**请求示例：**

```bash
curl http://localhost:8888/api/tags \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
```

**响应 (200 OK)：**

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

### 重排序

| 项目 | 值 |
|------|-----|
| 路径 | `/v1/rerank`, `/rerank`, `/api/rerank` |
| 方法 | `POST` |
| 认证 | ✅ 需要 |
| 说明 | 对文档进行重排序（支持 logprobs 和 embedding 模式） |

**请求示例：**

```bash
curl http://localhost:8888/v1/rerank \
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

**请求参数：**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model` | string | ✅ | 重排序模型名称 |
| `query` | string | ✅ | 查询文本 |
| `documents` | array | ✅ | 待排序文档列表（`string` 或 `{"text": "..."}`） |
| `top_n` | integer | ❌ | 返回前 N 个结果 |
| `return_documents` | boolean | ❌ | 是否返回文档内容（默认取配置 `rerank.return_documents`） |
| `instruction` | string | ❌ | 覆盖默认指令 |

兼容别名（便于对接不同客户端 / 网关）：

| 别名 | 对应字段 | 说明 |
|------|----------|------|
| `model_name` / `model_id` | `model` | 优先级：`model` > `model_name` > `model_id` |
| `input` | `query` | 支持字符串或单元素数组 |
| `texts` / `passages` | `documents` | 文档列表的另一种写法 |

> 未传 `model` 时，会回退到配置 `rerank.models` 中的第一个模型。

**响应 (200 OK)：**

```json
{
  "id": "rerank-abc123def456",
  "object": "list",
  "model": "qwen3-reranker:4b",
  "results": [
    {
      "index": 0,
      "rank": 0,
      "relevance_score": 0.91234567,
      "document": {
        "text": "手冲咖啡需要90°C左右的水温"
      }
    },
    {
      "index": 2,
      "rank": 1,
      "relevance_score": 0.63456789,
      "document": {
        "text": "研磨度影响萃取速度"
      }
    }
  ],
  "usage": {
    "rerank_count": 3,
    "returned_count": 2,
    "failed_count": 0
  },
  "meta": {
    "mode": "logprobs",
    "upstream": "ollama",
    "model": "qwen3-reranker:4b",
    "total_documents": 3,
    "returned_documents": 2,
    "failed_documents": 0,
    "normalized": false,
    "took_ms": 412.35
  }
}
```

**打分模式：**

| 模式 | 适用模型 | 原理 |
|------|----------|------|
| `logprobs`（默认） | Qwen3-Reranker 等生成式重排模型 | 用 yes/no 提示词取**首个回答 token** 的 logprobs，做二分类 softmax 得到概率 |
| `embedding` | bge-m3 等向量模型 | 一次性批量向量化 query + 文档，用余弦相似度打分 |

`relevance_score` 默认是**绝对概率**（0~1，跨请求可比）。若希望把本批次分数拉伸到 `[0,1]`，可设置 `rerank.normalize: true`。

**关键配置项（`rerank` 段）：**

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `mode` / `model_modes` | `logprobs` | 全局模式 / 按模型覆盖，如 `{"bge-m3:latest": "embedding"}` |
| `upstream_api` | `auto` | Ollama 走 `/api/generate`（`raw_prompt=true`，避免注入 `<think>`），OpenAI 兼容走 `/v1/chat/completions`；可强制 `chat` |
| `top_logprobs` | `20` | 向上游请求的候选 token 数，用于计算 yes/no 概率 |
| `max_tokens` | `2` | 只生成少量 token 判定 yes/no |
| `normalize` | `false` | 是否做 min-max 归一化 |
| `return_documents` | `true` | 响应是否回带文档原文 |
| `on_error` | `score_zero` | 单篇打分失败时：记 0 分或返回 502（全部失败一律 502） |
| `max_documents` / `max_document_chars` | `0` | 文档数量 / 单篇长度上限，0 表示不限制 |
| `embedding_query_prefix` / `embedding_clamp` | `""` / `true` | embedding 模式的 query 前缀、余弦值裁剪到 `[0,1]` |

---

### 聊天补全

#### OpenAI 兼容格式

| 项目 | 值 |
|------|-----|
| 路径 | `/v1/chat/completions` |
| 方法 | `POST` |
| 认证 | ✅ 需要 |
| 说明 | OpenAI 兼容的聊天补全接口 |

**请求示例：**

```bash
curl http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [
      {"role": "system", "content": "你是一个有帮助的助手"},
      {"role": "user", "content": "什么是机器学习？"}
    ],
    "temperature": 0.7,
    "max_tokens": 500,
    "stream": false
  }'
```

**请求参数：**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model` | string | ✅ | 模型名称 |
| `messages` | array | ✅ | 消息列表 |
| `temperature` | float | ❌ | 温度参数 (0-2) |
| `max_tokens` | integer | ❌ | 最大生成 token 数 |
| `stream` | boolean | ❌ | 是否流式返回 |
| `tools` | array | ❌ | 工具定义列表 |

**响应 (200 OK)：**

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

#### Ollama 原生格式

| 项目 | 值 |
|------|-----|
| 路径 | `/api/chat` |
| 方法 | `POST` |
| 认证 | ✅ 需要 |
| 说明 | Ollama 原生聊天接口 |

**请求示例：**

```bash
curl http://localhost:8888/api/chat \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [
      {"role": "user", "content": "你好"}
    ],
    "stream": false
  }'
```

**响应 (200 OK)：**

```json
{
  "model": "qwen3:8b",
  "message": {
    "role": "assistant",
    "content": "你好！有什么可以帮你的？"
  },
  "done": true,
  "total_duration": 1234567890,
  "eval_count": 42
}
```

---

### 文本补全

| 项目 | 值 |
|------|-----|
| 路径 | `/v1/completions` |
| 方法 | `POST` |
| 认证 | ✅ 需要 |
| 说明 | OpenAI 兼容的文本补全接口 |

#### Ollama 原生格式

| 项目 | 值 |
|------|-----|
| 路径 | `/api/generate` |
| 方法 | `POST` |
| 认证 | ✅ 需要 |
| 说明 | Ollama 原生生成接口 |

**请求示例：**

```bash
curl http://localhost:8888/api/generate \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "prompt": "写一首关于编程的诗：",
    "stream": false
  }'
```

**响应 (200 OK)：**

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

### 嵌入向量

| 项目 | 值 |
|------|-----|
| 路径 | `/api/embed`, `/v1/embeddings` |
| 方法 | `POST` |
| 认证 | ✅ 需要 |
| 说明 | 生成文本嵌入向量 |

**请求示例：**

```bash
curl http://localhost:8888/api/embed \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-m3:latest",
    "input": ["你好世界", "机器学习入门"]
  }'
```

**请求参数：**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model` | string | ✅ | 嵌入模型名称 |
| `input` | string/array | ✅ | 输入文本（字符串或数组） |

**响应 (200 OK)：**

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

### 模型管理

以下端点默认被阻止（`security.admin_endpoints: "deny"`），需要修改配置才能使用。

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/pull` | `POST` | 拉取模型 |
| `/api/push` | `POST` | 推送模型 |
| `/api/delete` | `POST` | 删除模型 |
| `/api/create` | `POST` | 创建模型 |
| `/api/copy` | `POST` | 复制模型 |
| `/api/show` | `POST` | 显示模型信息 |
| `/api/blobs/*` | `GET/POST/DELETE` | Blob 操作 |

**请求示例（需要配置 `security.admin_endpoints: "allow"`）：**

```bash
curl http://localhost:8888/api/pull \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{"name": "llama3"}'
```

---

## 流式响应

### OpenAI 格式 (SSE)

设置 `"stream": true` 启用流式响应：

```bash
curl http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

**响应格式 (Server-Sent Events)：**

```
data: {"id":"chatcmpl-abc","choices":[{"delta":{"role":"assistant","content":""},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"你"},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"好"},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"finish_reason":"stop","index":0}]}

data: [DONE]
```

### Ollama 格式 (NDJSON)

```bash
curl http://localhost:8888/api/chat \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

**响应格式 (NDJSON)：**

```json
{"model":"qwen3:8b","message":{"role":"assistant","content":"你"},"done":false}
{"model":"qwen3:8b","message":{"role":"assistant","content":"好"},"done":false}
{"model":"qwen3:8b","done":true,"done_reason":"stop"}
```

---

## 错误响应

所有错误响应遵循统一格式：

```json
{
  "detail": "错误描述信息"
}
```

### 错误状态码

| 状态码 | 错误类型 | 说明 |
|--------|----------|------|
| `400` | Bad Request | 无效 JSON、缺少必填字段或请求格式错误 |
| `401` | Unauthorized | 缺少 API 密钥 |
| `403` | Forbidden | API 密钥无效、IP 不在白名单或端点被阻止 |
| `404` | Not Found | 未知端点或模型未找到 |
| `405` | Method Not Allowed | 不支持的 HTTP 方法 |
| `413` | Request Entity Too Large | 请求体超过大小限制 (默认 32MB) |
| `422` | Unprocessable Entity | 请求体验证失败 |
| `502` | Bad Gateway | 上游不可达或返回错误 |
| `504` | Gateway Timeout | 上游响应超时 |

### 错误示例

**缺少 API 密钥 (401)：**

```bash
curl http://localhost:8888/v1/models
```

```json
{
  "detail": "Missing API key. Provide it via 'Authorization: Bearer <key>' or 'X-API-Key: <key>' header."
}
```

**无效的 API 密钥 (403)：**

```bash
curl http://localhost:8888/v1/models -H "Authorization: Bearer wrong-key"
```

```json
{
  "detail": "Invalid API key."
}
```

**模型未找到 (404)：**

```bash
curl http://localhost:8888/api/chat \
  -H "Authorization: Bearer sk-key" \
  -d '{"model":"unknown","messages":[{"role":"user","content":"hi"}]}'
```

```json
{
  "detail": "No upstream configured for model 'unknown'. Add it to an upstream's 'models' list, or set 'default_upstream'."
}
```

**端点被阻止 (403)：**

```bash
curl http://localhost:8888/api/delete \
  -H "Authorization: Bearer sk-key" \
  -d '{"name":"model"}'
```

```json
{
  "detail": "Endpoint '/api/delete' is disabled on this public gateway."
}
```

**请求体过大 (413)：**

```json
{
  "detail": "Request body too large."
}
```

**上游不可达 (502)：**

```json
{
  "detail": "Upstream 'local-main' is unreachable: Connection refused"
}
```

**上游超时 (504)：**

```json
{
  "detail": "Upstream 'local-main' timed out after 600s."
}
```

---

## 别名参数注入

通过配置别名，可以自动替换模型名称并注入参数。

**配置示例：**

```json
{
  "aliases": {
    "qwen3.5:4b-nothink": {
      "model": "qwen3.5:4b",
      "params": {"think": false}
    }
  }
}
```

**使用示例：**

```bash
# 使用别名（自动映射到 qwen3.5:4b 并注入 think=false）
curl http://localhost:8888/api/chat \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -d '{
    "model": "qwen3.5:4b-nothink",
    "messages": [{"role": "user", "content": "1+1=?"}],
    "stream": false
  }'
```

---

## 工具调用

支持 OpenAI 格式的工具调用（tool_calls）。

**请求示例：**

```bash
curl http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "读取 main.py"}],
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

**响应示例：**

```json
{
  "choices": [
    {
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
