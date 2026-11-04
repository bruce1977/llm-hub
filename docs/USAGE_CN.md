# LLM Hub — 使用手册

完整文档：配置详解、接口清单、Web 管理台、部署与安全。想先快速了解这是什么、看一眼界面截图、
五分钟跑起来，请回到 [README_CN.md](../README_CN.md)；English：[USAGE.md](USAGE.md)。

**目录** ·
[特性](#特性) ·
[目录结构](#目录结构) ·
[快速开始](#快速开始) ·
[配置模板](#配置模板example-文件) ·
[构建镜像](#构建镜像docker-build) ·
[配置详解](#配置详解configyaml) ·
[集成 LM Studio](#集成-lm-studio) ·
[Web 管理台](#web-管理台) ·
[API 使用](#api-使用) ·
[启动第二个 Ollama 实例](#启动第二个-ollama-实例) ·
[常见问题](#常见问题) ·
[安全](#安全公网暴露必读) ·
[本地开发](#本地开发不依赖-docker)

## 特性

| 能力 | 说明 |
|------|------|
| **多上游路由** | 按请求体中的 `model` 字段，转发到对应端口的 Ollama 实例 |
| **API Key 鉴权** | 支持多 Key 并存，常量时间比较，兼容 `Bearer` / `X-API-Key` |
| **Rerank API** | 提供 `/v1/rerank`，支持生成式（`logprobs`）与向量（`embedding`）两种打分 |
| **联网检索** | `POST /v1/tools/web-search`，把 OpenCode API Gateway 的关键词网页检索收敛到统一入口 |
| **统一模型列表** | `/v1/models` 聚合配置中所有实例与别名，而非代理到单个实例 |
| **别名 + 参数注入** | `qwen3.5:4b-nothink` → 转发 `qwen3.5:4b` 并强制注入 `think: false` |
| **流式透传** | NDJSON（`/api/chat`、`/api/generate`）与 SSE（`/v1/chat/completions`）原样转发 |
| **管理 API** | `/v1/management/*`：上游/显存总览（含今日请求数与今日 token 合计）、config.yaml 各上游**声明**的模型清单（别名优先显示，前缀通配按上游模型列表展开）与运行态设备/显存内存、配置投影（Rerank / System One / Web Search）、请求明细与时序与分布统计（SQLite 埋点）以及每模型测速（启动 / 轮询 / 历史），凭据为管理台会话 Cookie 或网关 API Key |
| **Web 管理台** | Vue 3 + TypeScript + Element Plus + ECharts 单页应用，构建后由网关在 `/admin` 直接提供（镜像内不含 node）：总览、上游、模型清单（配置声明 + 通配展开 + 运行态）、请求明细与图表、每模型测速，界面支持简体中文 / English，本期不提供写配置接口 |
| **模型测速** | Models · LLM 的 `测速` 按钮用同一段提示词跑 N 轮，给出 TTFT、decode tok/s、prefill tok/s：上游自己报了 `eval_duration` / `prompt_eval_duration` / `predicted_ms` 就用服务端计时（`timing_source: server`），否则退回客户端计时并标记；每个「上游 + 模型」保留最近 3 次，悬停即可查历史 |
| **中英双语** | 简体中文（源语言）与英文，账户菜单切换、按浏览器记忆（`localStorage`）；漏翻的键会让构建失败（`MessageSchema`），不会把原始 key 渲染到界面 |
| **配置热重载** | 修改 `config.yaml` 约 1 秒内自动生效，无需重启容器 |
| **Docker 外挂** | 配置文件通过 `/data` 卷挂载 |
| **路由优先级** | 同名模型出现在多个上游时，按 `priority` 数值最小者优先 |

## 目录结构

```
llm-hub/
├── Dockerfile
├── requirements.txt
├── app/
│   ├── main.py       # FastAPI 入口与特殊端点
│   ├── config.py     # 配置模型、加载与热重载、别名解析
│   ├── auth.py       # API Key 鉴权
│   ├── proxy.py      # 反向代理与流式透传
│   ├── rerank.py     # Rerank API 实现
│   ├── request_log.py # 请求明细 SQLite 埋点（异步批量写入）
│   ├── bench.py      # 模型测速：TTFT / decode / prefill 采集
│   ├── bench_store.py # 测速历史（与请求明细共用 llmhub.db，每模型保留 3 份）
│   ├── management.py # Web 管理台 API（/v1/management：只读视图 + 测速）
│   ├── websearch.py  # 联网检索代理（OpenCode API Gateway 上游）
│   └── web/          # 管理台构建产物（`npm run build` 的输出），网关在 /admin 提供服务
├── web/
│   ├── src/          # 管理台前端源码（Vue 3 + TypeScript + Vite + Element Plus + ECharts）
│   └── ...           # 语言文件在 src/i18n/messages（zh-CN.ts 是源语言）
├── docs/
│   ├── screenshots/  # README 用的界面截图（en/ 与 zh/）
│   └── ...           # 接口、部署、管理台实现说明
├── tests/            # pytest 测试（smoke / management / gateway）
└── data/
    └── config.yaml   # 配置文件（挂载到容器 /data）
```

## 快速开始

### 方式一：从 Docker Hub 拉取（推荐）

```bash
# 1. 拉取最新镜像
docker pull bruce1977/llm-hub:latest

# 2. 准备配置与密钥
mkdir -p llm-hub/data
cp data/config.example.yaml llm-hub/data/config.yaml
cp .env.example llm-hub/.env
# 编辑 llm-hub/data/config.yaml 和 llm-hub/.env，填入你的配置

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

# 1. 准备配置与密钥（config.yaml 放到挂载目录，密钥放 .env）
#    notepad data/config.yaml

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

管理台：浏览器打开 `http://localhost:8888/admin/`，用户名 `admin`，密码即 `.env` 里设置的
`ADMIN_PASSWORD`；不设置该变量时页面会显示「管理台未启用」，`/v1/management/*` 一律返回 503。
管理台前端随镜像发布在 `app/web`（`npm run build` 的产物，与模块同级）；上面第 3 步把宿主机的
`app` 目录挂到 `/app`，所以那份也必须是构建过的，否则 `/admin` 会返回 503。

> **密钥通过 `.env` 提供**：`--env-file` 读取其中的 `GATEWAY_API_KEYS` 并注入容器，
> `config.yaml` 里因此不需要写任何密钥。请妥善保管 `.env`，切勿提交到版本库；换密钥时只改这一处即可。
> Windows 下 `--env-file` / `-v` 的路径务必使用盘符形式（`${target folder}`），不要写 `/d/...`（docker 找不到文件）。

日志查看：`docker logs -f llm-hub`

改配置（如 `priority`、`upstreams`）后无需重建镜像，执行 `docker restart llm-hub` 即可；
若只改了 `config.yaml`，约 1 秒后还会被热重载自动生效。

## 配置模板（example 文件）

仓库里附带两份**不带任何真实信息**的模板，复制后填写即可：

| 模板 | 复制为 | 说明 |
|------|--------|------|
| `data/config.example.yaml` | `data/config.yaml` | 网关配置样例，含 `upstreams` / `aliases` / `rerank` / `security` 全字段，已示范 `priority` 路由优先级 |
| `.env.example` | `.env` | 密钥样例，只需把 `GATEWAY_API_KEYS` 换成你自己的随机串（≥16 位） |

```bash
cp data/config.example.yaml data/config.yaml
cp .env.example .env
# 然后编辑 config.yaml 填真实的 upstream base_url，编辑 .env 填真实密钥
```

> `config.yaml` 与 `.env` 已在 `.gitignore` 中忽略，不会被提交；模板 `config.example.yaml` / `.env.example` 保留在版本库，方便他人直接取用。
> 部署目录（如 `${target folder}`）同样放置了这两份模板，用法一致。

## 构建镜像（docker build）

仓库根目录已提供 `Dockerfile`（基于 `python:3.12-slim`）。可以用 `docker build` 显式构建镜像——更适合自定义标签、指定平台或推送到镜像仓库的场景。

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

- `-v ${target folder}:/data`：把宿主机上的 `config.yaml` 挂进容器 `/data`，与 `CONFIG_PATH` 默认路径一致（Windows 用盘符路径）
- `-v ${target folder}/app:/app`（可选）：源码目录挂载。容器内所有 `*.py` 直接位于 `/app`（`main.py`、`config.py`、`auth.py`、`proxy.py`、`rerank.py`），挂载后改代码只需重启容器即可生效，不必重新构建镜像
- `--add-host host.docker.internal:host-gateway`：Linux 上让容器访问宿主机 Ollama；Windows / macOS 的 Docker Desktop 自带该解析，可省略
- `--env-file ${target folder}/.env`：用环境变量文件注入密钥，避免明文写在 `config.yaml`（详见下方「安全」章节）

推送到镜像仓库（可选）：

```bash
docker tag llm-hub:1.0.0 registry.example.com/llm-hub:1.0.0
docker push registry.example.com/llm-hub:1.0.0
```

## 配置详解（config.yaml）

运行时配置采用 YAML —— 允许 `#` 注释，密钥依旧不写入其中。若目录下只有旧版 `config.json` 而没有
`config.yaml`，网关会读取它并打印一条警告，老部署不会因此失效；JSON 本身就是合法 YAML，需要时
直接改名为 `config.yaml` 即可。

### `server` —— 网关自身

| 字段 | 默认 | 说明 |
|------|------|------|
| `host` / `port` | `0.0.0.0` / `8000` | 监听地址与端口 |
| `log_level` | `info` | `debug` / `info` / `warning` / `error` |
| `request_timeout` | `600` | 上游默认超时（秒） |
| `max_concurrency` | `64` | HTTP 连接池上限 |

### `auth` —— 鉴权

```yaml
auth:
  enabled: true
  allow_anonymous_health: true
  allow_access_ip_whitelist: []
```

- **密钥不再写在 `config.yaml`**：`api_keys` 字段已移除，密钥只来自环境变量。若旧配置里还留着
  `api_keys`，启动时会打印警告并忽略它
- 密钥来源：`GATEWAY_API_KEYS` 环境变量与 `GATEWAY_API_KEYS_FILE` 指向的密钥文件（如 `data/.keys`），两者合并去重
- `GATEWAY_API_KEYS`：`GATEWAY_API_KEYS=key1,key2`（逗号/换行分隔）
- `GATEWAY_API_KEYS_FILE`：指向存放密钥的文件路径，适合 Docker/K8s secrets（`docker inspect` 也不会泄露）
- `allow_access_ip_whitelist`：字符串数组，按正则 **fullmatch** 匹配 ASGI 的客户端地址（直连时就是
  **TCP 对端**；网关代码本身**不读取** `X-Forwarded-For`，因为它可被伪造）。命中的地址可以打开
  `/docs` 与 `/openapi.json`，调用网关 API 时也免 API Key 检查。默认 `[]`（无人豁免）；非法正则会
  记录警告并跳过。注意 uvicorn 的 `ProxyHeadersMiddleware` 在**受信代理**（`--forwarded-allow-ips`，
  默认仅 `127.0.0.1`）转发时会用转发头改写客户端地址——经本机反代部署时，命中判定看到的是真实来源，
  因此必须确保反代正确设置并清理 `X-Forwarded-For`
- ⚠️ 启用鉴权（`enabled: true`）却一个 Key 都没有时，网关**拒绝启动**并提示配置方式，避免误暴露无鉴权服务
- `allow_anonymous_health`：`/health` 免鉴权（建议保持 `true`，否则容器健康检查会失败）
- `enabled: false` 可临时关闭鉴权

### `upstreams` —— 后端实例列表（Ollama / LM Studio / OpenCode）

```yaml
upstreams:
- name: local-main
  type: ollama
  base_url: http://host.docker.internal:11434
  models:
  - qwen3:8b
  - qwen3.5:4b
  - bge-m3:latest
  timeout: 600
  description: 本机主实例
  headers: {}
  vram_total_gb: 32.0
```

- `type`：后端类型，决定模型发现与健康探测走哪套接口，缺省为 `ollama`
  - `ollama`：模型列表 `/api/tags`、健康检查 `/api/version`
  - `lmstudio`：OpenAI 兼容协议，模型列表 `/v1/models`、健康检查 `/v1/models`
  - `opencode`：OpenCode API Gateway，模型列表 `/v1/models`、健康检查 `/health`；同时充当联网检索的
    提供方（见下方 `websearch`）
- `models`：**支持前缀通配**，`"qwen3:*"` 可命中 `qwen3:4b`、`qwen3:8b`、`qwen3:14b`。
  通配是**字面前缀**，斜杠分隔的 id 要写成斜杠形式：`"opencode/*"` 才能命中 `opencode/big-pickle`
  （写成 `"opencode:*"` 会一个都匹配不到）
- `model_aliases`：把该实例中冗长的模型 id 映射成短名（见下方 LM Studio 章节）
- `timeout`：覆盖全局超时，大模型建议调大
- `headers`：可选，转发到该实例时附加的自定义请求头（如上游自身也有鉴权）。模型发现
  （`/v1/models`、`/api/tags`、`/probe` 的健康与模型列表探测）同样会带上这些头，因此需要鉴权的
  上游也能被聚合出来
- `priority`：路由优先级（整数，**默认 `0`**）。当同一个模型名在多个上游中都被声明时，
  网关会选中 `priority` **数值最小**的那个上游；数值相同则按 `upstreams` 列表中的声明顺序优先。
  常用于把同一模型同时挂在 GPU 实例与 iGPU/CPU 实例上，让高优实例先承接、其余作兜底
- `vram_total_gb`：可选，该上游所在主机的显存总量（GiB）。该值无法通过 API 探测，
  仅用于 Web 管理台总览页的「已用 / 总量」进度条；不配置则总览中显示已用量、总量为空

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

```yaml
aliases:
  qwen3.5:4b-nothink:
    model: qwen3.5:4b
    params:
      think: false
```

请求 `qwen3.5:4b-nothink` 时，网关会把 `model` 替换为 `qwen3.5:4b`，
并将 `params` 中的字段**强制覆盖**进请求体（同名参数以别名为准）。

### `num_ctx_overrides` —— 按模型名固定上下文长度（避免 Ollama 反复创建）

Ollama 会为「模型 + `options.num_ctx`」的每种组合创建**独立实例**（KV cache 一次性预分配），
客户端每次传不同的 `num_ctx` 就会导致同一模型被反复新建、显存叠加。
这个映射用于**由网关统一钳制**指定模型的上下文长度，忽略客户端请求里携带的 `num_ctx`：

```yaml
num_ctx_overrides:
  qwen3.6:35b-pruned: 65536
  qwen3.5:9b: 16384
```

- **键是真实模型名**（别名/短名解析后的 `resolved.target`，即实际转发给上游的 `model` 字段，通常就是 `ollama list` / `/api/tags` 里看到的名字）。例如别名 `fast` → `qwen3:4b`，要钳制就填 `qwen3:4b`。
- **值是固定的 `num_ctx`**（整数，token 数）。
- **只对 `type=="ollama"` 的上游生效**；LM Studio / OpenAI 兼容上游用的是 `max_tokens`，不认 `num_ctx`，不会被钳制。
- 无论客户端是否显式传 `num_ctx`，最终都以这里的值为准；当覆盖生效时网关会打印 `num_ctx clamped` 日志。
- 支持**热重载**：改完约 1 秒后对新请求生效，无需重启网关。

> 用途示例：大上下文模型（如 35B-pruned）固定 64K 上下文，避免客户端误传更大的 ctx 撑爆显存；
> 或把小模型统一钳到 8K，防止不同客户端请求触发 Ollama 反复重分配 KV cache。

## 集成 LM Studio

LM Studio 提供的是 OpenAI 兼容接口，模型 id 通常带发布方前缀、名字很长
（例如 `qwen/qwen3.5-9b`）。网关原生支持它，并提供「短名映射」把它收敛成 `qwen3.5:9b`。

**1. 确认 LM Studio 已开启本地 API 服务**

在 LM Studio 中加载模型并启动 Local Server（默认端口 `1234`），然后确认可访问：

```bash
curl http://127.0.0.1:1234/v1/models
```

**2. 在 `config.yaml` 里登记为一个上游**

```yaml
name: lmstudio
type: lmstudio
base_url: http://host.docker.internal:1234
models: []
model_aliases:
  qwen3.5:9b: qwen/qwen3.5-9b
timeout: 600
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

```yaml
fast:
  model: qwen3:4b
  upstream: local-main
  params:
    think: false
    num_ctx: 8192
    temperature: 0.2
```

### `rerank` —— 重排配置

```yaml
rerank:
  enabled: true
  mode: logprobs
  models:
  - qwen3-reranker:4b
  normalize: false
  max_concurrency: 8
  return_documents: true
  model_modes:
    bge-m3:latest: embedding
```

两种打分模式：

| 模式 | 适用模型 | 原理 |
|------|----------|------|
| `logprobs` | Qwen3-Reranker 等生成式重排模型 | 构造 yes/no 二分类 prompt，读取首个 token 的 logprobs 做 softmax |
| `embedding` | bge-m3 等向量模型 | 分别取 query 与文档向量，计算余弦相似度 |

`mode` 是全局默认打分方式；当 reranker 混用不同类型模型（如生成式 `qwen3-reranker:4b` + 向量 `bge-m3:latest`）时，
可用 `model_modes` 为单个模型单独指定模式，例如 `{"bge-m3:latest": "embedding"}`。

### `websearch` —— 联网检索配置

```yaml
websearch:
  enabled: true
  upstream: opencode
  path: v1/web-search
  api_key_env: ''
  timeout: 300.0
```

把网页检索收敛到网关：`POST /v1/tools/web-search` 将请求体原样转发到
`<upstream.base_url>/v1/web-search`，上游响应也原样返回（`query`、`results[]`、拼接好的 `text`、
`sources`，`synthesize: true` 时另有 `answer`）。

- 检索方就是一个普通的 `upstreams` 条目（`type: "opencode"`）：地址、超时、附加 header 只配一次，
  同时被模型聚合（`/v1/models`、`/api/tags`）、聊天路由与 `/probe` 复用
- `upstream`：检索提供方名称；留空则自动取**第一个 `type: "opencode"` 的上游**。填了名字却找不到
  对应上游会返回 **503**，不会静默回退到别的上游
- `api_key_env`：保存上游密钥的环境变量名，命中后以 `Authorization: Bearer <值>` 调用上游；
  留空（默认）= 不签名（当前 OpenCode 网关匿名可访问）。密钥只放环境变量，绝不写进 `config.yaml`
- `model` 字段**纯透传**：不传由上游自选，传了也不会用网关的 `aliases` 去解析
- 调用方用网关自身 API Key 鉴权；只接受 POST（否则 **405**），上游不可达 / 超时分别返回 **502** / **504**

请求/响应字段与 curl 示例见 [API 文档](API.md#web-searchopencode-网关代理)。

### `request_log` —— 请求明细入库开关

```yaml
request_log:
  enabled: true
```

管理台「请求明细」页的数据来源：每笔经过网关的请求写入 SQLite（`llmhub.db`，WAL）。

- `enabled`：**逐请求读取**的写库开关，默认 `true`。改成 `false` 后代理不再采集请求/响应体，
  也不再落任何一行明细；因为每次请求都重新读取配置，改完保存即生效（热加载，无需重启）。
  配置的 mtime 每秒最多比对一次，所以开关在约 1 秒内生效（已用真实服务实测）
- 关闭状态下 `/v1/management/overview` 与 `/requests` 会回报 `request_log.enabled: false`，
  管理台据此提示「日志已停用」，避免空列表被误读成没有流量
- 库路径与保留天数属于部署级配置，走环境变量：`REQUEST_LOG_DB`、`REQUEST_LOG_RETENTION_DAYS`
- 记录字段：`ts` / `ts_end` / `duration_ms` / `ttfb_ms`（时间与耗时，`ts`/`ts_end` 精确到秒，
  亚秒耗时看 `duration_ms`）、`method` / `path` /
  `query` / `requested_model` / `model` / `request_body` / `req_size` / `stream` / `client_ip`
  （请求参数）、`status` / `upstream` / `response_body` / `res_size` / `tokens_in` / `tokens_out` /
  `tokens` / `error`（响应结果，token 按输入/输出分两列存，`tokens` 是总量）；
  响应体各截断 8 KB，凭据字段写盘前替换为 `***`；旧库启动时自动 `ALTER TABLE` 补列

## Web 管理台

管理台是 Vue 3 + TypeScript + Element Plus + ECharts 单页应用，源码在 `web/src`（`*.ts` 模块与
`<script setup lang="ts">` 单文件组件，`strict` 模式），构建产物写入 **`app/web`**
并随仓库提交，访问地址 **`http://localhost:8888/admin/`**（由网关自己以静态文件提供，容器内不需要 node；
`app/web` 与模块同级，本地和镜像里的解析路径因此是同一个）。
登录用户名固定为 `admin`，密码来自环境变量 `ADMIN_PASSWORD`，登录成功后签发 12 小时
HttpOnly 会话 Cookie；页面包括 Dashboard、Upstreams、Requests、Models（LLM / Embed / Rerank / System One）、
Tools（Web Search）、Settings 与 About，本期**不提供任何写配置接口**，唯一的“动作”是
Models · LLM 上的每模型测速。
任何管理接口返回 401 时前端会自动登出并跳回登录页；503 不当作鉴权失败，它分别代表
上游不可达、`ADMIN_PASSWORD` 未设置或前端尚未构建。
账户卡固定在面板左下角，点击弹出账户菜单：语言（简体中文 / English，按浏览器记忆）、深色/浅色切换、
设置入口（原侧栏入口已移除）、关于（项目简介、主要功能、动态版本信息、GitHub 地址与公众号二维码，同样不占侧栏位）与退出登录。

Models · LLM 表格与 Upstreams 的行展开列出的是**该上游在 config.yaml 里声明**的模型
（`upstreams[].models`，配了别名的以别名显示；`opencode/*` 这类前缀通配按与路由同一个发现结果
展开成命中的具体模型，实例里装了但没声明的模型不出现）。运行中的 Ollama 模型给出设备与显存/占用；
`opencode` 类型的托管上游没有加载状态，探测一旦有效其声明的模型全部算在线，设备显示 **Cloud**、显存显示「-」。
设备徽标只区分 **GPU / 部分 / CPU / Cloud**，不再显示占用百分比。
Models · LLM 分两个 Tab：**模型清单**（上述表格）与 **API 用法**，后者给出当前支持的两个生成端点
`POST /api/generate`（Ollama 原生）与 `POST /v1/chat/completions`（OpenAI 兼容）的参数表与请求 / 响应示例，
并标注两者都**缺省即流式**（不写 `stream` 时分别走 NDJSON 与 SSE）。
Models · Embed（位于 Rerank 上方）说明两个嵌入端点 `POST /api/embed`（Ollama 原生）与
`POST /v1/embeddings`（OpenAI 兼容）的字段与示例；两端都是原样透传，页面不读取任何配置。
Requests 页的模型下拉会随所选
上游收敛。每次刷新都有可见的进行中状态（`↻` 旋转、顶栏底部进度条、KPI 卡降透明度、图表蒙纱），
不再是静默换数字。页签图标来自 `web/public/favicon.svg`，Vite 原样复制进 `app/web`，
网关在 `/admin/favicon.svg` 提供。

```bash
cd web
npm install
npm run typecheck # vue-tsc --noEmit：校验组件 props、接口载荷与 ECharts 配置
npm run dev      # 开发服务器 http://localhost:5173/admin/，/v1/management 代理到本地网关 8000
npm run build    # 先类型检查，再写入 app/web —— 该目录需要提交，镜像连同 app/ 一起 COPY
```

数据全部来自 `/v1/management/*`。这些接口接受**两种凭据**：管理台会话 Cookie，或网关
API Key（`Bearer` / `X-API-Key`，供脚本与 curl 复用已有的 key）。`ADMIN_PASSWORD` 只是
登录页凭据，把它当作请求头凭据提交一律 401；未设置 `ADMIN_PASSWORD` 时所有接口返回 503。

```bash
# 设置管理员密码（不设置时管理接口统一返回 503）
ADMIN_PASSWORD=<至少 16 位随机串>

# 登录换取 12 小时 HttpOnly 会话 Cookie
curl -c cookie.txt http://localhost:8888/v1/management/auth/login \
  -H "Content-Type: application/json" -d '{"password": "'"$ADMIN_PASSWORD"'"}'

# 之后用 Cookie 认证（浏览器就是这么做的）
curl -b cookie.txt http://localhost:8888/v1/management/overview

# 或者直接用网关 API Key（查询串里的 key 不被接受）
curl -H "X-API-Key: $GATEWAY_API_KEYS" http://localhost:8888/v1/management/overview
```

| 路径 | 说明 |
|------|------|
| `GET /v1/management/overview` | KPI、上游摘要、运行中模型（Ollama 驻留模型带 GPU / 部分 / 纯 CPU 判定，`opencode` 托管模型带别名一并列出，设备为 `"cloud"`、显存为 `null`） |
| `GET /v1/management/upstreams[/{name}]` | 实例健康、显存占用、运行中模型（Ollama 取 `/api/ps`，托管上游按探测有效性整批算在线），以及该上游在 config.yaml 中**声明**的模型（别名优先，前缀通配按上游模型列表展开） |
| `GET /v1/management/models/{llm,rerank,systemone}` | LLM 为按 `upstreams[].models` 声明生成的模型清单（`display`/`alias` 表达别名、`wildcard` 标记通配展开的模型，含运行态设备与显存/占用；托管上游设备为 `"cloud"`），Rerank / System One 为配置只读投影（API Key 一律掩码为 `••••••`） |
| `GET /v1/management/tools/websearch` | Web Search 配置投影，`base_url` 由 provider 上游派生 |
| `GET /v1/management/requests[/{id}]` | 请求明细（按时间/上游/模型/状态/路径筛选、分页、详情抽屉） |
| `GET /v1/management/stats/{timeseries,distribution,failures}` | 时序聚合（每小时/每天的请求数、P95、错误数）、分组占比（上游 / 模型 / 成功失败，驱动三张实心饼图）与失败明细统计 |
| `POST /v1/management/bench` | 启动测速：`{upstream, model, rounds?, max_tokens?, think?}` → `{job_id, status: "pending"}`。任务跑在独立线程 + 独立事件循环上，关掉浏览器也不会中断 |
| `GET /v1/management/bench/{job_id}` | `pending` / `running`（带 `{completed, total, warmup}`）/ `done`（含逐轮完整数据）/ `failed`（附 `error`） |
| `GET /v1/management/bench/history?upstream=&model=` | 该「上游 + 模型」保留的历史记录（默认 3 份），即悬停浮层的数据源 |

**测速口径：** 同一段固定提示词经网关跑 `rounds` 轮（默认 4，首轮为预热，`temperature: 0`，
`max_tokens` 500），逐轮记录 TTFT、decode tok/s、prefill tok/s。上游如果自己报了
`eval_duration` / `prompt_eval_duration` / `predicted_ms`，`timing_source` 记为 `server`；
只能靠本地计时则记 `client`；思考通道漏进输出流（会污染 TTFT）时记 `client*`。
`think: auto` 保留模型默认行为，`off` 强制 `think: false`。结果写进与请求明细同一个
`llmhub.db`，走同一条批量写入队列。

**请求明细落库：** 每次转发（流式 / 非流式 / 上游失败）都会写入 SQLite（WAL），
默认位于 `config.yaml` 同级目录的 `llmhub.db`（容器内 `/data/llmhub.db`）。把
`config.yaml` 的 `request_log.enabled` 设为 `false` 即可完全停止入库——该开关逐请求读取，
保存后无需重启，约 1 秒内停止写入。

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `ADMIN_PASSWORD` | 未设置（管理接口 503） | 管理台登录密码；仅用于换取会话 Cookie，不能作请求凭据 |
| `REQUEST_LOG_DB` | `<config 目录>/llmhub.db` | 明细库路径 |
| `REQUEST_LOG_RETENTION_DAYS` | `30` | 明细保留天数，`0` 表示永久保留 |
| `LLMHUB_DB` | `<config 目录>/llmhub.db` | 测速历史库路径；默认与明细库同一个文件，部署只需要一个数据文件 |

写入通过有界内存队列异步批量完成，不阻塞转发；请求/响应体截断到 8 KB，
`authorization` / `api_key` / `token` / `password` 等字段落库前替换为 `***`。
上游显存总量无法通过 Ollama API 获取，可在该上游配置 `vram_total_gb` 供总览显示。

## API 使用

以下示例均假设网关运行在 `http://localhost:8000`。

### 鉴权

两种方式任选其一：

```bash
-H "Authorization: Bearer sk-gateway-example-key"
-H "X-API-Key: sk-gateway-example-key"
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
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.6:35b-pruned","messages":[{"role":"user","content":"你好"}],"stream":false}'

# Ollama 原生补全 —— 与 Ollama 一样：不写 stream 即流式（NDJSON），raw 跳过内嵌模板
curl http://localhost:8000/api/generate \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","prompt":"写一首关于编程的诗：","raw":true,"stream":false}'

# OpenAI 兼容接口
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"你好"}]}'

# 向量
curl http://localhost:8000/api/embed \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-m3:latest","input":["你好世界"]}'
```

> `/api/generate` 只对 **Ollama 上游**有意义：路径会原样拼到上游 `base_url` 后面，
> LM Studio / OpenAI 兼容的后端并没有这个接口。要跨后端通用请用 `/v1/chat/completions`。

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
  -H "Authorization: Bearer sk-gateway-example-key"
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
  -H "Authorization: Bearer sk-gateway-example-key" \
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

### 联网检索

```bash
curl -X POST http://localhost:8000/v1/tools/web-search \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "Ollama keep_alive 默认值", "top_k": 3}'
```

响应（由检索方原样返回）：

```json
{
  "query": "Ollama keep_alive 默认值",
  "model": "ollama-local/qwen3.5:9b",
  "engine": "opencode-websearch",
  "truncated": false,
  "answer": null,
  "text": "默认情况下，模型会在显存中保留 5 分钟 ...",
  "sources": ["https://docs.ollama.com/faq"],
  "results": [
    {
      "title": "FAQ",
      "url": "https://docs.ollama.com/faq",
      "snippet": "也可以通过 OLLAMA_KEEP_ALIVE 环境变量统一调整所有模型的驻留时间 ...",
      "paragraphs": ["默认情况下，模型会在显存中保留 5 分钟。"]
    }
  ]
}
```

请求字段：`query`（必填）、`top_k`、`max_chars_per_page`、`locale`、`recency`
（`day`/`week`/`month`/`year`）、`synthesize`、`include_sources`、`model`（原样透传）。
`text` 是抓取到的全部段落拼接结果，可直接塞进 prompt；`answer` 仅在 `synthesize: true` 时非 null。

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

然后在 `config.yaml` 里把 `base_url` 指向 `http://host.docker.internal:11435`。

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
| `cors_origins` | `[]` | 显式允许的跨域来源；留空则完全不返回 CORS 头（不再有 `*`） |
| `max_body_bytes` | `33554432` | 请求体上限（32MB），超过返回 413，防大 body 打挂 |
| `allow_query_api_key` | `false` | 是否接受 `?api_key=` 查询参数（默认关，避免密钥进日志/浏览器历史） |
| `expose_health_details` | `false` | 为兼容保留、当前**未被读取**：`/health` 固定只回 `{"ok": true}`，不再回显 `config_path` 或上游 `base_url` |
| `fail_on_weak_keys` | `true` | 启动时检测到弱密钥/占位密钥直接拒绝启动 |
| `allowed_client_ips` | `[]` | 客户端 IP 白名单（支持 CIDR，如 `10.0.0.0/8`），留空不限制 |
| `allowed_http_methods` | `["GET","POST"]` | 只放行这些 HTTP 动词，其余（PUT / PATCH / DELETE / HEAD / OPTIONS）一律返回 **405**。LLM 网关只需 GET（模型/健康/标签）与 POST（对话/重排），这是安全默认值 |
| `path_allowlist` | `[]` | 正则 perimeter 控制：非空时，请求路径必须匹配其中至少一个正则，否则返回 **403**；留空则不过滤。非法正则会被记录并忽略（坏正则绝不会放开网关），例如 `["^/health$", "^/v1/(models\|chat/completions\|rerank)$", "^/api/tags$"]` |
| `rate_limit` | `{"enabled":false,"per_minute":60,"burst":20,"by":"ip"}` | 内存令牌桶限流（单进程）：`by` 选择桶的维度（`ip` 或 `key`），`burst` 为桶容量（瞬时上限），`per_minute` 为持续补充速率；超量返回 **429**。可在反向代理后开启，或作为公网第一道廉价防线 |

> `/docs` 与 `/openapi.json` 的可见性**不在 `security` 段**：只有 `auth.allow_access_ip_whitelist`
> 命中的来源地址能打开（其余一律 **404**），这些地址调用 API 时同时免 API Key。
> 文档页使用收窄后的 CSP，每个端点的 **Parameters** 区都有 `X-API-Key` 输入框，顶部 **Authorize**
> 也可填 `Authorization: Bearer` / `X-API-Key`。

所有响应默认还会带上加固响应头：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、
`Referrer-Policy: no-referrer`、`Cross-Origin-Opener-Policy: same-origin`、
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`，以及严格的 `Permissions-Policy`。
这些头无法关闭（只加固响应，不会泄露数据）。
唯一例外是 `/docs` 与 `/openapi.json`：它们使用收窄后的 CSP（`script-src`/`style-src 'self' 'unsafe-inline'`），
以便内置的 Swagger UI 能正常加载——否则页面会渲染成一片空白；其余响应仍保持 `default-src 'none'`。

> **容器健康检查：** Docker 的 `HEALTHCHECK` 现在改为每小时探测一次 `/health`（`--interval=1h`），
> 而非每 30 秒一次。网关本身不做周期性轮询，只在收到 `/health` 时应答，因此该间隔只是 Docker 侧的存活探测节奏。

### 把密钥移出配置文件（唯一方式）

`config.yaml` 已不再接受 `api_keys`，密钥只能从运行环境注入：

```bash
# 容器 / 进程环境变量，逗号或换行分隔多个 Key
export GATEWAY_API_KEYS="sk-gateway-example-key,sk-gateway-example-two"
# 或者指向一个密钥文件（Docker / K8s secret），文件名例如 data/.keys
export GATEWAY_API_KEYS_FILE=/data/.keys
```

未设置 `GATEWAY_API_KEYS_FILE` 时，网关会**自动读取 `config.yaml` 同目录下的 `.keys`**——所以只要把
`/config/.keys`（或默认布局的 `/data/.keys`）挂进去就够了，不必再加环境变量；显式设置的
`GATEWAY_API_KEYS_FILE` 优先。

网关启动时读取以上来源并合并去重；旧配置里残留的 `api_keys` 只会打印一条忽略警告。
`auth.allow_access_ip_whitelist`（正则数组，按 **TCP 对端**匹配，绝不看 `X-Forwarded-For`）中的地址
可以打开 `/docs` 与 `/openapi.json`，调用网关 API 时也免 Key 检查；白名单非空时即使没有任何 Key
网关也能启动（受信任网络模式，启动日志有警告）。常见拼写错误 `allow_access_ip_whilelist` 会被
自动纠正为 `allow_access_ip_whitelist` 并打印警告。

### System One（laya-server 与 Ollama 代理）

网关可把 System One 结构化决策 API（`POST /v1/systemone`，兼容 Jev 线协议）统一收敛进来。后端两种都支持：`1panel/laya-server`，以及 **Ollama 0.40+**（原生提供该端点）：

- **调用方鉴权**用网关自己的 Key（`.env` / `GATEWAY_API_KEYS`，即 `.keys` 配置）；
- 网关再以 `config.yaml` 里 `systemone.api_key_env`（默认 `SYSTEMONE_SERVER_API_KEY`）环境变量中的密钥对后端签名；
- 请求体 / 响应体**原样透传**，网关不做改写。

`config.yaml` 的 `systemone` 段：

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

- `model` **会被注入到转发给后端的请求体**（仅当它是具体值、且调用方没自带 `model`）；`"local"` 是**哨兵值**，表示不注入。
- laya-server 只接受 `auto` / `english` / `multilingual` / `typed-decisions` 四个字面量，其它值返回 **422**。
- Ollama 要的是**真实模型名**：`auto` → **404**，缺失 → **400**；所以后端换成 Ollama 时把 `model` 改成实际模型（如 `tev1`）。
- 后端密钥只能从环境变量读取，绝不写入 `config.yaml`；Ollama 不需要它，会忽略这个 `Authorization` 头。

详情见 [API 文档](API.md#system-one-决策-apilaya-server-与-ollama-代理)。

### 公网部署 checklist

- [ ] `admin_endpoints` 保持 `deny`（或至少 `readonly`），绝不 `allow`
- [ ] 所有密钥 ≥ 16 位随机串，并且只通过 `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE`（如 `data/.keys`）注入
- [ ] 前面有反向代理（Nginx/Caddy）终止 TLS
- [ ] 如需限制来源，`security.allowed_client_ips` 填可信网段
- [ ] `auth.allow_access_ip_whitelist` 保持 `[]`；确实要开 `/docs` 时只填可信来源——判定用的是 ASGI 客户端地址，直连即 TCP 对端；经本机反代时 uvicorn 会按受信转发头改写它，务必确保反代清理 `X-Forwarded-For`
- [ ] 不要暴露 11434/11435 等上游端口，只暴露网关 8000

## 本地开发（不依赖 Docker）

### 启动脚本（Windows）

`scripts\start-local.bat`（或直接 `scripts\start-local.ps1`）是最短路径：它定位 python、读取 `.env`、找到 `data\config.yaml`，然后调用应用**自己的**规则做启动前检查——缺少依赖、Pydantic 模型拒绝配置、开启鉴权却没有 API Key、弱/占位 Key、端口被占用、控制台未构建——网关启动后打印各入口地址。

```powershell
scripts\start-local.bat                      # 检查通过后启动 uvicorn（自动重载）
scripts\start-local.bat -CheckOnly           # 只做检查，不启动
scripts\start-local.bat -Install -Port 8888  # 先执行 pip install -r requirements.txt
scripts\start-local.bat -NoReload            # 保存 .py 时不重启
```

| 参数 | 说明 |
| --- | --- |
| `-Port` / `-Bind` | 覆盖配置里的 `server.port` / `server.host` |
| `-Config` | 指定 `config.yaml`（默认取 `.env` 的 `CONFIG_PATH`，按镜像的方式相对 `app/` 解析，解析不到则用 `data\config.yaml`） |
| `-EnvFile` | 指定环境变量文件（默认 `<repo>\.env`；shell 中已设置的变量优先于文件） |
| `-Python` | 指定解释器（默认 `python`，其次 `py -3`） |
| `-LogLevel` | 覆盖 `server.log_level` |
| `-NoReload` | 不带 `--reload` 启动 |
| `-Install` | 先执行 `pip install -r requirements.txt` |
| `-CheckOnly` | 打印检查结果后退出 |

脚本在 `app/` 目录下以 `--app-dir app` 启动 uvicorn，工作目录与镜像的 `WORKDIR /app` 一致，因此 `.env` 中的相对路径（`../data/config.yaml`、`../data/.keys`）在本地和容器里解析结果相同。

### 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备配置文件
cp data/config.example.yaml data/config.yaml
# 编辑 data/config.yaml，填入真实的上游地址

# 3. 准备密钥文件（.env）—— 下文的 --env-file 会自动加载
cp .env.example .env
# 编辑 .env：设置 GATEWAY_API_KEYS、SYSTEMONE_SERVER_API_KEY 等

# 4. 启动服务（自动加载 ../.env）
cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env
```

### Windows PowerShell 启动

```powershell
# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备密钥文件（.env）
Copy-Item .env.example .env
# 编辑 .env：设置 GATEWAY_API_KEYS、SYSTEMONE_SERVER_API_KEY 等
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.yaml"

# 3. 启动服务（自动加载 ../.env）
cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env
```

### 指定配置文件运行

```bash
cd app
CONFIG_PATH=../data/config.yaml python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file ../.env
```

```powershell
# Windows PowerShell
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.yaml"
cd app
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file ../.env
```

### 后台运行（Linux/macOS）

```bash
cd app
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file ../.env > ../llm-hub.log 2>&1 &
```

### 注册为 Windows 服务（使用 NSSM）

```powershell
# 安装 NSSM: choco install nssm
nssm install LLMHub "C:\path\to\python.exe" "-m" "uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8000" "--env-file" "../.env"
nssm set LLMHub AppDirectory "D:\workspace\github\llm-hub\app"
nssm set LLMHub AppEnvironmentExtra "CONFIG_PATH=D:\workspace\github\llm-hub\data\config.yaml"
nssm start LLMHub
```

> **说明：**`uvicorn --env-file` 会在启动时一次性读取 `.env` 并把其中的变量注入为环境变量（如 `GATEWAY_API_KEYS`、`SYSTEMONE_SERVER_API_KEY` 等），**不会热重载**——改了 `.env` 后需重启 uvicorn。而 `config.yaml` 的改动仍无需重启即可热加载。

交互式文档：<http://localhost:8000/docs>（仅对 `auth.allow_access_ip_whitelist` 命中的来源开放，其余地址返回 **404**；这些来源同时可免 API Key 调用 API。文档页每个端点的 **Parameters** 区都有 `X-API-Key` 输入框——**Try it out** 后填写，**Execute** 时随该次请求发送；顶部 **Authorize** 则可一次性填入 `Authorization: Bearer` 或 `X-API-Key` 供所有请求使用）
