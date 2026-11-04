# LLM Hub API 文档

## 目录

- [基础信息](#基础信息)
- [认证方式](#认证方式)
- [端点列表](#端点列表)
  - [健康检查](#健康检查)
  - [系统信息](#系统信息)
  - [实例探测](#实例探测)
  - [模型列表](#模型列表)
  - [Ollama 原生模型列表](#ollama-原生模型列表)
  - [实例运行状态 (Process Status)](#实例运行状态-process-status)
  - [System One 决策 API（laya-server 与 Ollama 代理）](#system-one-决策-apilaya-server-与-ollama-代理)
  - [Web Search（OpenCode 网关代理）](#web-searchopencode-网关代理)
  - [重排序](#重排序)
  - [聊天补全](#聊天补全)
  - [文本补全](#文本补全)
  - [嵌入向量](#嵌入向量)
  - [模型管理](#模型管理)
  - [管理 API（Web 管理台）](#管理-apiweb-管理台)
- [流式响应](#流式响应)
- [错误响应](#错误响应)
- [别名参数注入](#别名参数注入)
- [工具调用](#工具调用)

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
-H "Authorization: Bearer sk-gateway-example-key"

# 方式二：X-API-Key 头
-H "X-API-Key: sk-gateway-example-key"
```

**例外：** `/`、`/health` 端点默认无需认证（由 `auth.allow_anonymous_health` 控制）。

**Swagger UI：** 打开 `/docs`，**每个端点的 Parameters 区都有一个 `X-API-Key` 输入框**——点
**Try it out** 后填入 Key，点 **Execute** 时随本次请求发送（只影响该端点的这一次请求）。
也可以用右上角 **Authorize** 一次性填入 `Authorization: Bearer` 或 `X-API-Key`，此后所有请求自动携带
（`persistAuthorization` 刷新页面后依然保留）。

---

## 端点列表

### 健康检查

| 项目 | 值 |
|------|-----|
| 路径 | `/health` |
| 方法 | `GET` |
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

### 系统信息

| 项目 | 值 |
|------|-----|
| 路径 | `/` |
| 方法 | `GET` |
| 认证 | ❌ 不需要 |
| 说明 | 返回系统名称与网关版本，供客户端发现/握手 |

**请求示例：**

```bash
curl http://localhost:8888/
```

**响应 (200 OK)：**

```json
{
  "name": "LLM Hub",
  "version": "1.2.0"
}
```

---

### 实例探测

| 项目 | 值 |
|------|-----|
| 路径 | `/probe` |
| 方法 | `GET` |
| 认证 | ✅ 需要 |
| 说明 | 探测所有上游实例的连接状态和模型可用性。本端点只处理 `GET`；其它方法会落到统一转发 catch-all，被当作上游路径代理（通常得到 502/404） |

**请求示例：**

```bash
curl http://localhost:8888/probe \
  -H "Authorization: Bearer sk-gateway-example-key"
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
| 说明 | 聚合所有已配置上游的模型（含声明模型、实时拉取的模型、别名） |

**请求示例：**

```bash
curl http://localhost:8888/v1/models \
  -H "Authorization: Bearer sk-gateway-example-key"
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
  -H "Authorization: Bearer sk-gateway-example-key"
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

### 实例运行状态 (Process Status)

| 项目 | 值 |
|------|-----|
| 路径 | `/api/ps` |
| 方法 | `GET` |
| 认证 | ✅ 需要 |
| 说明 | 聚合所有 Ollama 上游的 `/api/ps`（已加载/运行中的模型）。LM Studio 等 OpenAI 兼容上游无此端点，会被自动跳过。每个条目都会标注来源 `upstream`。 |

**请求示例：**

```bash
curl http://localhost:8888/api/ps \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**响应 (200 OK)：**

```json
{
  "models": [
    {
      "name": "qwen3:8b",
      "model": "qwen3:8b",
      "size": 5368709120,
      "digest": "sha256:abc...",
      "details": { "parameter_size": "8.0B" },
      "expires_at": "2026-09-07T06:00:00Z",
      "size_vram": 5368709120,
      "upstream": "local-main"
    }
  ],
  "errors": [
    { "upstream": "ollama-ipex", "error": "connect timeout" }
  ]
}
```

> 单个上游不可达时不会中断整体响应，错误会汇总到 `errors` 字段。

---

### System One 决策 API（laya-server 与 Ollama 代理）

| 项目 | 值 |
|------|-----|
| 路径 | `/v1/systemone` |
| 方法 | `POST` |
| 认证 | ✅ 需要（网关自身的 API Key，来自 `.env` / `GATEWAY_API_KEYS`） |
| 说明 | 将请求原样转发到配置的 System One 后端（`1panel/laya-server`，或 **Ollama 0.40+**——它原生提供 `POST /v1/systemone`）。网关用 `systemone.api_key_env`（默认 `SYSTEMONE_SERVER_API_KEY`）环境变量中的密钥对后端签名，请求体与响应体均原样透传。 |

**配置（`config.json` 的 `systemone` 段）：**

```json
{
  "systemone": {
    "enabled": true,
    "backend": "http://llm.home:11435",
    "model": "tev1",
    "api_key_env": "SYSTEMONE_SERVER_API_KEY",
    "path": "v1/systemone",
    "timeout": 60.0
  }
}
```

- `backend` 指向任一 System One 后端即可，网关侧行为完全一致——纯转发，不做模型解析、不改写响应。容器内指向宿主机 Ollama 时用 `http://host.docker.internal:11435`。
- `model` **会原样出现在转发给后端的请求体里**：仅当它是具体值且调用方自身没带 `model` 时才注入；`"local"` 是**哨兵值**，表示不注入、由调用方自行指定。
- laya-server 的 `model` 为字面量枚举，**仅接受 `auto` / `english` / `multilingual` / `typed-decisions`**，其它值返回 **HTTP 422** `literal_error`。
- Ollama 的 `model` 必须是**真实模型名**：`auto` → **404** `model 'auto' not found`；缺失 → **400** `model is required`。因此后端换成 Ollama 时，把 `model` 配成实际模型（如示例中的 `tev1`）。
- 后端密钥从环境变量读取，绝不写入 `config.json`。该 `Authorization` 头对不需要鉴权的 Ollama 后端无副作用，Ollama 会直接忽略。

**请求体结构（两家后端 `state` / `questions` 均必填；差异：laya-server **拒绝未知顶层字段**，实测 Ollama 会忽略）：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `state` | object | ✅ | 自由结构，承载上下文，如 `{"message": "..."}` |
| `questions` | object | ✅ | 问题 id → 问题定义，至少 1 项 |
| `model` | string | ❌ | 模型名/路由提示，取值规则见上文；缺省时由网关按配置注入 |

`questions.<id>` 是**按 `type` 判别的联合类型**（tag 为 `type`），共三种变体。`criteria` 的形状是两家唯一的形式差异，下表同时给出兼容写法：

| `type` | 额外必填字段 | 语义 | `criteria` 兼容要求 | 响应关键字段 |
|--------|--------------|------|--------------------|--------------|
| `noul` | `instructions: string` | 二值判定 | 不需要 | `noul`（概率）；laya 另有 `confidence`、`action`，Ollama 只回 `noul` |
| `choice` | `instructions: string`、`criteria` | 多选一 | laya 接受 `string[]` 或 object；**Ollama 只接受非空 object**（数组 → 400） | `choice`、`probabilities`、`confidence` |
| `score` | `instructions: string`、`criteria` | 打分/择优 | laya 要求 `string[]`（≥2 项）；**Ollama 要求 2–10 项的数组**（object → 400） | `score`、`legend`、`probabilities`、`confidence` |

> 即：`choice` 用 object、`score` 用数组，同一份请求体在 laya-server 与 Ollama 上都能通过校验。

**请求示例（两种后端均合法）：**

```bash
curl -X POST http://localhost:8888/v1/systemone \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "state": {"message": "I was charged twice and need a refund today."},
    "questions": {
      "refund":  {"type": "noul",   "instructions": "Does the customer ask for a refund?"},
      "intent":  {"type": "choice", "instructions": "Primary intent?", "criteria": {
                    "refund": "asking money back", "cancel": "cancel account", "other": "neither"}},
      "urgency": {"type": "score",  "instructions": "How urgent?",      "criteria": ["urgent", "normal"]}
    }
  }'
```

**响应示例（Ollama 0.40+ / `model: tev1` 实测）：**

```json
{
  "model": "tev1",
  "answers": {
    "refund": {"type": "noul", "noul": 0.9734541266310229},
    "intent": {"type": "choice", "choice": "refund", "probabilities": {"refund": 0.9864521057032967, "cancel": 0.001990846275500757, "other": 0.011557048021202679}, "confidence": 0.979678158554945},
    "urgency": {"type": "score", "score": 0.13496998422642553, "legend": {"0": "urgent", "1": "normal"}, "probabilities": {"0": 0.8650300157735745, "1": 0.13496998422642553}, "confidence": 0.7300600315471489}
  },
  "usage": {"input_tokens": 369, "output_tokens": 0}
}
```

**响应示例（laya-server 实测）：**

```json
{
  "model": "laya-rl-agent",
  "answers": {
    "refund": {"type": "noul", "noul": 0.9937, "confidence": 0.9937, "action": {"act_probability": 1.0}},
    "intent": {"type": "choice", "choice": "refund", "probabilities": {"refund": 0.9999, "cancel": 0.0, "other": 0.0001}, "confidence": 0.9992}
  },
  "usage": {"input_tokens": 83, "output_tokens": 0},
  "routing": {"model": "multilingual", "repo": "/opt/models/multilingual", "reason": "explicit model='multilingual'"}
}
```

> 响应（`answers`、`model`、`usage`，以及 laya-server 独有的 `routing`）由后端原样返回，网关不做任何改写。后端返回的错误（状态码 + body）同样原样透传。

---

### Web Search（OpenCode 网关代理）

| 项目 | 值 |
|------|-----|
| 路径 | `/v1/tools/web-search` |
| 方法 | `POST` |
| 认证 | ✅ 需要（网关自身的 API Key，来自 `.env` / `GATEWAY_API_KEYS`） |
| 说明 | 关键词检索网页正文段落。请求体转发到 `websearch.upstream` 指定的上游（`websearch.path`，默认 `v1/web-search`），上游响应原样返回，网关不改写。 |

**配置（`config.json` 的 `websearch` 段 + 对应上游）：**

```json
{
  "upstreams": [
    {
      "name": "opencode",
      "type": "opencode",
      "base_url": "http://vm.gateway:11430",
      "models": ["auto", "opencode/*"],
      "timeout": 300,
      "priority": 30
    }
  ],
  "websearch": {
    "enabled": true,
    "upstream": "opencode",
    "path": "v1/web-search",
    "api_key_env": "",
    "timeout": 300.0
  }
}
```

- 检索方是一个普通的 `upstreams` 条目（`type: "opencode"`）：它的地址、超时、附加 `headers` 只配一次，同时被模型聚合（`/v1/models`、`/api/tags`）、聊天路由和 `/probe` 复用。`opencode` 类型走 OpenAI 兼容的发现接口（`GET /v1/models`，解析 `data[].id`），健康探测走 `GET /health`。
- `websearch.upstream` 留空时自动选取**第一个 `type: "opencode"` 的上游**；填了名字却找不到对应上游则返回 **503**（不会静默回退到别的上游）。
- `api_key_env` 默认空字符串 = 不签名（当前 OpenCode 网关匿名可访问）。若上游启用了密钥，把它设成变量名（如 `OPENCODE_GATEWAY_API_KEY`），网关会以 `Authorization: Bearer <值>` 调用上游；密钥只从环境变量读取，绝不写入 `config.json`。
- `model` 字段**纯透传**：调用方传什么就转什么，不传则由上游自行选择；网关不会用自己的 `aliases` 去解析它。
- `synthesize: true` 会额外驱动一次模型综合作答，耗时由上游的推理速度决定，可能远超纯检索（实测 5~15s）。上游自身对单次推理有 **300 秒** 的硬上限：绕过本网关直接压测 `vm.gateway:11430`，`synthesize` 在 311s 返回 `502 {"error":{"message":"inference timed out (>300s)","code":"opencode_unavailable"}}`。因此 `websearch.timeout` 至少保持 300（默认即 `300.0`），让上游自己的错误原样透传（**502**）而不是先被网关判成 **504**；再往上调也不会更快拿到答案。需要 `answer` 时建议把 `model` 指向更快的上游。

**请求体字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 检索关键词（非空） |
| `model` | string | ❌ | 上游侧模型 id，原样透传 |
| `top_k` | int | ❌ | 抓取页数，1~50 |
| `max_chars_per_page` | int | ❌ | 单页正文字符上限，超出则 `truncated: true` |
| `locale` | string | ❌ | 检索语言提示，如 `zh-CN` |
| `recency` | string | ❌ | 时间窗：`day` / `week` / `month` / `year` |
| `synthesize` | bool | ❌ | 额外驱动模型综合作答，返回填充 `answer`（耗时明显变长） |
| `include_sources` | bool | ❌ | 是否返回扁平的 `sources` URL 列表 |

**请求示例：**

```bash
curl -X POST http://localhost:8888/v1/tools/web-search \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "Ollama keep_alive 默认值", "top_k": 2}'
```

**响应示例（实测）：**

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
      "snippet": "Alternatively, you can change the amount of time all models are loaded into memory by setting the OLLAMA_KEEP_ALIVE environment variable ...",
      "paragraphs": ["Documentation IndexFetch the complete documentation index at: /llms.txt"]
    }
  ]
}
```

- `text` 是所有抓取段落的拼接结果，可直接塞进 prompt；`results[]` 保留逐页的标题/URL/摘要/段落；`answer` 仅在 `synthesize: true` 时非 null。
- 状态码：`websearch.enabled=false` → **404**；上游缺失 → **503**；上游不可达 → **502**；网关等待超过 `websearch.timeout` → **504**；非 POST → **405**；请求体不是 JSON 对象 → **400**。上游自己返回的错误（如 `synthesize` 触发上游 300s 推理上限的 **502**）连同响应体**原样透传**。

> 启用该上游后，它的免费模型也会出现在 `/v1/models` 里，并可直接路由聊天：
> `{"model": "opencode/big-pickle", ...}` 打到 `POST /v1/chat/completions` 即可。

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
  -H "Authorization: Bearer sk-gateway-example-key" \
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
  -H "Authorization: Bearer sk-gateway-example-key" \
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
| 方法 | `POST`（其他方法一律 **405**，响应头 `Allow: POST`） |
| 认证 | ✅ 需要 |
| 说明 | Ollama 原生补全接口，按 `model` 路由后原样转发；在 `/docs` 中有完整 schema |

**请求参数：**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model` | string | ✅ | 模型名或网关别名（别名会先解析成真实模型名再转发） |
| `prompt` | string | ✅ | 待续写的提示词 |
| `raw` | boolean | ❌ | `true` 跳过模型内嵌 chat template，直接续写 `prompt`（打分/重排类提示需要） |
| `system` / `template` / `context` | string/string/array | ❌ | Ollama 原生字段，原样透传 |
| `images` | array | ❌ | 视觉模型的 base64 图片列表 |
| `format` | `"json"` / object | ❌ | 结构化输出：`json` 或直接给 JSON schema |
| `stream` | boolean | ❌ | **缺省即流式**（与 Ollama 一致，返回 NDJSON）；`false` 返回单个 JSON 对象 |
| `keep_alive` | string/number | ❌ | 模型驻留时长，如 `"3m"` |
| `logprobs` / `top_logprobs` | boolean / int(1-20) | ❌ | 逐 token 对数概率及其候选数 |
| `options` | object | ❌ | `temperature` / `top_p` / `num_predict` / `num_ctx` / `seed` / `stop` …；`num_ctx` 可能被 `num_ctx_overrides` 覆盖 |

其余未知字段一律保留，网关不注入任何内容（别名声明的 `params` 除外）。

**请求示例：**

```bash
curl http://localhost:8888/api/generate \
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

**响应 (200 OK)：**

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

流式（`stream` 为 `true` 或缺省）时返回 `application/x-ndjson`，每行一个上述结构的片段，
最后一行 `done: true`：

```
{"model":"qwen3:8b","response":"代码","done":false}
{"model":"qwen3:8b","response":"如诗行","done":true,"done_reason":"stop","eval_count":8}
```

> 与聊天接口一样，`/api/generate` 只在 **Ollama 上游**可用；把模型路由到 LM Studio /
> OpenAI 兼容上游时，该路径会原样打到对方 `base_url` 上（对方一般回 404）。
> 需要跨后端通用请用 `/v1/chat/completions`。

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
  -H "Authorization: Bearer sk-gateway-example-key" \
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
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "llama3"}'
```

---

### 管理 API（Web 管理台）

Web 管理台使用的只读接口，统一挂在 `/v1/management` 前缀下（实现在
`app/management.py`，请求明细由 `app/request_log.py` 落库）。管理台前端本身
由网关以静态文件提供服务，地址 **`/admin/`**（构建产物在 `app/web`，见 README 的
「Web 管理台」一节）；静态壳层不鉴权——登录页要能打开——但页面内每一次数据请求
都经过 `require_admin`。除登录外，全部接口需要以下**两种凭据之一**：

- **管理台会话 Cookie**：`POST /v1/management/auth/login` 用 `ADMIN_PASSWORD` 换取
  12 小时 HMAC 签名 HttpOnly Cookie（浏览器走这条路）；
- **网关 API Key**：`auth.api_keys` 中的任一 Key，经 `Authorization: Bearer` 或
  `X-API-Key` 提交，便于脚本与 curl 复用已有凭据。

**认证方式：**

```bash
# 登录换取 12 小时 HttpOnly 会话 Cookie
curl -c cookie.txt http://localhost:8888/v1/management/auth/login \
  -H "Content-Type: application/json" -d '{"password": "your-admin-password"}'

# 之后用 Cookie 认证
curl -b cookie.txt http://localhost:8888/v1/management/overview

# 或直接带上网关 API Key
curl -H "X-API-Key: $GATEWAY_API_KEY" http://localhost:8888/v1/management/overview
```

`ADMIN_PASSWORD` 本身只是登录页凭据，不是请求凭据：把它当作 `Bearer` / `X-API-Key`
提交一律 **401**；查询串里的 key 也不接受（避免落进访问日志与浏览器历史）。
网关鉴权关闭（`auth.enabled: false` 或没有配置 key）时没有可接受的 API Key，
管理接口退回 Cookie-only，绝不会变成匿名可访问。

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/v1/management/auth/login` | Body `{"password": "..."}`；成功 `200` 并下发 Cookie，密码错误 `401` |
| `POST` | `/v1/management/auth/logout` | 清除会话 Cookie |
| `GET` | `/v1/management/auth/ping` | 受保护心跳，返回 `{"ok": true, "admin": true}` |
| `GET` | `/v1/management/overview` | KPI、上游摘要、运行中模型（含 GPU/部分/CPU 判定）、`request_log` 开关与库信息 |
| `GET` | `/v1/management/upstreams` | 上游实例列表（状态 / 已加载数 / 显存 / 探测延迟） |
| `GET` | `/v1/management/upstreams/{name}` | 单实例详情：`/api/ps` 运行中模型 + `/api/tags` 已装模型；不可达返回 `503` 且带 `"unreachable": true` |
| `GET` | `/v1/management/models/llm` | 模型 / 别名 / `num_ctx_overrides` 只读投影 |
| `GET` | `/v1/management/models/rerank` | Rerank 配置投影（`api_key` 一律掩码为 `••••••`），含 `upstream_api` / `on_error` 与请求/响应示例 |
| `GET` | `/v1/management/models/systemone` | System One 配置投影（同样掩码），含请求/SSE 示例 |
| `GET` | `/v1/management/tools/websearch` | Web Search 配置投影：`enabled` / `endpoint` / `upstream` / `upstream_type` / `path` / `base_url`（由 provider upstream 派生）/ `api_key_env` / `api_key`（掩码）/ `timeout` / `provider_configured` |
| `GET` | `/v1/management/requests` | 请求明细：`start`/`end`/`upstream`/`model`/`status`(2xx\|3xx\|4xx\|5xx)/`path`/`page`/`page_size`，返回 `stats` + `rows` + `pagination` + `request_log`（开关状态） |
| `GET` | `/v1/management/requests/{id}` | 单条明细（含截断至 8 KB 的请求/响应体，敏感字段已脱敏） |
| `GET` | `/v1/management/stats/timeseries` | 时序聚合：`bucket=hour\|day`，每桶 `count` / `errors` / `p95` |
| `GET` | `/v1/management/stats/distribution` | 分组计数：`group_by=upstream\|model\|status`，返回 `{group_by, start, end, items:[{key, count}]}`；`status` 只分 `ok`(<400) / `error`(≥400)，未命中上游的行计入 `-` |
| `GET` | `/v1/management/stats/failures` | 失败统计：`by_code` / `by_upstream` / `samples` |

**状态码：**

| 码 | 场景 |
|----|------|
| `200` | 正常 |
| `400` | 时间格式非法、`start > end`、`page`/`page_size`/`status`/`bucket`/`group_by` 取值非法 |
| `401` | 既无有效会话 Cookie 也无网关 API Key；或登录密码、用户名错误；或把 `ADMIN_PASSWORD` 当成请求头凭据 |
| `404` | `upstream` 名称或请求记录 `id` 不存在 |
| `503` | 未配置 `ADMIN_PASSWORD`；探测的上游不可达（`upstreams/{name}`）；`/admin` 的前端尚未构建（`app/web` 缺失） |

**分布与配置投影示例：**

```json
// GET /v1/management/stats/distribution?group_by=upstream
{ "group_by": "upstream", "start": "2026-11-02T05:00:00", "end": "2026-11-03T05:00:00",
  "items": [ { "key": "ollama-gpu", "count": 642 },
             { "key": "lmstudio", "count": 380 },
             { "key": "-", "count": 12 } ] }

// group_by=status 只有两个键：ok（status < 400）与 error（status >= 400）

// GET /v1/management/tools/websearch
{ "enabled": true, "endpoint": "/v1/tools/web-search", "upstream": "opencode",
  "upstream_type": "opencode", "path": "v1/web-search",
  "base_url": "http://vm.gateway:11430", "api_key_env": "OPENCODE_API_KEY",
  "api_key": "••••••", "timeout": 300.0, "provider_configured": true }
```

`base_url` / `upstream_type` 由 `websearch.upstream`（未指名时取第一个 `type: "opencode"` 上游）派生，
与 `WebSearchService` 的路径解析用的是同一函数 `Config.websearch_upstream_obj()`；
`api_key` 只回报 `websearch.api_key_env` 所指环境变量是否已设置，值永远不外泄。

**请求日志（SQLite）：**

每笔请求在 `requests` 表中占一行，字段覆盖请求参数、耗时与响应结果：

| 分类 | 字段 |
|------|------|
| 时间 | `ts`（到达）、`ts_end`（完成，流式在流关闭时写入）、`duration_ms`、`ttfb_ms`；`ts`/`ts_end` 精确到秒，亚秒耗时看 `duration_ms` |
| 请求 | `method`、`path`、`query`、`requested_model`（客户端写的名字）、`model`（别名解析后）、`request_body`、`req_size`、`stream`、`client_ip` |
| 结果 | `status`、`upstream`、`response_body`、`res_size`、`tokens`、`error` |
| 标记 | `request_body_truncated`、`response_body_truncated` |

- **入库开关**：`config.json` 的 `request_log.enabled`（默认 `true`）逐请求读取，无需重启。
  关掉后代理不再采集请求/响应体、也不写库，`/overview` 与 `/requests` 里的
  `request_log.enabled` 会回报 `false`；改回 `true` 即恢复记录。
  配置热加载每秒最多比对一次文件 mtime，所以开关在**约 1 秒内**生效（已实测）。
  数据库路径与保留天数仍由环境变量决定。
- 默认写入 `config.json` 同级目录的 `llmhub.db`（容器内即 `/data/llmhub.db`），WAL 模式；
  可用 `REQUEST_LOG_DB` 覆盖路径、`REQUEST_LOG_RETENTION_DAYS`（默认 30，0 表示永久保留）控制清理。
- 旧库自动升级：启动时比对 `PRAGMA table_info(requests)`，缺失的列用 `ALTER TABLE ADD COLUMN` 补齐。
- 写入通过有界内存队列异步批量落盘，不阻塞转发主链路；队列满时丢弃并告警。
- 请求/响应体截断至 8 KB，`authorization`、`api_key`、`token`、`password` 等字段在落库前替换为 `***`。

**网关边界：** `/v1/management` 与 `/admin` 一样不受 `security.path_allowlist` 限制（它使用
独立的会话认证），但仍受 HTTP 方法白名单、客户端 IP 白名单与速率限制约束。管理台只读，
不提供任何修改 `config.json` 的写接口。

---

## 流式响应

### OpenAI 格式 (SSE)

设置 `"stream": true` 启用流式响应：

```bash
curl http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
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
  -H "Authorization: Bearer sk-gateway-example-key" \
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

`/api/generate`（`response` 字段）与 `/api/chat`、`/v1/chat/completions`、`/v1/completions`
一样属于**缺省即流式**的端点：请求体不写 `stream` 时按流式转发，与 Ollama 行为一致。

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
| `405` | Method Not Allowed | 不支持的 HTTP 方法（由 `security.allowed_http_methods` 决定，默认仅 GET/POST）；`/api/generate`、`/v1/rerank`、`/v1/systemone`、`/v1/tools/web-search` 这类只接受 POST 的端点还会带上 `Allow: POST` |
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
  -H "Authorization: Bearer sk-gateway-example-key" \
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
  -H "Authorization: Bearer sk-gateway-example-key" \
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
