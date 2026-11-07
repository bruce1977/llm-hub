# LLM Hub API 文档

English: [API.md](API.md)。

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
  - [消息补全（Anthropic 兼容）](#消息补全anthropic-兼容)
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

**Key 从哪里来：** 调用方的 Key 是 `llmhub.db` 里 `api_keys` 表的一行，表里只有 `sha256(明文)` 与掩码；
在管理台 `/admin`（账户菜单 → ⚿ API Key 管理）签发与吊销。`GATEWAY_API_KEYS` /
`GATEWAY_API_KEYS_FILE`（如 `data/.keys`）只在**表为空的首次启动**导入一次，之后文件不再被读取。
新生成的 Key 形如 `sk-hub-` + 40 位 base62，共 47 字符。详见「管理 API」一节的
**调用方 API Key**。

**客户端 IP 白名单：** `auth.allow_access_ip_whitelist`（正则字符串数组，`re.fullmatch` 逐条匹配）
命中的来源可以访问 `/docs` 与 `/openapi.json`，并且调用任何业务 API 时免查 API Key；未命中时
`/docs`、`/openapi.json` 一律 **404**（不是 401/403，对外看起来就像没有这两个路径）。匹配对象是
ASGI 的客户端地址（`request.client.host`），网关代码从不读取 `X-Forwarded-For`；但 uvicorn 的
`ProxyHeadersMiddleware` 在 TCP 对端属于可信代理时（`--forwarded-allow-ips`，默认 `127.0.0.1`）
会用转发头改写 `request.client`，所以前置代理必须自行清理 `X-Forwarded-For`。

**Swagger UI：** 打开 `/docs`（需先满足上面的 IP 白名单），**每个端点的 Parameters 区都有一个
`X-API-Key` 输入框**——点 **Try it out** 后填入 Key，点 **Execute** 时随本次请求发送（只影响该端点的
这一次请求）。也可以用右上角 **Authorize** 一次性填入 `Authorization: Bearer` 或 `X-API-Key`，此后
所有请求自动携带（`persistAuthorization` 刷新页面后依然保留）。白名单命中的来源即使不填 Key 也能
直接 Execute。

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

**配置（`config.yaml` 的 `systemone` 段）：**

```yaml
systemone:
  enabled: true
  backend: http://llm.home:11435
  model: tev1
  api_key_env: SYSTEMONE_SERVER_API_KEY
  path: v1/systemone
  timeout: 60.0
```

- `backend` 指向任一 System One 后端即可，网关侧行为完全一致——纯转发，不做模型解析、不改写响应。容器内指向宿主机 Ollama 时用 `http://host.docker.internal:11435`。
- `model` **会原样出现在转发给后端的请求体里**：仅当它是具体值且调用方自身没带 `model` 时才注入；`"local"` 是**哨兵值**，表示不注入、由调用方自行指定。
- laya-server 的 `model` 为字面量枚举，**仅接受 `auto` / `english` / `multilingual` / `typed-decisions`**，其它值返回 **HTTP 422** `literal_error`。
- Ollama 的 `model` 必须是**真实模型名**：`auto` → **404** `model 'auto' not found`；缺失 → **400** `model is required`。因此后端换成 Ollama 时，把 `model` 配成实际模型（如示例中的 `tev1`）。
- 后端密钥从环境变量读取，绝不写入 `config.yaml`。该 `Authorization` 头对不需要鉴权的 Ollama 后端无副作用，Ollama 会直接忽略。

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
| 说明 | 关键词检索网页正文段落。请求体转发到 `websearch.upstream` 指定的上游（`websearch.path`，默认 `v1/web-search`），上游响应原样返回，网关不改写。检索能力的实现方是 [opencode-port](https://github.com/bruce1977/opencode-port)（OpenCode API Gateway）。 |

**配置（`config.yaml` 的 `websearch` 段 + 对应上游）：**

```yaml
upstreams:
- name: opencode
  type: opencode
  base_url: http://vm.gateway:11430
  models:
  - auto
  - opencode/*
  timeout: 300
  priority: 30
websearch:
  enabled: true
  upstream: opencode
  path: v1/web-search
  api_key_env: ''
  timeout: 300.0
```

- 检索方是一个普通的 `upstreams` 条目（`type: "opencode"`）：它的地址、超时、附加 `headers` 只配一次，同时被模型聚合（`/v1/models`、`/api/tags`）、聊天路由和 `/probe` 复用。`opencode` 类型走 OpenAI 兼容的发现接口（`GET /v1/models`，解析 `data[].id`），健康探测走 `GET /health`。
- `websearch.upstream` 留空时自动选取**第一个 `type: "opencode"` 的上游**；填了名字却找不到对应上游则返回 **503**（不会静默回退到别的上游）。
- `api_key_env` 默认空字符串 = 不签名（当前 OpenCode 网关匿名可访问）。若上游启用了密钥，把它设成变量名（如 `OPENCODE_GATEWAY_API_KEY`），网关会以 `Authorization: Bearer <值>` 调用上游；密钥只从环境变量读取，绝不写入 `config.yaml`。
- `model` 字段**纯透传**：取 opencode-port 支持的模型名（**不是 llm-hub 在 `config.yaml` 里配置的模型**），调用方传什么就转什么，不传则由上游自行选择；网关不会用自己的 `aliases` 去解析它。上游（opencode-port）的模型清单可在控制台的 Tools · Web Search →「模型清单」查看；省略 `model` 时由 opencode-port 依次取 `web_search.model` → 首个本地模型（`ollama-local/*`）→ `models.auto.fallback` 作为默认模型，该模型只在 `synthesize=true` 时参与答案综合。
- `synthesize: true` 会额外驱动一次模型综合作答，耗时由上游的推理速度决定，可能远超纯检索（实测 5~15s）。上游自身对单次推理有 **300 秒** 的硬上限：绕过本网关直接压测 `vm.gateway:11430`，`synthesize` 在 311s 返回 `502 {"error":{"message":"inference timed out (>300s)","code":"opencode_unavailable"}}`。因此 `websearch.timeout` 至少保持 300（默认即 `300.0`），让上游自己的错误原样透传（**502**）而不是先被网关判成 **504**；再往上调也不会更快拿到答案。需要 `answer` 时建议把 `model` 指向更快的上游。

**请求体字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 检索关键词（非空） |
| `model` | string | ❌ | opencode-port 支持的模型名（不是 llm-hub 配置的模型），原样透传；省略时使用 opencode-port 的默认模型 |
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

> Claude SDK / Claude Code 用的 Anthropic 信封请走 [`/v1/messages`](#消息补全anthropic-兼容)——同一个上游、
> 同一套别名解析，只是请求与响应的外层格式不同。

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
| `reasoning_effort` | string | ❌ | OpenAI 兼容的思考强度（`none` / `low` / `medium` / `high`）；Ollama 用 `none` 关闭思考，不传由后端决定 |
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

### 消息补全（Anthropic 兼容）

#### Anthropic Messages 格式

| 项目 | 值 |
|------|-----|
| 路径 | `/v1/messages`（另提供单数别名 `/v1/message`） |
| 方法 | `POST` |
| 认证 | ✅ 需要（`Authorization: Bearer` 或 `x-api-key`） |
| 说明 | Anthropic Messages 兼容接口：网关把请求转成 OpenAI 聊天格式转发给上游（Ollama `/api/chat` 或 `/v1/chat/completions`），再把响应包回 Anthropic 信封 |

> `model` 留空或 `"auto"` 时回落到 **默认上游** 的首个模型；未配置 `default_upstream` 且模型无法解析时返回 `404`。
> 未知字段会被保留，请求日志与别名解析与其他上游路径完全同源；但**流式不是透传**——上游的 NDJSON / SSE
> 分片会被重新编排成下面的 Anthropic 事件序列。

**请求示例：**

```bash
curl http://localhost:8888/v1/messages \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "max_tokens": 256,
    "system": "你是一个有帮助的助手",
    "messages": [
      {"role": "user", "content": "什么是机器学习？"}
    ],
    "stream": false
  }'
```

**请求参数：**

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model` | string | ❌ | 模型名称或别名；留空 / `auto` 时回落默认上游首个模型（网关路由名，非上游原始名） |
| `system` | string / array | ❌ | 系统提示；可为字符串或文本块数组 `[{"type":"text","text":"..."}]` |
| `messages` | array | ✅ | 消息列表，元素为 `{"role","content"}`（`role` ∈ `user`/`assistant`），`content` 可为字符串或块数组（含 `image` 块） |
| `max_tokens` | integer | ✅ | 最大生成 token 数（必须 > 0） |
| `temperature` | float | ❌ | 采样温度 (0-2) |
| `top_p` | float | ❌ | 核采样概率上限 (0-1) |
| `top_k` | integer | ❌ | 保留的候选 token 数（仅 Ollama 上游生效） |
| `stop_sequences` | array | ❌ | 停止序列列表，命中即结束生成 |
| `stream` | boolean | ❌ | 是否流式；`false` 返回单个 JSON 信封，`true` 返回 Anthropic SSE 事件流 |

**响应（非流式，200 OK）：**

```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "model": "qwen3:8b",
  "content": [
    {"type": "text", "text": "机器学习是人工智能的一个分支..."}
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 25,
    "output_tokens": 100
  }
}
```

> `usage` 由上游原生 token 计数重建：Ollama 取 `prompt_eval_count` / `eval_count`，OpenAI 兼容取 `prompt_tokens` / `completion_tokens`。

**流式（SSE）事件顺序：** `message_start` → `content_block_start` → `content_block_delta*`（每个 `delta.text` 携带增量文本）→ `content_block_stop` → `message_delta`（含最终 `stop_reason` 与 `usage`）→ `message_stop`。上游错误以 `event: error` 事件返回，而非断开连接。

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
| `think` | boolean | ❌ | Ollama 原生思考开关：`false` 关闭思考（响应不含 `thinking`），缺省由模型决定 |
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
- **网关 API Key**：`api_keys` 表（`llmhub.db`）里的任一 Key，经 `Authorization: Bearer` 或
  `X-API-Key` 提交，便于脚本与 curl 复用已有凭据。表在**首次启动**时由
  `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE`（如 `data/.keys`）导入，之后不再读文件；
  未设置 `GATEWAY_API_KEYS_FILE` 时会自动读取 `CONFIG_PATH` 同目录的 `.keys`（如 `/config/.keys`）。

`/api-keys` 三个接口是唯一的例外：只接受**会话 Cookie**（`require_admin_session`）。网关 API Key
就是任何客户端手里都有的凭据，若这里也接受它，客户端就能给自己再造一个 key，所以只登录管理台才能签发与吊销。

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
网关鉴权关闭（`auth.enabled: false`，或首次启动时环境变量与密钥文件都没有配置 Key，`api_keys` 表因此
一行都没有）时没有可接受的 API Key，
管理接口退回 Cookie-only，绝不会变成匿名可访问。

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/v1/management/auth/login` | Body `{"password": "..."}`；成功 `200` 并下发 Cookie，密码错误 `401` |
| `POST` | `/v1/management/auth/logout` | 清除会话 Cookie |
| `GET` | `/v1/management/auth/ping` | 受保护心跳，返回 `{"ok": true, "admin": true}` |
| `GET` | `/v1/management/overview` | KPI（含今日请求数与今日 token 合计 `tokens_today`；可选查询参数 `today_start` / `today_end` 指定这个「今日」窗口，控制台填的是浏览器本地午夜换算成 UTC——用服务端 UTC 日界的话，UTC+8 下 00:00-08:00 的请求会被漏掉；不传则按 UTC 午夜到当前时间）、上游摘要、可用模型清单（Ollama 取 `/api/ps` 的驻留模型；OpenCode 这类托管上游没有 `/api/ps`，探测成功时把它声明的模型一并计入，设备为 `"cloud"`、显存为 `null`；根 `aliases:` 的派生模型再追加在末尾，`derived: true`、设备沿用目标模型、显存为 `null`，`kpi.running_models` 不重复计它们），每行含 `model` / `display`（`model_aliases` 短名）/ `alias`；另有 `request_log` 开关与库信息 |
| `GET` | `/v1/management/upstreams` | 上游实例列表（状态 / 已加载数 / 显存 / 探测延迟） |
| `GET` | `/v1/management/upstreams/{name}` | 单实例详情：`models` 取自 `config.yaml` 的 `upstreams[].models`（`model_aliases` 短名作为显示名），前缀通配（如 `opencode/*`）用与路由同一个发现结果展开成具体模型，末尾追加归属于该上游的根 `aliases:` 派生模型名；`running_models` 是 `/api/ps` 的运行中模型（含设备与显存/占用），托管上游则列出它声明的模型（设备为 `"cloud"`、显存为 `null`），派生模型行 `derived: true`（设备沿用目标模型、显存 `null`）；不可达返回 `503` 且带 `"unreachable": true` |
| `GET` | `/v1/management/models/llm` | 逐上游的模型清单，来源是 `config.yaml` 的 `upstreams[].models`（实例上装了但没声明的模型不列出，因为它们本来就走不通网关）；前缀通配按发现结果展开，根 `aliases:` 的派生模型作为独立一行紧跟其目标模型。每行 `model`（真实 id，派生行是别名名）/ `display`（`model_aliases` 短名，无则即声明名）/ `alias` / `derived` / `wildcard`（由通配展开而来）/ `running` / `device` / `device_ratio` / `num_ctx` / `size_gib` / `vram_gib`，派生行另带 `target` / `params` / `description` 且显存为 `null`；OpenCode 上游探测有效即全部 `running: true`，设备为 `"cloud"` 且显存为 `null`。另有 `aliases`（别名 / `kind`：`rename` 只改名、`derived` 是带固化参数的独立模型 / 目标 / 上游 / `params` / `description`）与 `num_ctx_overrides` |
| `GET` | `/v1/management/models/rerank` | Rerank 配置投影（`api_key` 一律掩码为 `••••••`），含 `upstream_api` / `on_error` 与请求/响应示例 |
| `GET` | `/v1/management/models/systemone` | System One 配置投影（同样掩码），含请求/SSE 示例 |
| `GET` | `/v1/management/tools/websearch` | Web Search 配置投影：`enabled` / `endpoint` / `upstream` / `upstream_type` / `path` / `base_url`（由 provider upstream 派生）/ `api_key_env` / `api_key`（掩码）/ `timeout` / `provider_configured` |
| `GET` | `/v1/management/requests` | 请求明细：`start`/`end`/`upstream`/`model`/`status`(2xx\|3xx\|4xx\|5xx)/`path`/`page`/`page_size`，返回 `stats` + `rows` + `pagination` + `request_log`（开关状态）。**只统计 API 调用**：查询始终带上 `request_log.API_PATH_SCOPE`（`path LIKE '/v1/%' OR path LIKE '/api/%'`），`/health`、`/metrics`、`/openapi.json`、`/admin/*` 等行照旧入库，但不出现在列表、图表、KPI 与失败统计里 |
| `GET` | `/v1/management/requests/{id}` | 单条明细（含截断至 8 KB 的请求/响应体，敏感字段已脱敏） |
| `GET` | `/v1/management/stats/timeseries` | 时序聚合：`bucket=hour\|day`，每桶 `count` / `errors` / `p95`。`tz_offset`（UTC 加多少分钟等于调用方本地时间，UTC+8 为 `480`，取值 -840..840）只影响 **day** 桶的分组，让每根柱子是一个本地自然日而不是 UTC 日；hour 桶保持 UTC，因为控制台是把它们当时间点渲染成本的 |
| `GET` | `/v1/management/stats/distribution` | 分组计数：`group_by=upstream\|model\|status`，返回 `{group_by, start, end, items:[{key, count}]}`；`status` 只分 `ok`(<500，含 4xx) / `error`(≥500)，与错误率、趋势图错误柱、`failures` 同一口径；未命中上游的行计入 `-` |
| `GET` | `/v1/management/stats/failures` | 失败统计：`by_code` / `by_upstream` / `samples` |
| `GET` | `/v1/management/api-test/models` | 每个 API 家族的可用模型、默认值与掩码密钥：`kinds` 以 `generate` / `chat` / `messages` / `embed_ollama` / `embed_openai` / `rerank` / `systemone` / `websearch` 为键，每键含 `method` / `endpoint` / `family` / `model_required` / `free_model` / `enabled` / `source`（选项来自哪个配置项）/ `models[]`（`model` / `display` / `alias` / `derived` / `target` / `upstream` / `type` / `running` / `device` / `api_key`（掩码）/ `unknown`）/ `default_model` / `api_key`（掩码）/ `api_key_env`；顶层另有 `max_response_bytes`。`api_models` 为空时按配置推导；`/api/...` 原生端点（`generate`、`embed_ollama`）只列 `type: "ollama"` 上游的模型，`/v1/...` 端点（`chat`、`messages`、`embed_openai`）也列 `lmstudio` / `opencode` 的模型；显式 `api_models` 列表按原样返回（封闭集合，忽略 `- ''` 之类的空白占位符，且 `free_model=false`）。一次探测所有上游，所以前端整站共用一份缓存，只在首次打开与点击「重新探测」时取 |
| `POST` | `/v1/management/api-test` | Body `{"kind": "generate", "body": {...}}`；服务端用网关自己的服务发出这一笔请求，返回信封 `status` / `ok` / `duration_ms` / `ttft_ms` / `content_type` / `stream` / `requested_model` / `model` / `upstream` / `injected` / `body` / `text` / `truncated` / `error`。`kind` 不在白名单、或需要模型的家族没给 `model` → `400` |
| `POST` | `/v1/management/bench` | 启动一次模型测速任务，Body `{"upstream": "...", "model": "...", "rounds"?: 4, "max_tokens"?: 500, "think"?: "auto"}`，立即返回 `{"job_id": "...", "status": "pending"}`；任务在后台跑 |
| `GET` | `/v1/management/bench/history` | 该 `upstream` + `model` 最近的 **3** 份测速记录（`?upstream=&model=`），按时间倒序，每行含 `decode_tok_s` / `decode_sd` / `prefill_tok_s` / `ttft_ms` / `timing_source` 与完整 `payload`。**注册顺序在 `/bench/{job_id}` 之前**，否则 "history" 会被当成 job_id |
| `GET` | `/v1/management/bench/{job_id}` | 轮询任务状态：`status` 为 `pending` / `running` / `done` / `failed`，`progress` 为 `{completed, total, warmup}`，完成时 `result` 含各轮明细与 `steady` 汇总，失败时 `error` 给出原因 |
| `GET` | `/v1/management/api-keys` | 调用方 API Key 列表（**仅会话 Cookie**）：`{items: [{id, masked, label, source, created_at}], total}`，`source` 为 `console`（在管理台签发）或 `file`（首次启动从环境变量导入）。只回掩码，既没有明文也没有 hash |
| `POST` | `/v1/management/api-keys` | 签发一把新 Key（**仅会话 Cookie**）：Body `{"label": "..."}`，`label` 必填（空 → `400`，超过 100 字符 → `422`），`201` 返回 `{item, secret}`。**这个响应是明文一生中唯一一次离开服务器**，之后任何接口都拿不回来 |
| `DELETE` | `/v1/management/api-keys/{id}` | 吊销（**仅会话 Cookie**）：`200` 返回 `{"deleted": id}`，`id` 不存在 → `404`。内存校验集合随即重载，下一笔请求就用不了了 |

**状态码：**

| 码 | 场景 |
|----|------|
| `200` | 正常 |
| `400` | 时间格式非法、`start > end`、`page`/`page_size`/`status`/`bucket`/`group_by` 取值非法、`api-test` 的 `kind` 不在白名单或该家族要求模型却没给 `model` |
| `401` | 既无有效会话 Cookie 也无网关 API Key；或登录密码、用户名错误；或把 `ADMIN_PASSWORD` 当成请求头凭据；或只带网关 API Key 访问 `/api-keys`（这三个接口只认会话 Cookie） |
| `404` | `upstream` 名称、请求记录 `id` 或 `api-keys/{id}` 不存在 |
| `503` | 未配置 `ADMIN_PASSWORD`；探测的上游不可达（`upstreams/{name}`）；`/admin` 的前端尚未构建（`app/web` 缺失） |

**分布与配置投影示例：**

```json
// GET /v1/management/stats/distribution?group_by=upstream
{ "group_by": "upstream", "start": "2026-11-02T05:00:00", "end": "2026-11-03T05:00:00",
  "items": [ { "key": "ollama-gpu", "count": 642 },
             { "key": "lmstudio", "count": 380 },
             { "key": "-", "count": 12 } ] }

// group_by=status 只有两个键：ok（status < 500，含 4xx）与 error（status >= 500）

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
| 结果 | `status`、`upstream`、`response_body`、`res_size`、`tokens_in`、`tokens_out`、`tokens`、`error` |
| 标记 | `request_body_truncated`、`response_body_truncated` |

- **token 用量分方向入库**：`tokens_in` / `tokens_out` 分别是输入（提示）与输出（生成）的 token 数，
  取自 OpenAI 兼容响应的 `usage.prompt_tokens` / `usage.completion_tokens`，或 Ollama 响应里的
  `prompt_eval_count` / `eval_count`；`tokens` 继续保存总量，优先采用上游自己回报的 `total_tokens`
  （它计入推理 token 时会大于两者之和）。上游没有回报用量时三列都是 `NULL`，与真实的 `0` 可区分。
- **入库开关**：`config.yaml` 的 `request_log.enabled`（默认 `true`）逐请求读取，无需重启。
  关掉后代理不再采集请求/响应体、也不写库，`/overview` 与 `/requests` 里的
  `request_log.enabled` 会回报 `false`；改回 `true` 即恢复记录。
  配置热加载每秒最多比对一次文件 mtime，所以开关在**约 1 秒内**生效（已实测）。
  数据库路径与保留天数仍由环境变量决定。
- 默认写入 `config.yaml` 同级目录的 `llmhub.db`（容器内即 `/data/llmhub.db`），WAL 模式；
  可用 `REQUEST_LOG_DB` 覆盖路径、`REQUEST_LOG_RETENTION_DAYS`（默认 30，0 表示永久保留）控制清理。
- 旧库自动升级：启动时比对 `PRAGMA table_info(requests)`，缺失的列用 `ALTER TABLE ADD COLUMN` 补齐。
- 写入通过有界内存队列异步批量落盘，不阻塞转发主链路；队列满时丢弃并告警。
- 请求/响应体截断至 8 KB，`authorization`、`api_key`、`token`、`password` 等字段在落库前替换为 `***`。
- **`HEAD` 请求只转发、不记录**：它没有请求体也没有响应体，且监控探活会持续发送，记下来只会淹没真实
  调用。转发路径照旧，写入判定只在 `logging_on = self._logging_on(cfg) and method != "HEAD"` 一处。
  （按默认的 `allowed_http_methods: [GET, POST]`，HEAD 根本到不了代理，直接 `405`。）

**调用方 API Key（存储在 SQLite，实现在 `app/key_store.py`）：**

Key 不再是密钥文件里的一行文本，而是 `llmhub.db` 中 `api_keys` 表的一行，可在管理台随时签发与吊销：

| 字段 | 说明 |
|------|------|
| `id` | 自增主键，`DELETE /api-keys/{id}` 用它定位 |
| `hash` | `sha256(明文)` 的小写十六进制，`UNIQUE`——**库里没有明文** |
| `masked` | 展示用掩码，签发时一并算好存下，列表页不需要还原任何东西 |
| `label` | 描述（最长 100 字符），签发时必填 |
| `source` | `console` = 在管理台签发；`file` = 首次启动从环境变量导入 |
| `created_at` | 创建时间，秒级 ISO UTC |

- **格式与长度固定**：`sk-hub-` + 40 位 base62（`secrets` 采样）= **47 字符**，约 238 位随机。
  前缀不是密钥的一部分，它的作用是让一个疑似泄漏的字符串在日志与客户端配置里一眼可辨。
  掩码保留前缀与主体首尾各 4 位（`sk-hub-FNdc…SVvw`）；从文件导入的旧 Key 形状任意（如 `sk-...`），
  退化为整串首 8 尾 4，12 字符及更短的全部打点。
- **为什么是 SHA-256 而不是 bcrypt/argon2**：生成的 Key 是 238 位随机数，没有可供暴破的弱口令，
  而网关要在**每一笔**客户端请求上校验 Key；慢速 KDF 只会给每次调用加上几十毫秒延迟，把网关自己
  变成 DoS 目标，却防不住一个谁也猜不到的 secret。数据库泄漏也复现不出可用凭据。
- **校验走内存**：`ApiKeyStore` 启动时把全部 hash 读成一个 `frozenset`，`verify()` 只做一次集合查表，
  所以请求路径上不碰 sqlite。每次写入（签发 / 吊销 / 导入）都会重载该集合，因此新 Key 立即可用、
  吊销立即失效，无需重启。
- **只在空表时导入一次**：首次启动把 `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE`（或 `CONFIG_PATH`
  同目录的 `.keys`）里的 Key 以 `source: "file"` 写入，之后**文件不再被读取**——在管理台吊销的 Key
  不会因为重启而复活，改动文件也不会新增 Key。`auth.enabled` 且表里一个 Key 都没有时（且没有 IP
  白名单）拒绝启动，报错会提示先用环境变量把空表喂上。导入之后如果环境变量里还有表里没有的 Key，
  启动日志会以掩码逐条告警，因为这些凭据已经不再生效。
- 旧写法 `config.yaml` 里的 `auth.api_keys:` 仍然只读展示，写在配置里会被忽略并告警；密钥本身
  永远不进 `config.yaml`。

**模型测速（bench，实现在 `app/bench.py` + `app/bench_store.py`）：**

口径与 `D:/apps/ollama/bench_llm.mjs` 一致，纯 Python 实现，不依赖 Node：

- **预热**：默认 1 轮不计入统计；正式 4 轮，取均值 / 标准差 / 最小 / 最大 / 变异系数。
- **TTFT**：首 token 到达的墙钟时间（毫秒）。注意 Ollama 的非流式请求拿不到首 token 时刻，所以测速**始终走流式**。
- **Decode（tok/s）**：优先用服务端回报的 `eval_duration`（Ollama `/api/chat`）或 `usage` + `timings.predicted_ms`（OpenAI 兼容），缺失时回落客户端墙钟；口径写在 `timing_source`：`server` / `client` / `client*`（流式但不带服务端计时）。
- **Prefill（tok/s）**：`prompt_eval_duration` / `prompt_ms` → 提示 token 数 / 秒，同样回落客户端。
- **思考模型**：模型名命中 `qwen3` / `deepseek-r1` / `reasoning` 等模式时自动加 `think: false`（Ollama）或最小推理预算，避免思考链把 decode 拉低；`think` 参数可显式覆盖（`auto` / `true` / `false`）。
- **模型名对齐**：先请求上游的模型列表做 preflight，按「精确 → 归一化（去 `:latest`）→ 子串」顺序解析；找不到直接报错，不会静默测到别的模型。
- **代理**：内部 `httpx.AsyncClient` 使用 `trust_env=False`，与网关主客户端一致——本机若导出 `HTTP_PROXY`，否则打上游的请求会被绕到代理上（表现为 `502`）。

结果落在 `llmhub.db` 的 `bench_results` 表（与 `requests` 同库，WAL）：

| 分类 | 字段 |
|------|------|
| 标识 | `id`、`upstream`、`model`、`created_at`（任务完成时刻） |
| 目标 | `server`（`ollama` / `lm`）、`host`、`port` |
| 参数 | `rounds`、`warmup`、`max_tokens` |
| 指标 | `decode_tok_s`（均值）、`decode_sd`、`prefill_tok_s`、`ttft_ms`、`timing_source`、`prompt_tokens`、`gen_tokens` |
| 明细 | `payload`（完整 JSON：逐轮数据 + `steady` 汇总 + `config`） |

每个 `(upstream, model)` **只保留最新 3 份**：写入后按 `created_at DESC, id DESC` 删除第 4 份及更早的记录。
任务本身只存在内存（`_BENCH_JOBS`，上限 200 条，完成后被清扫），进程重启后旧 job_id 一律 `404`——历史成绩请走 `/bench/history`。

任务跑在**独立线程 + 独立事件循环**里，不占用 `POST /bench` 那个请求的生命周期；结果行先入写队列再置 `done`，
`/bench/history` 读之前会先 flush 队列，所以「状态刚变 done 就查历史」也能读到记录。

**接口测试（api-test，实现在 `app/api_test.py`）：**

管理台每个 API 文档块的「请求参数」标题右侧都有一个**测试**按钮，点开是浮层里的测试窗口
（不是嵌在文档下方），但浏览器不能直接调业务端点：`/api/generate`、
`/v1/rerank` 这些路径校验的是**调用方** API Key，而管理台的会话 Cookie 故意不是（见
`admin_auth.require_admin`）。所以请求在**服务端**执行——`POST /v1/management/api-test` 把
页面填好的 body 重建成一个真实的 ASGI `Request`，交给网关处理该端点用的**同一个服务**
（`ProxyService.forward` / `RerankService.handle` / `SystemOneService.handle` /
`WebSearchService.handle` / `MessagesService.handle`）。别名解析、固化参数注入、`num_ctx_overrides` 收窄、被封禁的
路径、请求明细入库全都与外部调用者一致，页面显示的就是 API 真实行为。

- `KINDS` 是**封闭白名单**（8 个 kind 映射到 `proxy` / `rerank` / `systemone` / `websearch` /
  `messages` 五类执行器），每个 kind 的上游仍由正常路由决定，因此这一组路由不会被改写成访问任意地址的代理。
- 模型选择框按 `api_models` 的家族收窄（见 USAGE 的配置参考）：每个选项**只列名字或别名**，
  上游、设备与运行状态放在悬停提示里；被其它家族显式认领的名字不再出现在 generate/chat 里
  （`api_models.embed` / `rerank` / `systemone` 与 `rerank.models`，比较时忽略 `:latest`）。
  `api_models.websearch` 不算认领——搜索端点本质是聊天透传，托管 provider 的路由模型
  （如 `opencode/auto`）必须继续可选；`systemone.model` 这类兜底同样不算。
  **非空的 `api_models` 列表是封闭集合**：原样返回、不做 provider 收窄、忽略 `- ''` 之类的空白占位符，
  且 `free_model` 置为 `false`——页里的「可用模型」表、请求/响应示例与测试窗口都读同一份结果。
  只有**留空推导**的 `systemone` / `websearch` 家族才保留 `free_model: true`（laya-server 的路由枚举、
  opencode-port 的模型 id），下拉末尾多一项「自定义模型…」，选中后换成可手输的输入框；
  再选回列表里的名字即可收起。测试窗口一打开就带着各参数的初始值，
  「恢复默认值」重新填入，「清空」则把每一项置空。**模型同样可以清除**：字段旁的 ×（或下拉的
  空白项）清空后请求体不带 `model`——需要模型的家族由窗口先拦下提示「请先选择模型」，
  `systemone` / `websearch` 则交给端点决定（System One 仍按配置注入 `systemone.model`）。
  「恢复默认值」会重新选中 `default_model`，「清空」也一并清掉模型。**有文档默认值的布尔参数渲染为复选框**，
  勾选与否都会显式发送 `true` / `false`；其余空值不会出现在请求体里，端点于是使用自己的默认。
  三个 LLM 窗口（generate / chat / messages）的 `stream` 初始为 `false`（端点缺省流式，测试窗口先要单个 JSON）；
  generate 另有 `think` 复选框（默认关闭，Ollama 原生顶层字段），chat 提供 OpenAI 兼容的
  `reasoning_effort`（Ollama 用 `none` 关闭思考）。
- 凭据永远不外泄：上游 `headers` 里的 key 与 `systemone.api_key_env` /
  `websearch.api_key_env` 所指环境变量一律回报为 `••••••`，页面据此显示只读掩码行。
- 响应最多回传 `max_response_bytes`（128 KB）并在 `truncated` 里标注——测试窗口是看回答的，
  不是下载回答的。流式响应逐块收集，`ttft_ms` 是首块到达时刻；`_drain` 会关闭迭代器，
  代理的生成器正是在 `aclose` 时写库，所以测试过的调用同样出现在「请求明细」里。
- 信封里的 `stream` 是**网关选择的传输方式**，与请求明细的 `stream` 列同源：调用方没写
  `stream` 时按 `proxy.STREAM_BY_DEFAULT` 决定。响应是否真的分块到达要看 `content_type`
  是不是 `application/x-ndjson` / `text/event-stream`——管理台就是据此决定「流式」标签与
  「首字」耗时是否显示，避免把一整个 JSON 对象说成流。
- 网关与上游的拒绝答复不会被吞：`400`（请求体不合法）、`404`（模型路由不到 / 功能已停用）、
  `413`（超大）、`503`（找不到 provider / provider 未部署）、`502`·`504`（上游连不上或超时）都原样
  装进信封（`ok: false`、`error: "HTTP 404"`），页面与真实调用者看到同一件事。只有路由层自己的
  拒绝才走 HTTP 状态（`kind` 不在白名单、该家族没给 `model` → `400`）。

**网关边界：** `/v1/management` 与 `/admin` 一样不受 `security.path_allowlist` 限制（它使用
独立的会话认证），但仍受 HTTP 方法白名单、客户端 IP 白名单与速率限制约束。管理台只读，
不提供任何修改 `config.yaml` 的写接口。两组成员会**真的向外发请求**：测速（`/bench`）额外写
`bench_results` 表，接口测试（`/api-test`）只像一次普通调用那样写请求明细，两者都不碰配置。

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

### Anthropic Messages 格式 (SSE)

`/v1/messages` 写 `"stream": true` 时，网关把上游分片重新编排成 Anthropic 事件流
（`message_start` → `content_block_start` → `content_block_delta*` → `content_block_stop` →
`message_delta` → `message_stop`）。单个增量事件形如：

```
event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"你"}}
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

> 根 `aliases:` 声明的每一项都是**一个独立模型**：目标模型仍然可以直接调用、也仍按它自己的默认参数运行，
> 而别名带自己的固化参数。Web 管理台因此把它单独列成一行（设备沿用目标模型所在实例，显存显示 `-`，
> KPI 的运行中模型数不重复计数）。若只想给已有模型换一个短名、不注入任何参数，用
> `upstreams[].model_aliases`——那只改显示名与调用名，不会新增模型。

**配置示例：**

```yaml
aliases:
  qwen3.5:4b-nothink:
    model: qwen3.5:4b
    params:
      think: false
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
