# LLM Hub —— `config.yaml` 逐节点说明

网关真正读取的每个节点与字段：默认值、消费它的代码，以及改了之后会发生什么。完整手册见
[USAGE_CN.md](USAGE_CN.md)；接口见 [API_CN.md](API_CN.md)；部署与环境变量见
[DEPLOY_CN.md](DEPLOY_CN.md)。English: [CONFIG.md](CONFIG.md)。

**目录** ·
[文件如何被读取](#文件如何被读取) ·
[节点一览](#节点一览) ·
[`server`](#server--网关自身) ·
[`routing`](#routing) ·
[`upstreams`](#upstreams--后端实例) ·
[`default_upstream`](#default_upstream) ·
[`aliases`](#aliases--独立成模型) ·
[`num_ctx_overrides`](#num_ctx_overrides) ·
[`api_models`](#api_models--每类-api-可以提供哪些模型) ·
[`rerank`](#rerank) ·
[`systemone`](#systemone) ·
[`websearch`](#websearch) ·
[`request_log`](#request_log) ·
[`security`](#security) ·
[路径面](#路径面--可调用路径的封闭集合) ·
[管理台专用字段](#管理台读取而路由不读的字段) ·
[已移除节点](#已不存在的节点) ·
[环境变量](#留在-configyaml-之外的设置) ·
[检查节点](#检查一个键是否真的起作用)

## 文件如何被读取

| 事实 | 细节 |
|---|---|
| 路径 | `CONFIG_PATH`（默认 `/data/config.yaml`）；`scripts/start-local.ps1` 传的是 `./data/config.yaml` |
| 格式 | YAML 1.2。密钥依然不该写在这里；注释可以 |
| 旧文件 | 旁边没有 `config.yaml` 时，YAML 之前的 `config.json` 会被加载并打印一条警告 —— JSON 本身就是合法 YAML，改个名就够了 |
| schema | `app/config.py` 里的 Pydantic 模型（`Config` 及其子模型）—— 那个文件*就是* schema |
| 缺失节点 | 每个节点都可选；没写就用下面列出的模型默认值。整个文件里只有 `upstreams[].name` 与 `upstreams[].base_url` 是必填 |
| 热重载 | `ConfigManager` 最多每秒比对一次文件 mtime，所以保存后约 1 秒内生效 |
| 未知的键 | **静默忽略** —— 见[检查节点](#检查一个键是否真的起作用) |

改一个值时，有两种读取时机需要区分：

- **每请求读取** —— 路由、别名、`rerank.*`、`systemone.*`、`websearch.*`、
  `request_log.enabled` 以及整个 `security.*`。永远不需要重启。
- **仅启动时读取** —— 监听套接字（`server.host` / `server.port` /
  `security.public.port` 由 `app/serve.py` 绑定一次）、共享的上游连接池
  （容量来自 `server.max_concurrency`、`server.upstream_keepalive_expiry`、
  `server.request_timeout`）以及 `api_keys` 表扫描，都只在启动时读取。
  改这些需要重启。

## 节点一览

| 节点 | `app/config.py` 中的模型 | 消费方 | 决定什么 |
|---|---|---|---|
| `server` | `ServerConfig` | `main.py` lifespan、`serve.py`、`proxy.py` | 绑定地址、内网端口、日志级别、上游超时、连接池 |
| `routing` | `RoutingConfig` | `Config.resolve()` | 未声明的**具名**模型是 404 还是回退 |
| `upstreams[]` | `UpstreamConfig` | `Config`、`proxy.py`、`main.py`、`management.py` | 每个后端：地址、方言、模型目录、请求头、优先级 |
| `default_upstream` | `str \| None` | `Config.default_upstream_obj()` | 未匹配的具名模型（`routing.strict: false`）与**每一次不带模型的调用**的回退目标 |
| `aliases` | `dict[str, AliasConfig]` | `Config.resolve()`、`proxy.py` | 派生模型及其强制注入的参数 |
| `num_ctx_overrides` | `dict[str, int]` | `proxy.py`、`messages.py` | 按真实模型名强制指定 Ollama 上下文长度 |
| `api_models` | `ApiModelsConfig` | `management.py`（仅管理台测试器） | 每类 API 可以提供哪些模型 |
| `rerank` | `RerankConfig` | `rerank.py`、`management.py` | `/v1/rerank` 打分引擎 |
| `systemone` | `SystemOneConfig` | `systemone.py`、`management.py` | `POST /v1/systemone` 转发 |
| `websearch` | `WebSearchConfig` | `websearch.py`、`management.py` | `POST /v1/tools/web-search` 转发 |
| `request_log` | `RequestLogConfig` | `proxy.py`、`management.py` | 调用是否入库 |
| `security` | `SecurityConfig` | `auth.py`、`bridge.py`、`main.py` 中间件 | 分区、Key 强制校验、公网暴露面、加固、限流 |

## `server` —— 网关自身

| 字段 | 默认值 | 读取方 | 作用 |
|---|---|---|---|
| `host` | `0.0.0.0` | `main.py`、`serve.py` | 所有监听器的绑定地址。`0.0.0.0` **加上** `security.internal.allow_anonymous: true` 会在启动时打印 "INTERNAL ZONE IS FULLY OPEN" 警告 |
| `port` | `8000` | `Config.listen_ports()`、`serve.py` | **内网（可信 LAN）**套接字。这个端口就是分区锚点，不是可以靠 IP 或请求头改写的设置 |
| `log_level` | `info` | `main.setup_logging()`、`app/logging.json` | 一个开关管所有 logger，包含 uvicorn 的访问日志：`2026-11-10 20:31:18.718 \| INFO    \| uvicorn.access \| 127.0.0.1:61641 - "GET /v1/models HTTP/1.1" 404 Not Found`。`debug` 会打印别名解析与路由决策；rerank 分数或路由看起来不对时就该用这个级别 |
| `request_timeout` | `600.0` | `main.py`（httpx 客户端）、`proxy.py`、`messages.py`、`rerank.py`、`systemone.py`、`websearch.py` | 没有单独设置上游级或功能级超时的调用，用「连接 10s / 读取 `request_timeout`」这个预算 |
| `max_concurrency` | `64` | `main.py` → `httpx.Limits` | 决定共享池容量：`max_connections = max(16, it)`、`max_keepalive_connections = min(32, max(8, it))` |
| `upstream_keepalive_expiry` | `30.0` | `main.py` → `httpx.Limits` | 到后端的空闲 keep-alive 连接保留多久 |

`follow_redirects=False` 与 `trust_env=False` 是代码级常量，不是配置：网关从不跟随上游重定向，
也从不让系统级 `HTTP_PROXY` 劫持后端调用。

## `routing`

| 字段 | 默认值 | 读取方 | 作用 |
|---|---|---|---|
| `strict` | `false` | `Config.resolve()` | `true` → 没有任何上游声明、也没有别名能解析的**具名**模型是 **404**。`false` → 请求回退到 `default_upstream` |

`strict` 拒绝的是「*猜*一个具名模型」，不是拒绝一个根本不带 `model` 的请求体。空模型名 ——
`GET`，或者请求体里带的是 `name` 的 Ollama 管理调用，例如
`POST /api/pull {"name":"qwen3:4b"}` —— 永远解析到 `default_upstream`，**即使
`strict: true`**。在这条规则落地之前，这类调用会死在
`404 No upstream configured for model ''`，也正是它让 `admin_endpoints: allow` 看起来像坏了。

还有一个前提：`default_upstream` 必须指向一个真实存在的 `upstreams[].name`。悬空的名字解析结果为
空，此时 **404** 才是正确答案 —— 见 [`default_upstream`](#default_upstream)。

## `upstreams` —— 后端实例

一个列表；每一项是一个实例。`name` 与 `base_url` 是整个文件里仅有的必填字段。

| 字段 | 默认值 | 读取方 | 作用 |
|---|---|---|---|
| `name` | —（必填） | 处处 | `aliases[].upstream`、`default_upstream`、`websearch.upstream` 与管理台引用的键 |
| `base_url` | —（必填） | `proxy.py`、`main.py` | 实例地址。有校验：必须是 `http(s)`、不能内嵌凭据、绝不能是云元数据地址；私网 LAN 地址是刻意允许的 |
| `type` | `ollama` | `UpstreamConfig.tags_path()` / `health_path()` / `parse_model_names()`、`main.py` | `ollama` → 原生 `/api/*` + `/v1/*`，通过 `api/tags` 发现模型；`lmstudio` → 仅 OpenAI 兼容，通过 `v1/models` 发现；`opencode` → 托管的 OpenAI 兼容 chat + 联网检索提供方，健康检查走 `health`，模型行报为 `device: "cloud"` |
| `models` | `[]` | `Config.find_upstream_for_model()`、`main.py`、`management.py` | 这个实例提供的目录：精确 id 或前缀通配（`qwen3:*`、`opencode/*`）。装了但没声明的模型不可路由，通配则通过上游自己的模型列表展开（发现结果缓存 60s） |
| `model_aliases` | `{}` | `Config._expand_upstream_aliases()` | **仅显示改名** —— 对外短名 → 上游真实 id，用于冗长的 LM Studio id。它不新增模型、不固定任何参数；生成的名字在内部被标记，好让管理台把它们与 `aliases` 区分开 |
| `timeout` | `null` | `proxy.py`、`messages.py`、`rerank.py`、`websearch.py` | 单实例读取超时；`null` 回退到 `server.request_timeout` |
| `description` | `""` | `management.py` | 管理台显示的自由文本 —— 不会发往任何地方 |
| `headers` | `{}` | `proxy._build_headers()`、`main._discovery_headers` | 附加到每个转发请求上的额外请求头，模型发现与 `/probe` 健康检查也会带上，所以自带鉴权的上游依然能出现在 `/v1/models` 里。**绝不要把密钥值写在这里** —— 改为声明一个环境变量名（`websearch.api_key_env` / `systemone.api_key_env`）。无论这里怎么设，网关都会剥掉调用方的 `cookie` / `referer` / `x-forwarded-*` 并丢弃上游的 `set-cookie` |
| `vram_total_gb` | `null` | `management.py` | 宿主机显存总量，供管理台的「已用/总量」仪表使用 —— 没有任何 API 暴露它，所以只能声明 |
| `priority` | `0` | `Config.find_upstream_for_model()`、`Config` 模型合并 | 多个上游声明同一模型时，**数值最小者胜**；相同则保持文件里的书写顺序 |

## `default_upstream`

`str | None`，默认 `null`。由 `Config.default_upstream_obj()` 解析，它按
**`upstreams[].name`** 查找这个值。

它在「没有任何上游匹配这个**具名**模型且 `routing.strict: false`」时被用到；此外 —— 因为空模型名
不算猜 —— **每一次不带模型的调用都会用它，即使 `routing.strict: true`**（见
[`routing`](#routing)）。匹配不到任何上游的名字解析为 `None`，于是回退静默变成 **404** ——
这是本文件里最常见的配置错误，任何改名之后都值得再核对一遍。`null` 与悬空名字含义相同。
现在配置加载时会打一条 **error**，把这个变量名和当前可用的上游一并列出，所以改名不会再无声地
弄坏 `api/pull`、`api/tags` 等所有不带模型的调用。

## `aliases` —— 独立成模型

每个键就是调用方发送的名字；这一项本身就是一个模型（管理台单独列一行，`/v1/models` 会列出它，
它的目标模型仍可用自己的名字调用）。

| 字段 | 默认值 | 读取方 | 作用 |
|---|---|---|---|
| `model` | —（必填） | `Config.resolve()` | 转发给上游的真实名字 |
| `upstream` | `null` | `Config.resolve()` | 固定归属实例；`null` 时按路由的方式解析（`get_upstream` → `find_upstream_for_model` → `default_upstream_obj`） |
| `params` | `{}` | `proxy.py`（`body.update(extra_params)`） | 强制注入转发请求体，客户端无法覆盖 —— 例如 `think: false` |
| `description` | `""` | `management.py` | 仅用作管理台 tooltip / 悬停文本 |

**纯显示改名**应该写在 `upstreams[].model_aliases`，不是这里。写进 `aliases` 会造出第二个可选模型，
而它什么参数都不固定。

## `num_ctx_overrides`

`dict[str, int]`，默认 `{}`。键是**解析后的目标**模型名，由 `proxy.py` 与 `messages.py` 只对
`type: "ollama"` 的上游生效（LM Studio / OpenAI 兼容后端认 `max_tokens`，不认 `num_ctx`）。
它会覆盖客户端发来的任何 `options.num_ctx`。

目的是显存，不是行为：Ollama 会预分配整个 KV cache，所以同一个模型用三种上下文长度访问就意味着
三个实例、三份内存占用。按模型固定这个数字才能保持一种热态形状。`management.py` 会在管理台显示
生效值。

## `api_models` —— 每类 API 可以提供哪些模型

五个可选列表。**只被 `management.py` 读取** —— 路由从不查它们，所以它们只收窄管理台的模型选择框、
「可用模型」表格与示例请求体，别的什么都不影响。

| 字段 | 留空的含义 | 非空的含义 |
|---|---|---|
| `generate` | 所有已声明模型，减去被 `embed` / `rerank` / `systemone` / `rerank.models` 认领的名字 | 完全按书写内容提供 |
| `embed` | 同样的推导 | 同上 |
| `rerank` | 回退到 `rerank.models` | 同上 |
| `systemone` | 回退到 `systemone.model`（除非它是 `""` 或 `local`） | 同上 |
| `websearch` | 检索提供方自己的模型（`websearch.upstream` 指名的那个 `opencode` 上游） | 同上 |

值得知道的规则：

- 名字可以是真实 id、`model_aliases` 短名或 `aliases` 键；没有任何实例提供的名字依然可见，并被标记
  `unknown: true`，而不是消失。
- `""` / 只有空白的项会被**丢弃** —— 占位符永远不会变成空选项，而只含占位符的列表算作空。
- 非空列表是**封闭集合**：不做提供方侧收窄，也没有自由输入的「自定义模型…」入口
  （`free_model: false`）。只有推导出来的家族才有这些。
- `websearch` 刻意**不算**一次「认领」：联网检索是 chat 透传，所以用来合成答案的那个模型依然可以
  被选作 chat。
- 两个 Ollama 原生测试器（`/api/generate`、`/api/embed`）还会额外剔除只由 `lmstudio` / `opencode`
  上游提供的行，因为那类后端没有原生端点。

## `rerank`

由 `app/rerank.py` 消费；大部分字段会镜像到管理台的 Rerank 投影（`management.py`）。

| 字段 | 默认值 | 作用 |
|---|---|---|
| `enabled` | `true` | `false` → `POST /v1/rerank` 返回 **404** |
| `endpoint` | `/v1/rerank` | **只是标签。**路由硬编码在 `main.py`（`/v1/rerank`、`/rerank`、`/api/rerank`）；这个字符串只用于命名管理台卡片、示例请求路径与测试器信封。改它不会移动端点 |
| `mode` | `logprobs` | 打分模式。`logprobs` —— 构造 yes/no 提示词，对第一个回答 token 的 logprobs 做 softmax —— 是唯一受支持的模式；已移除的 `embedding`（余弦）模式会返回 **400** 并附带解释 |
| `models` | `[]` | 本网关提供的 reranker；同时是 `api_models.rerank` 的回退，也是管理台示例请求体里用的模型 |
| `instruction` | 「直接回答问题」那段提示词 | 以 `{instruction}` 写入模板；请求可以自带 `instruction`，且它优先。这是对抗「话题匹配」的主要杠杆 |
| `template` | 官方 Qwen3-Reranker chat 模板 | 每篇文档打分时用的提示词（`{instruction}` / `{query}` / `{document}`）。只有当某个 reranker 期待不同措辞时才覆盖，并保留 yes/no 的回答形状 —— 分数来自第一个回答 token 的 logprobs |
| `yes_token` / `no_token` | `yes` / `no` | 两个候选 token |
| `yes_aliases` / `no_aliases` | `[]` | 额外接受的拼写，大小写不敏感、去掉标点后匹配（例如 `["是"]` / `["否"]`） |
| `temperature` | `0.0` | 发给打分调用；保持 0 |
| `top_logprobs` | `20` | 在回答位置采样的候选数量，这样前面带 `\n` 或 `<think>` token 时仍能看出 yes/no 概率 |
| `max_tokens` | `4` | 刻意很小 —— 答案只有一个 token，预算大了会让前置 token 把它吞掉 |
| `token_scan_depth` | `4` | 在多少个生成位置里搜索 yes/no 候选 |
| `stop` | `[]` | 发给上游的停止序列；留空则只靠 `max_tokens` |
| `options` | `{}` | 合并进每次打分调用的额外 Ollama options。这里**`num_ctx` 主导显存**：`qwen3-reranker:4b` 在默认 40960 上下文下约占 9.3 GB，8192 下约 4.0 GB —— 分数相同 |
| `keep_alive` | `-1` | 仅 Ollama。`-1` 沿用服务端默认；`0` 在这一波结束后立刻卸载；`"3m"` 保持热态。重排是突发型的，所以一个空闲的 4B reranker 会抢走 chat 模型的显存 |
| `logprobs_fallback` | `true` | 在退化为纯文本之前，先尝试下一种端点风格（`/api/generate` → `/api/chat` → `/v1/chat/completions`） |
| `fallback_yes` / `fallback_no` / `fallback_unknown` | `0.9` / `0.1` / `0.0` | 没有 logprobs 返回时使用的分数。`unknown` 保持 `0.0`，这样无法解析的答案绝不会看起来「半相关」—— 如果所有分数都是 0，就该看这个字段 |
| `normalize` | `false` | min-max 重缩放。刻意关闭：logprobs 本来就是概率，归一化会把最佳命中压成 1.0 / 最差压成 0.0，使分数在不同请求之间不可比 |
| `max_concurrency` | `8` | 逐文档打分调用上的信号量 |
| `return_documents` | `true` | 响应里带上文档文本；请求可以覆盖它 |
| `model_modes` | `{}` | 按模型覆盖模式（先按请求名，再按解析后的目标名）。每个值都通过 `MODE_ALIASES` 折叠到 `logprobs`（`generation`、`generative`、`cross-encoder`、`yes_no`、…）；保留 `embedding` 这个拼写只是为了让它给出那条有用的 **400** |
| `upstream_api` | `auto` | `auto` → Ollama 用带原始提示词的 `/api/generate`，OpenAI 兼容用 `/v1/chat/completions`；`generate` → 总是 `/api/generate`；`chat` → 总是 chat 端点 |
| `raw_prompt` | `true` | 原样发送渲染后的模板，而不套用模型的 chat 模板 —— 必须如此，因为 Qwen3-Reranker 的提示词本身*就是*一段完整 chat 提示词 |
| `disable_thinking` | `true` | 关闭思考模式，否则整个 token 预算都会花在 `<think>…</think>` 上 |
| `max_documents` | `0` | 护栏，0 = 不限；超过上限 → **400** |
| `max_document_chars` | `0` | 护栏，0 = 不限；更长的文档会被截断，而不是拒绝 |
| `on_error` | `score_zero` | 单篇无法打分 → 给 `0.0` 并继续（`score_zero`），或者 **502**（`fail`）。如果*每一篇*都失败，两种设置都是 **502** |

## `systemone`

由 `app/systemone.py` 消费，服务 `POST /v1/systemone`。

| 字段 | 默认值 | 作用 |
|---|---|---|
| `enabled` | `false` | `false` → **404** |
| `backend` | `http://127.0.0.1:8080` | 决策后端的 base URL —— 一个 laya-server 部署或 Ollama 0.40+。URL 校验与 `base_url` 相同 |
| `model` | 模型里是 `auto`，生成的配置里是 `local`，随包发布的文件里是一个真实名字 | 只有当它是具体值且调用方没传 `model` 时才注入转发请求体；哨兵值 `"local"` 表示「不注入」。**两种后端并不一致**：laya-server 只接受枚举 `auto` / `english` / `multilingual` / `typed-decisions`（其它值 **422**），而 Ollama 要一个已安装的模型名（`auto` → **404**）。同时也是 `api_models.systemone` 的回退 |
| `api_key_env` | `SYSTEMONE_SERVER_API_KEY` | 存放后端密钥的环境变量*名*；有值时以 `Authorization: Bearer` 发送，即使发给会忽略它的 Ollama 后端。必须以 `_API_KEY` / `_API_TOKEN` 结尾 —— 指向 `ADMIN_PASSWORD` 会被拒绝。这是**出站**密钥（llm-hub → 后端）；调用方的**入站**凭据是 `api_keys` 表里的网关 Key。启动日志会打出解析后的 URL 以及该变量是否已设置 |
| `path` | `v1/systemone` | `backend` 上的端点路径 |
| `timeout` | `60.0` | 读取超时；`0`/空回退到 `server.request_timeout` |

## `websearch`

由 `app/websearch.py` 消费，服务 `POST /v1/tools/web-search`。提供方就是一个普通的 `upstreams`
条目，所以它的地址、超时与请求头只配置一次。

| 字段 | 默认值 | 作用 |
|---|---|---|
| `enabled` | `false` | `false` → **404** |
| `upstream` | `""` | **按名字**选择提供方；留空 = 第一个 `type: "opencode"` 上游。名字匹配不到任何上游是 **503**，绝不静默回退 |
| `path` | `v1/web-search` | 该上游上的检索端点路径 |
| `api_key_env` | `""` | 存放提供方密钥的环境变量，以 `Authorization: Bearer` 发送；留空 = 不签名。命名规则与 `systemone.api_key_env` 相同 |
| `timeout` | `60.0` | 读取超时，依次回退到 `upstream.timeout`、`server.request_timeout`。当 `synthesize: true` 时请保持 **≥ 300**：提供方自己的推理上限是 300 s，网关超时更短会把提供方的 **502** 变成网关的 **504** |

请求体原样转发，提供方的 JSON 原样返回。`model` **绝不**经过网关别名解析 —— 它指的是提供方模型
（例如 `opencode/big-pickle`），也可以省略，由提供方自己挑。

## `request_log`

| 字段 | 默认值 | 作用 |
|---|---|---|
| `enabled` | `true` | 入库开关，**每请求**求值（`proxy._logging_on`）：为 `false` 时连请求/响应体都不复制，也不写任何行。因为这个标志每次调用都重读，保存文件后约 1 秒内生效、无需重启。关闭期间 `/v1/management/overview` 与 `/requests` 会报 `request_log.enabled: false`，于是管理台可以说「日志已关闭」，而不是显示一个看起来像没流量的空列表 |

存储位置与保留期刻意**不**放在本文件里 —— `REQUEST_LOG_DB`、`LLMHUB_DB` 与
`REQUEST_LOG_RETENTION_DAYS` 属于部署层。`HEAD` 请求会被转发但永不入库，所以存活探测不会淹掉日志。

## `security`

两个监听器共用一棵树。分区由**内核分配的本地套接字**决定 —— 绝不看客户端 IP、
`X-Forwarded-For` 请求头或 API Key —— 而 `app/serve.py` 以 `proxy_headers=False` 启动 uvicorn，
所以请求头动不了这个锚点。

一个进程绑定两个套接字（`app/serve.py`）；`app/bridge.py` 是公网区的**策略**模块，不是第二跳代理。
锚点就是 `scope["server"]` 里的监听端口，因此没有任何请求头能把请求从一个区挪到另一个区。
落到每个请求上是这样：

| 区 | 请求 | 回答 |
|---|---|---|
| public | 已列入 allowlist 的路径，**不带** Key | **401** |
| public | 已列入 allowlist 的路径，带有效 Key | 转发 |
| public | 未列出的路径，带有效 Key | **404** `Not found.` |
| internal，`allow_anonymous: true` | 业务 API，不带 Key | 转发 —— 不需要 Key |
| internal，`allow_anonymous: false` | 业务 API，不带 Key | **401** |

管理台会话 Cookie 在两个区都算凭据。带了但错误的 Key 是 **403**，完全没带是 **401**；
内网套接字上 `/` 与 `/health` 始终匿名，无论 `allow_anonymous` 怎么写。

### `security.internal` —— 可信的 `server.port` 套接字

| 字段 | 默认值 | 作用 |
|---|---|---|
| `allow_anonymous` | `true` | `true` → 业务 API **只在这个端口上**免 Key。`false` → 每个业务 API 都需要调用方 Key 或管理台会话；`/` 与 `/health` 仍然匿名，好让容器探测可用 |
| `expose_version` | `true` | 匿名的 `GET /` 是否带上精确构建版本 |
| `expose_docs` | `true` | 是否提供 `/docs`、`/openapi.json` 与随包发布的 Swagger 静态资源。无论这里怎么写，公网区都不提供 |

Ollama 模型管理在这个端口上是可用的：`security.public.admin_endpoints` 从不作用于此，所以
`api/pull` / `api/push` / `api/blobs/*` 会被转发，除非 `security.blocked_paths` 拒绝它们。
但它依然不是开放代理 —— 封闭可转发集合之外的路径在这个套接字上同样是 **404**
（见[路径面](#路径面--可调用路径的封闭集合)）。

### `security.public` —— 面向公网的套接字

| 字段 | 默认值 | 作用 |
|---|---|---|
| `port` | `null` | 打开公网监听的**唯一**开关。`null` = 不开；端口 ≠ `server.port` = 一个进程里两个监听器（共享 Key 缓存、限流桶、登录锁定）；端口 == `server.port` = 纯公网单套接字，**没有任何匿名 HTTP 路径**（容器探测请改用 TCP 检查）。校验范围 1–65535 |
| `path_allowlist` | `DEFAULT_PUBLIC_PATHS`（`GET ^/v1/models$`、`POST ^/v1/chat/completions$`、`^/v1/messages$`、`^/api/generate$`、`^/api/chat$`） | **完整的**公网暴露面：HTTP 方法 → `re.fullmatch` 正则列表。任何未列出的项在**路由前就 404**，带不带 Key 都一样。没有全局方法闸门、没有全局路径 allowlist，也没有 `require_api_key` 开关 —— 这个映射是唯一的正向规则，而通过它的每个请求仍然需要调用方 Key 或管理台会话 Cookie。方法键会被转成大写；非 HTTP 方法是配置错误；无效正则会被**丢弃并告警**（收窄只会更窄）。前缀规则必须写成 `^/v1/.*$`，因为匹配是全匹配。`/v1/management/*` 与 `/admin*` 在代码层面这里一律 404——除非用 `admin_endpoints: allow` 主动打开这块管理面（见下）。列出一个路径并不会凭空造出它：它还必须[可转发](#路径面--可调用路径的封闭集合)或由本地应答，否则代理会返回自己的 **404** |
| `admin_endpoints` | `deny` | **公网监听器**的管理策略，在 `server.port` 上完全不起作用（所以在那里改它是必然的空操作 —— 在可信 LAN 上拉模型、开管理台都是正常操作）。它同时管两件事：**(1) Ollama 自己的模型 API** —— `deny`（默认）阻断破坏性调用（`api/delete` / `api/copy` / `api/create` / `api/push`）、blob 传输（`api/blobs/*`）与 `api/pull`；`readonly` 只放行 `pull`；`allow` 原样转发每个管理调用，**包括 `api/blobs/*`**。**(2) 管理面** —— `allow` 同时也是让 `/admin*` 与 `/v1/management/*` 在公网 socket 上**存在**（不再恒 404）的开关 |

**`admin_endpoints: allow` 会以「前缀」为单位打开管理面。**`bridge_denial()` 会在查 `path_allowlist`
**之前**先判定这两个管理前缀，所以 `allow` 会把管理台外壳与整块 `/v1/management/*` 路由到真实处理器——
不需要任何 allowlist 条目，也很难用正则可靠地描述 `/admin/assets/*`。但它永远不匿名：只有静态外壳与
`POST /v1/management/auth/login` 免凭据，其余管理路由仍需要会话 Cookie，而网关 API Key 从来不是管理
凭据。在 `deny` / `readonly` 下这两个前缀仍是 **404**，把它们写进 `path_allowlist` 也没有任何效果。

**对 Ollama 自己的端点，`admin_endpoints` 不会自己打开一条路径。**在公网套接字上，`bridge_denial()` 在中间件里运行，
会在到达 `proxy.forward()` 内部的管理过滤器*之前*，把 `path_allowlist` 里没有的项全部 **404**。
因此要把 `pull` 暴露到公网，**两者都要**：`admin_endpoints: readonly`（或 `allow`）**以及**一条
匹配的 allowlist 条目：

```yaml
security:
  public:
    admin_endpoints: readonly
    path_allowlist:
      # the map is the COMPLETE public surface - keep the defaults you still want
      GET:
        - ^/v1/models$
      POST:
        - ^/v1/chat/completions$
        - ^/api/pull$
```

路径仍未列出时改模式毫无变化：**404** 来自 allowlist，而管理过滤器本来会回答 **403**。
`api/show` 与 `api/version` 在任何模式下都不会被管理策略阻断。

这组开关很容易只改一半，所以启动日志会把它讲清楚：只要有公网监听器，日志要么列出**同时通过**
模式与 allowlist 的端点，要么在模式为 `readonly`/`allow` 却一条都没列出时给出明确告警：
「`security.public.admin_endpoints=readonly` has no effect: …」。`allow` 打的是另一条日志，因为它
同时会打开管理面。排查 404 之前先看这一行。

`api/blobs/*` 过去即使在 `allow` 下也仍被阻断，因为前缀判断写在模式列表之外。这个问题已修复 ——
`allow` 现在真的会转发 blob（已实测验证：`POST /api/blobs/sha256:…` 到达了 Ollama）。管理路径被
刻意放进可转发集合，所以被拒绝时回答 **403**，而不是声称端点不存在的 **404**；见
[路径面](#路径面--可调用路径的封闭集合)。

**两个判断，不是一个。** `path_allowlist` 与 API Key 由 `app/bridge.py` 分成两步检查，都在请求
中间件里、都在路由之前：

| 步骤 | 函数 | 问题 | 结果 |
|---|---|---|---|
| 1 | `bridge_denial()` | 这个 method+path 在这个端口上存在吗？ | 未列出 → **404**，带着有效 Key 也一样 |
| 2 | `public_auth_denial()` | 这个调用者有凭据吗？ | 没有 → **401** + `WWW-Authenticate: Bearer`，Key 无效 → **403** |

第 2 步属于**分区**：它不能靠配置关掉，也不取决于某条路由自己声明了哪些依赖。因此列进白名单永远
不会把端点变成匿名端点，一个将来漏写 `Depends(require_api_key)` 的 handler 也一样会被公网侧拦住。
这一步排在限流之后（被限速的调用者先看到 **429**）与 CORS 预检应答之后，并且从不作用于内网监听
端口 —— 那些匿名路径（`/admin` 静态资源、`/`、`/health`，以及 `security.internal.allow_anonymous`
之下的全部业务 API）由并非业务端点的路由回答。没有 `require_api_key` 开关可以关掉这套规则。

唯一天生不带凭据的请求就是 CORS 预检：它在上一步就被应答了，第 2 步根本看不到 `OPTIONS`。但对
第 1 步来说它照样是一个 method+path，所以公网侧要让浏览器能用，必须在 `GET:`/`POST:` 旁边补上
`OPTIONS:` 条目，详见下面 `security.cors_origins` 一行。

### `security` —— 两个区共用的加固项

| 字段 | 默认值 | 作用 |
|---|---|---|
| `blocked_paths` | `[]` | 运维方黑名单，**处处生效**（精确名或以 `*` 结尾的前缀），在公网管理黑名单之外额外叠加。在 `proxy.py` 里通过 `SecurityConfig.is_path_blocked()` 检查 |
| `cors_origins` | `[]` | 允许跨站的精确 origin；留空则完全不发 CORS 头。中间件只对列表内的 origin 用**不带 body 的 `204`** 应答预检。预检同样受所在分区的路径策略约束：内网监听任何路径都能应答，公网侧 `OPTIONS` 却和其它方法一样必须在 `security.public.path_allowlist` 里列出，否则预检就是 **404**，浏览器根本发不出真正的请求 |
| `max_body_bytes` | `33554432` | 请求体上限：`Content-Length` 超过它 → 中间件里 **413**；分块传输的 body 在流式读取时计数（`read_limited_body`），然后才由 proxy、messages、websearch、systemone 路径转发 |
| `allow_query_api_key` | `false` | `?api_key=…` 是否算一种凭据传输方式。默认关闭：URL 里的 Key 会落进访问日志。无论如何，请求日志里的 Key 都会被掩码 |
| `allowed_client_ips` | `[]` | CIDR 围栏，列表之外 **403**。它拦的是客户端；从不选择分区 |
| `trusted_proxies` | `[]` | 哪些 CIDR 的 `X-Forwarded-For` 可以被 `client_ip()` 读取 —— 用于 IP 围栏、限流桶与审计行，绝不用于分区。取**最右侧的不受信跳**，所以伪造的最左值挑不到内网地址。放在 nginx 后面时请覆盖该头（`proxy_set_header X-Forwarded-For $remote_addr`）而不是追加 |

### `security.rate_limit`

| 字段 | 默认值 | 作用 |
|---|---|---|
| `enabled` | `false` | 内存令牌桶，超预算时 **429** 并带 `Retry-After`。开了公网监听而这里是关闭时，启动会告警。仅单进程有效 —— 它不是分布式限流器 |
| `per_minute` | `60` | 持续补充速率 |
| `burst` | `20` | 桶容量 = 最大瞬时请求数 |
| `by` | `ip` | 桶的键：`ip` → `client_ip()`（或 `unknown`），`key` → 调用方的 API Key，请求不带 Key 时回退到 IP。超过 5 000 条后清理陈旧桶 |

## 路径面 —— 可调用路径的封闭集合

它不是一个配置节点，而是上面所有与路径有关的设置所处的框架。catch-all 过去会把本地匹配不到的
*任何*路径都转发出去，这让内网监听器成了整个上游 API 的开放代理。`app/proxy.py` 现在持有一个封闭
集合（`FORWARDABLE_PATHS` + `FORWARDABLE_PREFIXES`），集合之外的路径在**两个区**都回答 **404**，
detail 是：

```text
Endpoint '/<path>' is not part of this gateway's API. See /docs (internal zone) or docs/API.md for the callable paths.
```

| 路径 | 家族 | 备注 |
|---|---|---|
| `api/generate` | Ollama 原生 | 有文档的路由，仅 POST |
| `api/chat` | Ollama 原生 | 有文档的路由，仅 POST |
| `api/embed` | Ollama 原生 | 有文档的路由，仅 POST |
| `api/embeddings` | Ollama 原生 | Ollama 已弃用，旧实例仍会应答；不在 schema 里 |
| `api/show` | Ollama 原生 | 不在 schema 里，永不被管理策略阻断 |
| `api/version` | Ollama 原生 | 不在 schema 里，永不被管理策略阻断 |
| `v1/chat/completions` | OpenAI 兼容 | 有文档的路由，仅 POST |
| `v1/completions` | OpenAI 兼容 | 有文档的路由，仅 POST |
| `v1/embeddings` | OpenAI 兼容 | 有文档的路由，仅 POST |
| `api/pull` | Ollama 模型管理 | 公网区：`admin_endpoints` **与** `path_allowlist` 都要 |
| `api/push` | Ollama 模型管理 | 同上 |
| `api/create` | Ollama 模型管理 | 同上 |
| `api/delete` | Ollama 模型管理 | 同上 |
| `api/copy` | Ollama 模型管理 | 同上 |
| `api/blobs/`（前缀） | Ollama 模型管理 | `api/blobs/<digest>`；在 `admin_endpoints: allow` 下转发 |

管理条目是**刻意**留在集合里的：被 `admin_endpoints` 拒绝的调用于是回答 **403**，detail 为
`Endpoint '/<path>' is disabled on this public gateway.`，这比声称端点不存在的 **404** 更清楚。
在内网套接字上它们照常转发。

由网关**本地应答**的路径从不会发往上游，所以不在集合里：

| 本地应答 | 由谁提供 |
|---|---|
| `/`、`/health`、`/probe` | 网关信息、存活检查与上游探测 |
| `/v1/models`、`/models`、`/api/tags`、`/api/ps` | 跨上游聚合，而不是代理到某一个 |
| `/v1/rerank`、`/rerank`、`/api/rerank` | `rerank.py` 打分引擎 |
| `/v1/messages`、`/v1/message` | `messages.py` 请求映射 + 信封/SSE 重建 |
| `/v1/systemone` | `systemone.py`，它自己的 `backend` |
| `/v1/tools/web-search` | `websearch.py`，它自己的提供方上游 |
| `/v1/management/*`、`/admin/*` | 管理 API + 管理台（只认 Cookie；公网区在 `admin_endpoints: allow` 之前恒 404） |
| `/docs`、`/openapi.json` | 内网区，`security.internal.expose_docs` |

**`proxy.forward()` 内部的检查顺序**：点段 / NUL 拒绝（**403**）→ 可转发集合 **404** →
`admin_endpoints`（仅公网）+ `blocked_paths` **403** → 请求体上限 **413** → 模型路由 **404**。
在第二步就被拒的路径永远到不了路由，所以 `default_upstream` 救不活它，而 `blocked_paths` 也只可能
对真实 API 路径产生 **403**。

**仅 POST 的路由。**`main.POST_ONLY_PATHS` 是：

```text
/v1/rerank, /rerank, /api/rerank, /v1/systemone, /v1/tools/web-search,
/api/generate, /api/chat, /v1/chat/completions, /v1/completions,
/api/embed, /v1/embeddings, /v1/messages, /v1/message
```

这些路径上的任何其它方法都是网关 **405**，并带 `Allow: POST`。这份显式列表是必需的，因为
Starlette 更偏好 catch-all 的完全匹配，而不是有文档路由的「方法不对」的部分匹配 —— 没有它，
`GET /api/chat` 会被转发到上游而不是被拒绝。

**Swagger 可见性。**六个透传端点在 catch-all 之前被注册为有文档的路由，因此它们出现在
`/openapi.json` 与 `/docs` 里，带请求 schema、示例、各自的响应媒体类型以及 **404** / **405** 条目：

| 路由 | 响应媒体类型 |
|---|---|
| `POST /api/generate` | `application/json`、`application/x-ndjson` |
| `POST /api/chat` | `application/json`、`application/x-ndjson` |
| `POST /v1/chat/completions` | `application/json`、`text/event-stream` |
| `POST /v1/completions` | `application/json`、`text/event-stream` |
| `POST /api/embed` | `application/json` |
| `POST /v1/embeddings` | `application/json` |

每个 handler 都只是转手调用 `ProxyService.forward()` 的一行代码，所以路由、别名 + 参数注入、
`num_ctx_overrides` 钳制、流式与请求明细入库都不变 —— 注册只换来 schema 可见性与那条 **405**，
别的什么都没有。`api/embeddings`、`api/show` 与 `api/version` 仍不进 schema（由 catch-all 转发）。
`/docs` 只在内网区提供，并受 `security.internal.expose_docs` 控制；公网区永不提供。逐端点契约见
[API_CN.md](API_CN.md)。

## 管理台读取而路由不读的字段

`upstreams[].description`、`upstreams[].vram_total_gb`、`aliases[].description`、
`rerank.endpoint`、整个 `api_models` 节点以及 `rerank.models`（作为选择框回退）存在的意义是渲染或
标注一个视图。它们改不了请求落到哪个后端 —— 只有一个例外值得直说：`api_models` 列表*看起来*像
授权控制，其实不是。把一个没列进去的模型发到 `/v1/chat/completions`，它照样会被路由。

## 已不存在的节点

把它们留在文件里没有任何有用效果；每一个要么带警告迁移，要么被忽略。

| 已移除 | 替代者 | 现在的行为 |
|---|---|---|
| 顶层 `auth:` | `security.internal` / `security.public` | `auth.enabled` 带警告变成 `internal.allow_anonymous` 的反值；`auth.api_keys`、`trusted_networks`、`allow_anonymous_health` 与那些 IP 白名单带警告被忽略 |
| `security.allowed_http_methods` | `public.path_allowlist` 的方法键 | 迁移到那些方法上，然后丢弃 |
| 全局 `security.path_allowlist` | `security.public.path_allowlist` | 只有新节点不存在时才迁移（并告警要求核对暴露面） |
| `security.expose_version` | `security.internal.expose_version` | 迁移 |
| `security.admin_endpoints` | `security.public.admin_endpoints` | 迁移 |
| `security.expose_health_details` | — | 忽略，并在警告里列为空操作 |
| `rerank.embedding_query_prefix` / `embedding_clamp`、`mode: embedding` | `logprobs` 打分 | 字段属于未知键（静默丢弃）；该模式回答 **400** 并解释原因 |
| `config.yaml` 里的 `api_keys:` | SQLite 的 `api_keys` 表 | 告警并忽略 —— 管理台在那里签发与吊销 |
| `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE` / 配置旁的 `.keys` | 管理台写入的 SQLite `api_keys` 表 | 完全不读；启动脚本会从环境里删除并告警 |
| `LLMHUB_PORT` / `LLMHUB_PUBLIC_PORT(S)` / `LLMHUB_HOST` / `LLMHUB_LOG_LEVEL` / `GATEWAY_AUTH_DISABLED` / `LLMHUB_PUBLIC_ADMIN` | `server.*` / `security.*` | 告警；当文件里没写时，公网端口与 `allow_anonymous` 会被当作迁移提示**沿用一次** —— 显式配置永远优先。请从 `.env` 里删掉它们 |
| `LAYA_SERVER_API_KEY` | `SYSTEMONE_SERVER_API_KEY` | 只读取 `systemone.api_key_env` 里的那个名字 |

## 留在 `config.yaml` 之外的设置

| 变量 | 角色 |
|---|---|
| `CONFIG_PATH` | 加载哪个文件 |
| `ADMIN_PASSWORD` | 仅用于管理台登录；它绝不是 Bearer / `X-API-Key` 凭据 |
| `ADMIN_SESSION_TTL` | Cookie 生命周期（默认 12 h，钳制在 5 min…7 d，滑动续期） |
| `REQUEST_LOG_DB` / `LLMHUB_DB` | `requests` 用的数据库文件 / `bench_results` + `api_keys` 用的数据库文件 |
| `REQUEST_LOG_RETENTION_DAYS` | 行保留期（`0` = 永久保留，每小时清理一次） |
| `*_API_KEY` / `*_API_TOKEN`，由 `systemone.api_key_env` / `websearch.api_key_env` 指名 | 提供方凭据；文件里只能写变量名，绝不能写值 |

## 检查一个键是否真的起作用

`Config` 并不禁止额外属性，所以未知的键能解析通过、被丢弃、且不产生任何错误 —— 拼写错误就是这样
活过一次部署的。审计一个文件的方法：

1. 问 schema。文件里的每条键路径都必须是拥有它的那个模型的已声明字段
   （`app/config.py` 里的 `Config` → `ServerConfig`、`SecurityConfig`、…）。模型树上没有的路径就是
   死的 —— 改名、已移除的功能，或者拼错。
2. 问代码。一个已声明字段是活的，当且仅当 `app/config.py` 之外有东西读它 —— 字段名出现在
   `proxy.py`、`management.py`、`main.py`、`bridge.py`、`auth.py` 或某个服务模块里，或者通过某个
   `Config` 辅助方法（`resolve()`、`find_upstream_for_model()`、`default_upstream_obj()`、
   `websearch_upstream_obj()`、`is_path_blocked()`、`PublicSecurityConfig.allows()`）。
   完全没人读的字段就是该删的节点。
3. 测效果，而不是测解析。改这个值，看什么动了：`log_level: debug` 会显示路由决策，
   `GET /v1/management/config` 会显示管理台看到的掩码投影，而改公网端口要么改变一个 **404**、
   要么毫无变化 —— 后者说明监听器根本没被绑定，因为端口只在启动时读取。

这套流程在真实部署里已知能抓到两件事：`default_upstream` 指向一个不存在的上游（静默 404 回退），
以及 `api_models` 列表里的 `- ''` 占位符（被忽略，而且只有当旁边还有一个真实名字时，它才会把一个
推导家族切换成封闭集合模式）。

---

上一篇：[API_CN.md](API_CN.md) · 完整手册：[USAGE_CN.md](USAGE_CN.md) · 部署：[DEPLOY_CN.md](DEPLOY_CN.md)