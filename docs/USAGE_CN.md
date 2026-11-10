# LLM Hub — 使用手册

完整文档：配置详解、接口清单、Web 管理台、部署与安全。想先快速了解这是什么、看一眼界面截图、
五分钟跑起来，请回到 [README_CN.md](../README_CN.md)；English：[USAGE.md](USAGE.md)。
接口文档：[API_CN.md](API_CN.md)。配置逐节点参考：[CONFIG_CN.md](CONFIG_CN.md)。
部署：[DEPLOY_CN.md](DEPLOY_CN.md)。

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
| **API Key 鉴权** | 多 Key 并存，兼容 `Bearer` / `X-API-Key`；校验拿请求里的 Key 算一次 `sha256`，与内存中的摘要集合比对（Key 存在 SQLite 的 `api_keys` 表），明文既不落盘也不参与比较 |
| **Rerank API** | 提供 `/v1/rerank`，使用生成式（`logprobs`）yes/no 打分 |
| **联网检索** | `POST /v1/tools/web-search`，把 OpenCode API Gateway 的关键词网页检索收敛到统一入口 |
| **Anthropic Messages** | `POST /v1/messages`（含单数别名 `/v1/message`）：把 Anthropic 请求转成上游的 chat 格式（Ollama NDJSON 或 OpenAI SSE），回答再包回 Anthropic 信封（`content` 块、`stop_reason`、`usage`）；流式按 `message_start → content_block_delta → message_stop` 的事件序重新编排，不是原样透传 |
| **统一模型列表** | `/v1/models` 聚合配置中所有实例与别名，而非代理到单个实例 |
| **别名 + 参数注入** | `qwen3.5:4b-nothink` → 转发 `qwen3.5:4b` 并强制注入 `think: false`；`aliases` 里的每一项都是一个独立模型（管理台单独成行），`model_aliases` 只改名 |
| **流式透传** | NDJSON（`/api/chat`、`/api/generate`）与 SSE（`/v1/chat/completions`）原样转发 |
| **封闭的 API 暴露面** | **两个区**都只转发网关已声明的路径，其余一律 **404**，内网监听因此不是通向上游整套 API 的开放代理（见[网关转发哪些路径](#网关转发哪些路径)） |
| **管理 API** | `/v1/management/*`：上游/显存总览（含今日请求数与今日 token 合计）、config.yaml 各上游**声明**的模型清单（`model_aliases` 短名优先显示，根 `aliases:` 的派生模型独立成行，前缀通配按上游模型列表展开）与运行态设备/显存内存、配置投影（Rerank / System One / Web Search）、请求明细与时序与分布统计（SQLite 埋点）、每模型测速（启动 / 轮询 / 历史）与每端点的接口测试（按家族给出可用模型并服务端执行），凭据**只有**管理台会话 Cookie——网关 API Key 不是管理凭据 |
| **Web 管理台** | Vue 3 + TypeScript + Element Plus + ECharts 单页应用，构建后由网关在 `/admin` 直接提供（镜像内不含 node）：总览、上游、模型清单（配置声明 + 通配展开 + 运行态）、请求明细与图表、每模型测速、每个 API 文档参数表右上角**测试**按钮打开的浮层测试窗口，以及 API Key 管理（签发 / 吊销，列表只显示掩码）；除 Settings 页（唯一保存 `config.yaml` 的编辑器）外，其余页面不再提供写配置入口 |
| **API Key 管理** | 调用方 Key 是 `llmhub.db` 中 `api_keys` 表的一行，库里只存 `sha256(明文)` 与掩码，**永不存明文**。在管理台签发（`sk-hub-` 开头、共 47 字符）与吊销，新生成的明文只显示一次。管理台是**唯一**来源：没有 seed 变量、没有密钥文件、配置里也没有节点 |
| **模型测速** | Models · LLM 的 `测速` 按钮用同一段提示词跑 N 轮，给出 TTFT、decode tok/s、prefill tok/s：上游自己报了 `eval_duration` / `prompt_eval_duration` / `predicted_ms` 就用服务端计时（`timing_source: server`），否则退回客户端计时并标记；每个「上游 + 模型」保留最近 3 次，悬停即可查历史 |
| **接口测试** | 每个 `API 用法` 标签页里，每个端点「请求参数」标题右侧都有一个**测试**按钮，点开是浮层测试窗口（`/api/generate`、`/v1/chat/completions`、`/v1/messages`、`/api/embed`、`/v1/embeddings`、`/v1/rerank`、`/v1/systemone`、`/v1/tools/web-search`）：模型选择框按 `api_models` 收窄（`/api/...` 原生端点只列 `type: ollama` 上游的模型）且只列名字/别名，文档里的参数基本都能填并**一打开就带着默认值**，可预览将要发送的请求体，结果给出状态码、耗时、实际路由到的模型与别名注入的参数、从 JSON / NDJSON / SSE 抽出的回答。请求由**服务端**用网关自己的服务发出，别名注入与请求明细入库跟外部调用完全一致；需要额外密钥的端点只显示只读 `••••••` |
| **三语界面** | 简体中文（源语言）、繁體中文与英文，账户菜单右侧子菜单切换、按浏览器记忆（`localStorage`）；首次访问按 `navigator.language` 猜（`zh-TW` / `zh-HK` / `zh-MO` / `zh-Hant` → 繁体，`en*` → 英文，其余 → 简体），Element Plus 自带的控件（日期面板、分页）也跟着切换。漏翻的键会让构建失败（`MessageSchema`），不会把原始 key 渲染到界面 |
| **配置热重载** | 修改 `config.yaml` 约 1 秒内自动生效，无需重启容器 |
| **Docker 外挂** | 配置文件通过 `/data` 卷挂载 |
| **路由优先级** | 同名模型出现在多个上游时，按 `priority` 数值最小者优先 |

## 目录结构

```
llm-hub/
├── Dockerfile
├── requirements.txt
├── app/
│   ├── main.py       # FastAPI 入口与特殊端点（/v1/messages 等路由在此注册）
│   ├── config.py     # 配置模型、加载与热重载、别名解析
│   ├── auth.py       # API Key 鉴权
│   ├── serve.py      # 加固入口：同一进程绑定所有监听（proxy_headers=False）
│   ├── bridge.py     # 外网区路径策略（白名单 + 硬 404）——不是第二个代理服务
│   ├── admin_auth.py # 管理台登录会话（admin 密码、12 小时 Cookie、登录失败节奏限制）
│   ├── key_store.py  # 调用方 API Key 的 SQLite 存储（只存 sha256 摘要与掩码，签发/吊销）
│   ├── proxy.py      # 反向代理与流式透传
│   ├── messages.py   # Anthropic Messages 兼容层（请求转换、响应与 SSE 事件重建）
│   ├── rerank.py     # Rerank API 实现
│   ├── systemone.py  # System One 决策代理（laya-server / Ollama）
│   ├── request_log.py # 请求明细 SQLite 埋点（异步批量写入）
│   ├── bench.py      # 模型测速：TTFT / decode / prefill 采集
│   ├── bench_store.py # 测速历史（与请求明细共用 llmhub.db，每模型保留 3 份）
│   ├── api_test.py   # 管理台接口测试的执行引擎（封闭的端点白名单）
│   ├── management.py # Web 管理台 API（/v1/management：只读视图 + 测速 + 接口测试 + API Key 管理 + 设置页保存）
│   ├── websearch.py  # 联网检索代理（OpenCode API Gateway 上游）
│   └── web/          # 管理台构建产物（`npm run build` 的输出），网关在 /admin 提供服务
├── web/
│   ├── src/          # 管理台前端源码（Vue 3 + TypeScript + Vite + Element Plus + ECharts）
│   └── ...           # 语言文件在 src/i18n/messages（zh-CN.ts 是源语言，zh-TW.ts / en.ts 与它逐键对齐）
├── docs/
│   ├── images/       # README 直接引用的图：架构图、界面截图、公众号二维码
│   ├── screenshots/  # 管理台逐页实拍（en/ 与 zh/，由 capture-live.js 连真实网关抓取）
│   └── ...           # 接口（API.md / API_CN.md）、配置逐节点参考（CONFIG.md / CONFIG_CN.md）、部署（DEPLOY.md / DEPLOY_CN.md）、管理台实现说明
├── tests/            # 独立运行的测试脚本（smoke / gateway / management / security / 联机 E2E）
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

> **密钥通过 `.env` 提供**：`--env-file` 读取其中的 `ADMIN_PASSWORD`（以及后端需要时的
> `SYSTEMONE_SERVER_API_KEY`）并注入容器，`config.yaml` 里因此不需要写任何凭据。调用方 API Key
> 之后在管理台签发。请妥善保管 `.env`，切勿提交到版本库。
> Windows 下 `--env-file` / `-v` 的路径务必使用盘符形式（`${target folder}`），不要写 `/d/...`（docker 找不到文件）。

日志查看：`docker logs -f llm-hub`

改配置（如 `priority`、`upstreams`）后无需重建镜像，执行 `docker restart llm-hub` 即可；
若只改了 `config.yaml`，约 1 秒后还会被热重载自动生效。

## 配置模板（example 文件）

仓库里附带两份**不带任何真实信息**的模板，复制后填写即可：

| 模板 | 复制为 | 说明 |
|------|--------|------|
| `data/config.example.yaml` | `data/config.yaml` | 网关配置样例，含 `upstreams` / `aliases` / `rerank` / `security` 全字段，已示范 `priority` 路由优先级 |
| `.env.example` | `.env` | 模板只列这个服务读取的三个变量：`CONFIG_PATH`（注释形式，标注默认值）、`ADMIN_PASSWORD`（这一项必须改，≥32 位随机）、`SYSTEMONE_SERVER_API_KEY`（注释形式，可选）。其余全部在 `config.yaml`；调用方 Key 在 `/admin` 签发，没有任何变量 |

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

本章讲的是你真正会去改的那些节点，以及它们对一次调用的影响。**每个键的逐节点参考**——默认值、
由谁读取、改动何时生效，以及那些坑（未声明的 YAML 键会被静默忽略、`rerank.endpoint` 只是个标签、
`api_models` 里的 `- ''` 占位符会被丢弃、`default_upstream` 按 `upstreams[].name` 解析）——在
[CONFIG_CN.md](CONFIG_CN.md)。

### `server` —— 网关自身

| 字段 | 默认 | 说明 |
|------|------|------|
| `host` / `port` | `0.0.0.0` / `8000` | 监听地址与端口 |
| `log_level` | `info` | `debug` / `info` / `warning` / `error` |
| `request_timeout` | `600` | 上游默认超时（秒） |
| `max_concurrency` | `64` | HTTP 连接池上限 |

### `security` —— 分区、鉴权与加固

网关有**两个信任区**，由**内核分配的本地监听端口**（`scope["server"]`）判定——不看客户端 IP、
不看 `X-Forwarded-For`、也不看 API Key。同一个 socket 不可能既可信又不可信；`app/serve.py`
以 `proxy_headers=False` 启动 uvicorn，因此任何请求头都改不了这个锚点。端口与分区策略**只写在
本文件**：`server.port` 是内网监听，`security.public.port` 打开外网监听。不存在端口环境变量
（见 `.env.example`）。

```yaml
server:
  port: 8000                 # 内网（可信 LAN）监听
security:
  internal:                  # 作用于 server.port 这个 socket 的策略
    allow_anonymous: true    # true = 该端口上业务 API 免 Key
    expose_version: true     # `/` 是否回显构建版本
    expose_docs: true        # /docs、/openapi.json、/static/swagger/*
  public:                    # 作用于外网 socket 的策略
    port: null               # null = 不开；8001 = 双监听；8000（= server.port）= 纯公网单端口
    path_allowlist:          # 方法 -> re.fullmatch 正则列表；这就是外网的全部暴露面
      GET:  ["^/v1/models$"]
      POST: ["^/v1/chat/completions$", "^/v1/messages$", "^/api/generate$", "^/api/chat$"]
```

- **内网区** —— 请求落在 `server.port`，且该端口不等于外网端口：
  - `/` 与 `/health` **恒匿名**（存活探针），不受 `allow_anonymous` 影响。`/healthz` 不是探针别名，
    `allow_anonymous: false` 时它要 Key
  - `allow_anonymous: true` → 其余业务 API（`/v1/models`、`/api/tags`、`/v1/chat/completions` …）
    在本端口免 Key；`false` → 需要有效 Key 或管理台会话 Cookie（无 Key `401`、无效 Key `403`）
  - `expose_docs` 控制 `/docs` / `/openapi.json` / Swagger 静态资源；`expose_version` 控制匿名的
    `/` 是否回显构建版本
  - 管理接口（`/v1/management/*`）与 `/admin` 在本端口可达；管理接口**始终只认管理台会话 Cookie**，
    网关 Key 永远不是管理凭据
- **外网区** —— 请求落在 `security.public.port`：
  - **每个请求都必须带有效 Key 或管理台会话 Cookie，强制且不可关闭**——没有 `require_api_key`
    这类开关
  - 只有 `public.path_allowlist` 里的 `方法 + 路径` 对存在。每个值是**全匹配**正则
    （`re.fullmatch`，不是前缀），key 为全大写 HTTP 方法。末尾斜杠算不同路径；
    **未列出的组合一律 404，带不带 Key 都一样**。非法正则会被丢弃并告警（失败关闭）；
    非法方法名直接让配置校验失败
  - 配置**打不开**的红线：`/v1/management/*`、`/admin*`（404），以及任何含 `..` 段或 NUL 字节的
    路径。因此 `/`、`/health`、`/docs`、`/openapi.json`、`/probe`、`/api/tags`、`/api/ps`、
    `/api/version` 默认都不存在，除非你显式列出；管理与控制台无论如何都是 404
  - `security.public.port: null` = 不开外网监听。`= server.port`（例如都为 `8000`）得到
    **纯公网单 socket**，**没有任何匿名 HTTP 路径**——此时容器 HEALTHCHECK 必须改用 TCP 端口探测，
    不能用 `GET /health`。否则（如内网 `8000`、外网 `8001`）由 `serve.py` 在同一进程绑定两个监听，
    共享 Key 缓存与限流桶
- **API Key 存在 `llmhub.db` 的 `api_keys` 表**，不在 `config.yaml`——配置文件里没有任何凭据。
  每行只存 `sha256(明文)` 与一段显示掩码，**不存明文**。Key 只有一种诞生方式：在 `/admin` →
  **API Key 管理**签发，明文只显示一次（见 [Web 管理台](#web-管理台)）。早期版本的
  `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE` / `.keys` 三种入口都已删除——环境变量、文件、
  `config.yaml` 都不能再注入或复活一把 Key。空表是**正常的首次启动状态**：签发第一把之前，
  所有需要 Key 的请求都返回 401
- **Key 格式**：`sk-hub-` + 40 位 base62 = **47 字符**，约 238 位随机。前缀不是密钥，它的作用是
  让一个疑似泄漏的字符串在日志或客户端配置里一眼可辨。掩码保留前缀与主体首尾各 4 位
  （`sk-hub-FNdc…SVvw`）；从文件导入的旧 Key 形状任意，退化为整串首 8 尾 4。选 SHA-256 而不是
  bcrypt/argon2：238 位随机数没有可暴破的弱口令，而网关在**每一笔**请求上都要校验，慢速 KDF 只会
  给每次调用加几十毫秒、把网关自己变成 DoS 目标
- **校验走内存**：启动时把全部 hash 读成一个集合，`verify()` 只做一次查表，请求路径不碰 sqlite；
  每次签发 / 吊销 / 导入都会重载该集合，所以新 Key 立即可用、吊销立即失效，无需重启
- **管理台登录后的会话 Cookie 在**两个区的业务 API 上**都是有效凭据**：登录拿到的签名
  `llmhub_admin` Cookie 可直接调用 `/v1/models` 等业务端点，浏览器无需持有调用方 Key。有效期由
  `ADMIN_SESSION_TTL` 控制（秒，默认 `43200` = 12 小时，钳制在 5 分钟 ~ 7 天），用过半后每次成功
  请求自动续期。退出登录只清除浏览器 Cookie——服务端没有会话表，被复制出去的 Cookie 在到期或
  `ADMIN_PASSWORD` 变更之前仍然有效
- **Key 自检**：空表本身是正常的（Key 只能在管理台签发）。只有彻底无路可走时网关才**拒绝启动**：
  表为空**且** `ADMIN_PASSWORD` 未设置（管理台无法签发）**且**没有可匿名的内网区——即系统不可能
  存在任何凭据。设置 `ADMIN_PASSWORD` 启动后，到 `/admin` 签发第一把即可

其余 `security.*` 键（`blocked_paths`、`cors_origins`、`max_body_bytes`、`allow_query_api_key`、
`allowed_client_ips`、`trusted_proxies`、`rate_limit`）是**两个区共享**的加固项，
表格见[安全](#安全公网暴露必读)，每个键的默认值见
[CONFIG_CN.md](CONFIG_CN.md#security)。

`admin_endpoints` **不属于**共享项——它在 `security.public` 下，只在外网监听上生效：

| 取值 | 对外网监听的作用 |
|------|------|
| `deny`（默认） | 拦截 `api/delete`、`api/copy`、`api/create`、`api/push`、`api/blobs/*` **以及** `api/pull`；`/admin*` 与 `/v1/management/*` 保持 **404** |
| `readonly` | 只放行 `api/pull`；破坏性调用、`api/blobs/*` 与管理面仍被拦截（404） |
| `allow` | 放行全部模型管理调用，**包括 `api/blobs/*`**，**并打开管理面**：`/admin*` 与 `/v1/management/*` 会被路由到真实处理器 |

`api/show` 与 `api/version` 永不被这项拦截。在 `server.port`（可信 LAN 监听）上这个取值根本不会被
应用，所以在那里改它必然没有任何效果——在 LAN 上拉模型、开管理台都是正常操作。

两半的行为有意不同：

- **Ollama 的端点**是普通路径，取值只负责「撤掉过滤器」，请求仍要过
  `security.public.path_allowlist`。要把 `pull` 暴露到外网，取值与一条 `POST` 规则两者都得有
  （常见问题里 [`admin_endpoints: allow` 没有任何效果](#常见问题)一条列出全部三个原因）。
- **管理面是一个前缀**，在 `bridge_denial()` 里、查白名单**之前**判定，所以 `allow` 会整体打开它——
  没有哪条正则能可靠地枚举 `/admin/assets/*`。但它永远不匿名：只有静态外壳与
  `POST /v1/management/auth/login` 免凭据，其余管理路由仍要会话 Cookie，网关 Key 从来不是管理凭据。

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
- `model_aliases`：**仅改名**——把该实例中冗长的模型 id 映射成短名，此后该模型的展示名与调用名都用短名；
  它不会新增模型（见下方 LM Studio 章节；需要带独立固化参数的新模型，写在根节点 `aliases` 下）
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
- `strict: true`：**带名字**的模型若没有任何上游声明、也没有别名能解析，直接返回 404，
  避免误打到默认实例
- **不带 `model` 的请求恒退回 `default_upstream`，即使 `strict: true` 也一样。** Ollama 的模型管理
  调用（`api/pull`、`api/create`、`api/delete`、`api/copy`、`api/blobs/*`）带的是 `name` 而不是
  `model`，`GET` 则两者都不带。strict 拒绝的是替一个**有名字**的模型瞎猜；空名字不是猜，
  而是「这个请求体里没有模型」的情形
- `default_upstream` 按 **`upstreams[].name`** 匹配。名字悬空（或为 `null`）就解析不到任何上游，
  兜底于是静默变成 **404**，不带模型的 admin 调用也无路可走——改过上游名字后务必回头核对。
  字段细节见 [CONFIG_CN.md](CONFIG_CN.md#default_upstream)

```bash
# routing.strict: true 时同样可用——请求体没有 model，因此落到 default_upstream
#（它必须指向一个真实存在的上游）
curl http://localhost:8000/api/pull \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"name":"qwen3:4b"}'
```

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

这里的每一项都是**一个独立的模型**：`qwen3.5:4b` 依然可以直接调用、也依然带思考运行，
Web 管理台会把 `qwen3.5:4b-nothink` 作为另一条模型列出来（它的设备类型取自目标模型所在实例，
显存显示 `-`，因为那份显存已经计在目标模型名下）。若只是想把名字改短、不带任何独立参数，
请用 `upstreams[].model_aliases`——它只改变一个模型的展示名和调用名，不会新增模型。

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

### `api_models` —— 每类 API 可以选哪些模型

`upstreams[].models` 说的是某个实例**声明**了什么，不是它**承担什么职责**：配置里没有任何地方
告诉管理台 `bge-m3` 是回答 `/api/embed` 的、`qwen3:8b` 是回答 `/api/generate` 的。这个节点就是
那句缺失的说明——每类 API 一个可选列表。**所有** `API 用法` 测试窗口的模型下拉，以及各页面
「可用模型」表、请求/响应示例里的 `model`，都读它（或按下面的规则推导），共用同一份
`/v1/management/api-test/models` 数据：

```yaml
api_models:
  generate: []                       # /api/generate · /v1/chat/completions · /v1/messages
  embed: [bge-m3:latest]             # /api/embed · /v1/embeddings
  rerank: []                         # /v1/rerank
  systemone: []                      # /v1/systemone
  websearch: []                      # /v1/tools/web-search
```

- 列表里可以写真实模型 id、`upstreams[].model_aliases` 短名，或根 `aliases:` 的派生模型名。
  发出去的就是别名本身，所以网关照样会注入它携带的固化参数。
- **写了就是封闭集合**：非空的列表原样成为该家族的全部选项，`source` 回报 `api_models.<family>`。
  这时不再做 provider 收窄，也不再显示「自定义模型…」——增删模型请改配置。
  `- ''` 或纯空白行会被忽略（它们是占位符，不是模型）；全是占位符等同于留空。
- **空列表的含义是「自己推」，不是「没有」**——这个节点之前的配置文件一个字都不用改。
  `rerank` 回落到 `rerank.models`，`systemone` 回落到 `systemone.model`，其余就是全部声明名
  （含别名与通配展开）。每个选择框下方都会写明它读的是哪一处
  （`选项来自 api_models.embed` / `rerank.models` / `upstreams[].models`）。
- **Ollama 原生端点只列 Ollama 模型**：`POST /api/generate` 与 `POST /api/embed` 是 Ollama
  自己的接口，`lmstudio` / `opencode` 上游只实现了 `/v1` 那一侧，所以这两个测试窗口按空列表
  推导时只保留 `type: "ollama"` 的行；同样的模型在 `POST /v1/chat/completions` /
  `POST /v1/embeddings` 的窗口里照常可选。非空的 `api_models` 列表仍按原样展示——写错的名字
  会作为可见的配置问题留在下拉里，而不是悄悄消失。
- 被**其它家族显式认领**的名字会从 `generate` / `chat` 里消失：`api_models` 的 `embed` /
  `rerank` / `systemone` 与 `rerank.models` 都算，两种写法（真实 id 或别名）都算，
  聊天选择框因此绝不会把嵌入、打分或决策模型递给用户选。`api_models.websearch` **不算认领**——
  搜索端点本质上是聊天透传，回答综合提示词的模型本来就该能聊天，托管 provider 的路由模型
  （如 `opencode/auto`）因此仍会出现在**聊天**选择框里。比较时忽略 `:latest`——Ollama 把
  `tev1` 与 `tev1:latest` 当成同一个模型。`systemone.model` 这类**兜底**同样不算认领：同一台机器
  完全可以拿聊天模型回答决策请求，它必须继续可选。
- `websearch` 列的是搜索 provider 自己的模型。**留空**（或只写占位符）时，后端会按
  `websearch.upstream` 把候选收窄到该 provider 声明的模型，`source` 标为
  `upstreams[].models (search provider)`；显式写出列表后严格按列表，不再做这层收窄。
- 「自定义模型…」只出现在**推导**（留空）的家族里：`websearch` 的 opencode-port 自己解析 `model`
  （不传就用 provider 的默认值），`systemone` 的 laya-server 要的是路由枚举（`auto` / `english` /
  `multilingual` / `typed-decisions`）而不是模型 id。配置明确列出可选名字时这一项会消失——
  选项就该从配置里选。
- 没有任何上游服务的名字依然**会显示出来**并标注 `未被声明`：配错了应当给你看见，而不是藏起来。
- 这些列表**只收窄管理台提供的选项**，路由逻辑完全不变——网关照旧接受它原本就接受的模型名，
  所以这个节点不可能把调用方锁在某个已声明的模型之外。

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
- `model_aliases`：建立「对外短名 → LM Studio 真实 id」的映射，键是对外暴露的名字，值是上游真实 id。
  它只负责改名——该模型本身不变，也不会因此多出一个小写形式的 `qwen3.5:9b` 新模型

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
  upstream_api: auto
  options:
    num_ctx: 8192
  keep_alive: 3m
```

只支持一种打分方式：**`logprobs`**——构造 yes/no 二分类 prompt，取首个回答 token 的 logprobs 做
二分类 softmax 得到概率，这正是 `qwen3-reranker:4b` 这类生成式重排模型回答的形式。`model_modes`
仍可按模型覆盖，且被接受的各种同义写法（`generation`、`cross-encoder`、`yes_no` 等）都归一到
`logprobs`。

曾经存在的另一种方式已移除：**`embedding`**（用向量模型分别编码 query 与文档，再算余弦相似度）。
用产生检索向量的同一个模型再打一遍分不会带来任何额外信号，因此现在选中它会返回 **400** 并说明
原因，而不是静默退化成一个没有意义的分数。请改用生成式重排模型，或省去重排这一步。

`options.num_ctx` 要收紧：Ollama 会一次性预分配整个 KV cache，决定显存占用的是上下文长度而不是参数
量——`qwen3-reranker:4b` 用默认的 40960 上下文约需 9.3 GB，改成 8192 只需约 4.0 GB，分数一致。
`keep_alive` 再负责在两次请求之间把它请出显存：重排是脉冲式的，常驻一个 4B 模型只会挤占对话模型的显存。

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

请求/响应字段与 curl 示例见 [API 文档](API_CN.md#web-searchopencode-网关代理)。

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
- **统计只计 API 调用**：明细列表、图表、KPI 与失败统计一律带上
  `path LIKE '/v1/%' OR path LIKE '/api/%'`（`request_log.API_PATH_SCOPE`），所以 `/health`、`/metrics`、
  `/openapi.json` 与 `/admin/*` 的资源请求不会稀释数字。这些路径的行**照样入库**（数据没有丢），
  且 `GET /requests/{id}` 有意不加这个条件，任何一条记录的深链都还能打开
- 记录字段：`ts` / `ts_end` / `duration_ms` / `ttfb_ms`（时间与耗时，`ts`/`ts_end` 精确到秒，
  亚秒耗时看 `duration_ms`）、`method` / `path` /
  `query` / `requested_model` / `model` / `request_body` / `req_size` / `stream` / `client_ip`
  （请求参数）、`status` / `upstream` / `response_body` / `res_size` / `tokens_in` / `tokens_out` /
  `tokens` / `error`（响应结果，token 按输入/输出分两列存，`tokens` 是总量）；
  响应体各截断 8 KB，凭据字段写盘前替换为 `***`；旧库启动时自动 `ALTER TABLE` 补列
- **`HEAD` 请求只转发、不记录**：它没有请求体也没有响应体，监控探活又会持续发送，记下来只会
  淹没真实调用。catch-all 的 `HTTP_METHODS` 里包含 `HEAD`（已无全局方法白名单），
  所以它会正常转发，只是不写明细

## Web 管理台

管理台是 Vue 3 + TypeScript + Element Plus + ECharts 单页应用，源码在 `web/src`（`*.ts` 模块与
`<script setup lang="ts">` 单文件组件，`strict` 模式），构建产物写入 **`app/web`**
并随仓库提交，访问地址 **`http://localhost:8888/admin/`**（由网关自己以静态文件提供，容器内不需要 node；
`app/web` 与模块同级，本地和镜像里的解析路径因此是同一个）。
登录用户名固定为 `admin`，密码来自环境变量 `ADMIN_PASSWORD`，登录成功后签发 12 小时
HttpOnly 会话 Cookie；页面包括 Dashboard、Upstreams、Requests、Models（LLM / Embed / Rerank / System One）、
Tools（Web Search）、API Key 管理、Settings 与 About，会动手的地方只有四处：
Models · LLM 上的每模型测速、每个 `API 用法` 标签页里的接口测试窗口、API Key 管理页的签发与吊销，
以及 Settings 页本身（唯一会校验并保存 `config.yaml` 的编辑器）——前三者只写数据库，不碰 `config.yaml`。
任何管理接口返回 401 时前端会自动登出并跳回登录页；503 不当作鉴权失败，它分别代表
上游不可达、`ADMIN_PASSWORD` 未设置或前端尚未构建。
账户卡固定在面板左下角，点击弹出账户菜单：语言（简体中文 / 繁體中文 / English，右侧子菜单，按浏览器记忆）、深色/浅色切换、
设置入口（原侧栏入口已移除）、**API Key 管理**（同样不占侧栏位）、关于（项目简介、主要功能、动态版本信息、GitHub 地址与公众号二维码）与退出登录。

**API Key 管理**（`/admin/api-keys`）是调用方凭据的签发与吊销之处。表格里每一行只有掩码
（`sk-hub-FNdc…SVvw`）、描述与创建时间——列表响应里既没有明文，也没有 hash；响应中的 `source`
（本台签发 / 环境导入）只留给脚本读，界面上不再占一列。
「新建密钥」弹窗要求填写描述（必填，好让 Key 以后认得出来），随后一次性展示生成的 47 字符明文，
带复制按钮与「我已保存」确认；关掉弹窗就重新拉取列表，此后这把 Key 只能被删除，无法再被找回。
吊销是行内的两步操作（「撤销」→「确认撤销」/「取消」），因为一次误点不该打断正在使用它的客户端。
包括这三个接口在内，所有管理接口都只认管理台会话 Cookie：网关 API Key 是任何客户端都持有的凭据，不能等同于管理员权限。

Models · LLM 表格与 Upstreams 的行展开列出的是**该上游在 config.yaml 里声明**的模型
（`upstreams[].models`，`model_aliases` 的短名作为显示名、根 `aliases:` 的每一项单独成行；`opencode/*`
这类前缀通配按与路由同一个发现结果展开成命中的具体模型，实例里装了但没声明的模型不出现）。
运行中的 Ollama 模型给出设备与显存/占用；派生模型行设备沿用它的目标模型、显存显示「-」（同一个实例只计一次，
KPI「运行中模型数」也不重复计数）；`opencode` 类型的托管上游没有加载状态，探测一旦有效其声明的模型全部算在线，设备显示 **Cloud**、显存显示「-」。
设备徽标只区分 **GPU / 部分 / CPU / Cloud**，不再显示占用百分比。
展开一个上游不会收起其他已经展开的行：可以多行同时打开，每行各自探测，每分钟自动刷新时逐个重新探测。
Models · LLM 分两个 Tab：**模型清单**（上述表格，另有**别名与派生模型**表：名称 / 类型（显示别名与派生模型两种徽标样式相同，只靠文字区分）/ 目标模型 / 上游 / 固化参数——说明不再单列，鼠标停在**名称**或**目标模型**上即可看到）与 **API 用法**，后者给出当前支持的三个生成端点
`POST /api/generate`（Ollama 原生）、`POST /v1/chat/completions`（OpenAI 兼容）与 `POST /v1/messages`（Anthropic 兼容）的参数表与请求 / 响应示例，
并标注 `POST /api/generate` 与 `POST /v1/chat/completions` 都**缺省即流式**（不写 `stream` 时分别走 NDJSON 与 SSE），而 `POST /v1/messages` 缺省返回单个 JSON 信封。
Models · Embed（位于 Rerank 上方）说明两个嵌入端点 `POST /api/embed`（Ollama 原生）与
`POST /v1/embeddings`（OpenAI 兼容）的字段与示例；两端都是原样透传，但页面的「可用模型」表与
示例里的 `model` 同样读 `api_models.embed`，不写死具体模型。
每一份 API 文档的「请求参数」标题右侧都有一个**测试**按钮，点开后是浮层里的**接口测试窗口**
（关掉再开，填过的内容和上一次的答复都还在）：模型选择框按 `api_models` 收窄；`lmstudio` /
`opencode` 上游的模型只出现在 `/v1` 的测试窗口里，因为 `/api/generate` 与 `/api/embed` 是 Ollama
自己的接口。每个选项**只显示名字或别名**（有别名用别名，否则用配置里的声明名），上游与运行状态放在悬停提示里，没人服务的名字
仍列出来并标注「未被声明」；文档里写到的参数基本都能填，**打开时已按初始值填好**，
「恢复默认值」重新填入、「清空」把表单与模型选择清空，「查看请求体」给出将要发送的 JSON；
**模型是真正的下拉框**：打开时选中默认模型，列表就是该家族的全部候选（写进 `api_models`
的就是封闭集合，页面各处的示例也用同一批名字）；只有**留空推导**的 `systemone` / `websearch`
在列表末尾多一项「自定义模型…」，选中后换成可手输的输入框（带候选提示），再选回列表里的
名字即可收起。**模型可以清除**：字段旁的 ×（或下拉里的空白项）把它清空后，请求体里就不带
`model`——需要模型的家族会先被本地拦下并提示「请先选择模型」，`systemone` / `websearch`
则交给端点决定（System One 仍会按配置注入 `systemone.model`）。「恢复默认值」会重新选中
`default_model`，「清空」同样清掉模型；
结果是状态码、耗时、真正流式返回时的首字时间、网关实际路由到的模型与别名注入的参数、
从 JSON / NDJSON / SSE 里抽出的回答，以及原始响应。**有文档默认值的布尔参数是复选框**，
勾选/不勾选都会显式发送 `true` / `false`（不勾选就是 false，而不是「不发」）；其余留空的参数
**直接不发**，让端点用自己的默认值回答。两个 LLM 窗口的 `stream` **默认关闭**（端点本身缺省流式，
但测试窗口先让它返回单个 JSON）；generate 窗口另有 `think` 复选框（默认关闭），chat 窗口提供
OpenAI 兼容的 `reasoning_effort`（`none` 在 Ollama 上关闭思考）。
`systemone` 与 `websearch` 还允许填列表之外的名字，因为这两个端点本来就不经网关解析模型。
请求是在**服务端**发出的（`POST /v1/management/api-test`）：管理台的会话 Cookie 故意不是调用方 API Key，
所以网关会把表单重建为一个真实请求，交给该端点用的同一个服务——别名注入、`num_ctx` 钳制、请求明细入库
与外部调用完全一致；端点自己给出的拒绝（404 模型路由不到 / 功能已停用、400 请求体不合法、
502 / 504 上游失败）也带着原状态码出现在结果里，不会被抹平。功能开关关掉的端点依然显示测试窗口，只多一个「未启用」徽标；响应最多回传 128 KB。
需要额外密钥的端点（上游 `Authorization` 头、`systemone.api_key_env`、`websearch.api_key_env`）
在表单里是一行只读的 `••••••`，旁边标出它来自哪个环境变量——值永远不出服务器。
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

数据全部来自 `/v1/management/*`。这些接口**只认管理台会话 Cookie**。网关 API Key
会分发给每个调用方，不能等同于管理员凭据：否则任一调用方都能读取完整配置、导出所有人的
请求正文并改写上游，因此用它访问管理接口一律 401。`ADMIN_PASSWORD` 只是登录页凭据，
把它当作请求头凭据提交同样 401；未设置 `ADMIN_PASSWORD` 时所有接口返回 503。
同一来源连续 5 次登录失败锁定 15 分钟。

```bash
# 设置管理员密码（不设置时管理接口统一返回 503）
ADMIN_PASSWORD=<至少 16 位随机串>

# 登录换取 12 小时 HttpOnly 会话 Cookie
curl -c cookie.txt http://localhost:8888/v1/management/auth/login \
  -H "Content-Type: application/json" -d '{"password": "'"$ADMIN_PASSWORD"'"}'

# 之后用 Cookie 认证（浏览器就是这么做的；网关 API Key 在这里返回 401）
curl -b cookie.txt http://localhost:8888/v1/management/overview
```

| 路径 | 说明 |
|------|------|
| `GET /v1/management/overview` | KPI、上游摘要、运行中模型（Ollama 驻留模型带 GPU / 部分 / 纯 CPU 判定，`opencode` 托管模型设备为 `"cloud"`、显存为 `null`，根 `aliases:` 的派生模型追加在末尾：`derived: true`、设备沿用目标模型、显存 `null`，`kpi.running_models` 不重复计数） |
| `GET /v1/management/upstreams[/{name}]` | 实例健康、显存占用、运行中模型（Ollama 取 `/api/ps`，托管上游按探测有效性整批算在线，另追加归属该上游的派生模型行），以及该上游在 config.yaml 中**声明**的模型（`model_aliases` 短名优先、前缀通配按上游模型列表展开，末尾是根 `aliases:` 声明的派生模型名） |
| `GET /v1/management/models/{llm,rerank,systemone}` | LLM 为按 `upstreams[].models` 声明生成的模型清单（`display`/`alias` 表达 `model_aliases` 短名、`derived`/`target`/`params`/`description` 表达根 `aliases:` 的派生模型、`wildcard` 标记通配展开的模型，含运行态设备与显存/占用；托管上游设备为 `"cloud"`），`aliases[]` 每项带 `kind`（`rename` / `derived`），Rerank / System One 为配置只读投影（API Key 一律掩码为 `••••••`） |
| `GET /v1/management/tools/websearch` | Web Search 配置投影，`base_url` 由 provider 上游派生 |
| `GET /v1/management/requests[/{id}]` | 请求明细（按时间/上游/模型/状态/路径筛选、分页、详情抽屉） |
| `GET /v1/management/stats/{timeseries,distribution,failures}` | 时序聚合（每小时/每天的请求数、P95、错误数）、分组占比（上游 / 模型 / 成功失败，驱动三张实心饼图）与失败明细统计 |
| `GET /v1/management/api-test/models` | 接口测试的按家族可用模型（按 `api_models` 收窄；Ollama 原生的 `generate` / `embed_ollama` 只保留 `type: "ollama"` 的行）：每行带 `display` / `alias` / `upstream` / `running` / `device` 与掩码后的上游密钥，外加 `default_model` / `free_model` / `enabled` 和掩码后的 provider 密钥及其环境变量名；这份数据要探测所有上游，前端整站共用一份缓存，只在首次打开与点击「重新探测」时取 |
| `POST /v1/management/api-test` | `{kind, body}`——在服务端用该端点自己的网关服务发出这一笔请求，返回 `status` / `ok` / `duration_ms` / `ttft_ms` / `content_type` / `stream` / `requested_model` → `model` / `upstream` / `injected` / `body` / `text` / `truncated` / `error`；`kind` 不在白名单、或需要模型的家族没给 `model` 时是 `400`，而网关自己的拒绝（403 / 404 / 413 / 上游 4xx）作为数据返回，不会被抹平 |
| `POST /v1/management/bench` | 启动测速：`{upstream, model, rounds?, max_tokens?, think?}` → `{job_id, status: "pending"}`。任务跑在独立线程 + 独立事件循环上，关掉浏览器也不会中断 |
| `GET /v1/management/bench/{job_id}` | `pending` / `running`（带 `{completed, total, warmup}`）/ `done`（含逐轮完整数据）/ `failed`（附 `error`） |
| `GET /v1/management/bench/history?upstream=&model=` | 该「上游 + 模型」保留的历史记录（默认 3 份），即悬停浮层的数据源 |
| `GET /v1/management/api-keys` | 调用方 Key 列表（**只认会话 Cookie**）：`{items: [{id, masked, label, source, created_at}], total}`；`source` 为 `console`（本台签发）或 `file`（首次启动从环境导入）。响应里只有掩码，既无明文也无 hash |
| `POST /v1/management/api-keys` | 签发新 Key（**只认会话 Cookie**）：`{label}`，描述必填（空 → `400`，超 100 字符 → `422`），`201` 返回 `{item, secret}`——**这个响应是明文唯一一次离开服务器** |
| `DELETE /v1/management/api-keys/{id}` | 吊销（**只认会话 Cookie**）：`200` → `{"deleted": id}`，`id` 不存在 → `404`。内存校验集合随即重载，下一笔请求即失效 |

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
| `LLMHUB_DB` | `<config 目录>/llmhub.db` | `bench_results`（测速历史）与 `api_keys`（调用方 Key 的 hash 与掩码）共用的库路径；默认与明细库同一个文件，部署只需要一个数据文件 |

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

# 被阻止的管理端点 —— 只在公网监听端口生效（security.public.admin_endpoints: deny）。
# 内网端口（8000）原样转发这个调用，不做管理员过滤。
curl http://localhost:8001/api/delete \
  -H "Authorization: Bearer sk-key" \
  -d '{"name":"model"}'
# 403 {"detail":"Endpoint '/api/delete' is disabled on this public gateway."}
# 想拿到这个 403，前提是 path_allowlist 里已经列出 POST ^/api/delete$；
# 没有这一条，公网分区在管理员过滤之前就先回答 404。
```

### 网关转发哪些路径

网关不是一台盲转发的反向代理：**两个分区**都只把网关自己声明过的路径送到上游，其余一律由网关回答
**404**，无论后端实际实现了什么。因此内网监听端口不是通向上游整套 API 的开放代理。

**转发给持有 `model` 的那个上游：**

| 类别 | 路径 |
|------|------|
| Ollama 原生 | `api/generate`、`api/chat`、`api/embed`、`api/embeddings`、`api/show`、`api/version` |
| OpenAI 兼容 | `v1/chat/completions`、`v1/completions`、`v1/embeddings` |
| Ollama 模型管理 | `api/pull`、`api/push`、`api/create`、`api/delete`、`api/copy`、`api/blobs/*` |

模型管理这些路径是**故意**留在集合里的：在内网监听端口它们本来就是正常操作，在公网监听端口则由
`security.public.admin_endpoints` 决定放行与否 —— 那个过滤器给出的 **403** 比声称端点不存在的 404
更清楚。它们要在公网端口存在，仍然必须先在 `security.public.path_allowlist` 里列出。

**本地回答、永不转发：** `/`、`/health`、`/probe`、`/v1/models`、`/models`、`/api/tags`、`/api/ps`
（跨上游聚合）、`/v1/rerank`（以及 `/rerank`、`/api/rerank`）、`/v1/messages`（以及 `/v1/message`）、
`/v1/systemone`、`/v1/tools/web-search`、`/v1/management/*`、`/admin/*`、`/docs`、`/openapi.json`。

```bash
# 网关没有声明过的路径：两个分区都是 404，带不带 Key 都一样
curl http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-gateway-example-key"
# 404 {"detail":"Endpoint '/v1/audio/transcriptions' is not part of this gateway's API. See /docs (internal zone) or docs/API.md for the callable paths."}
```

有两点后果值得知道：

- `security.blocked_paths` 只有在一条路径确实是网关 API 时才会回答 **403**。未知路径先被 **404**
  拦掉，再屏蔽它没有任何变化。
- 在 `security.public.path_allowlist` 里列出一条路径同样不会凭空造出它：一个不可转发的
  method+path 仍然拿到 proxy 自己那句 **404**。

**两个判断，不是一个。** 在公网端口上，「这条路径存不存在」和「这个调用者能不能用」是两个独立的
步骤，都由 `app/bridge.py` 在请求中间件里按顺序回答：

| 步骤 | 问题 | 结果 |
|---|---|---|
| 1 | 这个 method+path 在这个端口上存在吗？ | 未列出 → **404**，带着有效 Key 也一样 |
| 2 | 这个调用者有凭据吗？ | 没有 Key 也没有管理台会话 → **401**，Key 无效 → **403** |

第 2 步属于**分区**，而不属于某条路由自己声明的依赖，所以它在路由之前执行：`path_allowlist` 里加
一条永远不可能把端点变成匿名端点，一个将来漏写 `Depends(require_api_key)` 的 handler 也一样会被
公网侧拦住。它排在限流之后（被限速的调用者仍然看到 **429**），也排在 CORS 预检应答之后（预检按
设计不携带凭据）。内网监听端口刻意不做这一步的凭据检查 —— 它的匿名路径（`/admin` 静态资源、`/`、
`/health`，以及 `security.internal.allow_anonymous: true` 时的全部业务 API）由并非业务端点的路由回答。

预检是唯一一个不用带凭据就能到达浏览器的请求，但第 1 步照样把它当成一个 method+path：公网端口上
要让浏览器能用，必须在 `GET:`、`POST:` 旁边补上 `OPTIONS:` 条目，否则预检就是 **404**，真正的调用
根本发不出去。预检的应答是 `204` 且**不带任何 body 字节** —— 那里多出的字节会把连接打乱。

**Swagger 里能看到什么。** 六个转发路径是带请求 schema、示例和 404/405 响应的显式文档路由：
`POST /api/generate`、`/api/chat`、`/v1/chat/completions`、`/v1/completions`、`/api/embed`、
`/v1/embeddings`。所有只允许 POST 的路由 —— 这六条加上 `/v1/rerank`、`/rerank`、`/api/rerank`、
`/v1/systemone`、`/v1/tools/web-search`、`/v1/messages`、`/v1/message` —— 遇到其它方法时由网关回答
**405** 并带上 `Allow: POST`，而不是转发。`/docs` 本身只在内网分区提供，受
`security.internal.expose_docs` 控制。

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

### Anthropic Messages（兼容 Claude 客户端）

`POST /v1/messages`（单数别名 `/v1/message` 等价）让 Anthropic SDK、Claude Code 这类客户端直接接入。
`max_tokens` 必填（> 0），`model` 可省略（缺省 `auto`，落到默认上游的首个模型）；鉴权用 `x-api-key`
或 `Authorization: Bearer` 都行。

```bash
curl http://localhost:8000/v1/messages \
  -H "x-api-key: sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5:9b",
    "max_tokens": 256,
    "system": "你是一个简洁的助手。",
    "messages": [{"role": "user", "content": "用一句话解释 rerank"}]
  }'
```

返回是标准 Anthropic 信封（`type: "message"`、`content: [{"type":"text", ...}]`、`stop_reason`、
`usage.input_tokens` / `output_tokens`，计数取自上游自己的 token 统计）。不写 `stream` 时返回单个
JSON；`stream: true` 时按 `message_start → content_block_start → content_block_delta* →
content_block_stop → message_delta → message_stop` 发 SSE。`system` 接受字符串或 content 块数组，
`content` 里的 `image` 块会转成上游的图片字段。字段细节见
[API_CN.md](API_CN.md#消息补全anthropic-兼容)。

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

返回各上游的连通性、模型数量与 Ollama 版本。任一上游不可达时返回 503。`/` 与 `/health` 在**内网区**（`server.port`）**恒匿名**，与 `security.internal.allow_anonymous` 无关。在**外网区**同一路径返回 **404**（即使带有效 Key），除非你在 `security.public.path_allowlist` 里显式列出 `GET ^/health$`。因此这个探针要留在内网端口（8000）。

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

把探针指向**内网**端口（`server.port`）——那里的 `/health` 恒匿名，`security.internal.allow_anonymous`
设成什么都不影响。两种情况会让容器 HEALTHCHECK 失败：去探被声明为 `security.public.port` 的端口
（按设计 → 404，带 Key 也一样），或跑**纯公网**实例（`security.public.port == server.port`）——
它没有任何匿名 HTTP 路径，改为对该端口做 TCP 连接探测。

**某条路径回答 404 `is not part of this gateway's API`？**

这就是封闭的可转发集合，它同样作用于内网监听端口：你没声明过的路径是 **404**，而不是把上游碰巧
提供的任何接口透传出去。可调用的路径清单见[网关转发哪些路径](#网关转发哪些路径)和
[API_CN.md](API_CN.md)。确实需要集合之外的接口，就直接连那台实例。`security.blocked_paths` 不可能是
原因：它回答 **403**，而且只对本来就是网关 API 的路径生效。

**`admin_endpoints: allow`（或 `readonly`）改了没效果？**

三个互相独立的成因，按值得排查的顺序：

1. **你调的是内网端口。** 这个模式只在公网监听端口上强制执行；`server.port` 永远不会应用它，
   所以在内网场景下改它必定是空操作。
2. **路径没有列进公网允许清单。** `security.public.path_allowlist` 就是公网的全部暴露面，它在
   管理员过滤**之前**就对未列出的 method+path 回答 **404** —— 模式本身永远不打开一条路径。公网开放
   `pull` 需要两半都齐：

   ```yaml
   security:
     public:
       port: 8001
       admin_endpoints: readonly        # 允许 pull；delete/copy/create/push/blobs 仍是 403
       path_allowlist:                  # 公网的全部暴露面 —— 留下调用方真正需要的
         GET:  ["^/v1/models$"]
         POST: ["^/v1/chat/completions$", "^/api/pull$"]
   ```

3. **`default_upstream` 没设或者指向一个不存在的上游。** 管理调用的请求体带的是 `name` 而不是
   `model`，于是这次调用解析出空模型名并落到 `default_upstream` —— `routing.strict: true` 也是如此。
   这个名字悬空就没有可去之处，调用 **404**。

模式回顾：`deny`（默认）拦住破坏性调用、`api/blobs/*` 和 `api/pull`；`readonly` 只放行 `pull`；
`allow` 全部转发，**包括 `api/blobs/*`**，并额外打开公网管理面（`/admin*` 与 `/v1/management/*`，
仍只认会话 Cookie）。

**`think: false` 不生效？**

确认 Ollama 版本支持 `think` 参数（0.9+）。较老版本可在别名参数里改用
`{"stop": ["<think>"]}` 作为临时方案。

**Rerank 分数全是 0？**

说明上游没有返回可用的 yes/no token logprobs，于是退化成文本兜底并把答案判为未知
（`rerank.fallback_unknown`，故意取 `0.0`）。把 `log_level` 调成 `debug` 观察原始响应，并确认这个模型
确实是用 `yes` / `no` 回答的生成式重排模型；词表不同时，用 `rerank.yes_token` / `no_token`
（或 `yes_aliases` / `no_aliases`）改成它实际输出的词。

**改了配置没生效？**

配置热重载依赖文件 mtime 变化。某些编辑器或网络挂载（NFS / SMB）可能不更新 mtime，
此时执行 `docker restart llm-hub` 即可。

**某个配置键看起来完全没作用？**

配置模型没有禁止多余属性，所以一个未知或拼错的 YAML 键会被解析、丢弃且不产生任何日志。先对照
[CONFIG_CN.md](CONFIG_CN.md) 检查这个键 —— 那份文档逐节点列出默认值和读取它的代码 —— 再去验证效果
而不是解析结果：`log_level: debug` 会打印路由决策，`GET /v1/management/config` 会给出管理台读到的
掩码投影，公网端口的改动要么改变一个 **404** 要么不变，因为监听 socket 在启动时绑定，需要重启。
三个坑值得点名：`rerank.endpoint` 只是一个标签（路由是固定的）、`api_models` 列表里的 `- ''`
占位项会被丢弃而不是变成一个空选项，以及 `default_upstream` 按 `upstreams[].name` 解析，所以一次改名
就会悄悄让兜底失效。

**工具调用返回 400 / `cannot unmarshal object into ... arguments of type string`？**

部分 OpenAI 兼容客户端在流式拼接工具调用时，会把 `tool_calls[].function.arguments`
发成 JSON **对象**（如 `{"path":"main.py"}`）而非 OpenAI 规定的 JSON **字符串**。
Ollama 的 gin 解析器会严格拒绝此类请求并报 400。网关在转发前会自动把对象/数组
重新序列化为字符串，因此**只要客户端走网关（而非直连 Ollama:11434）即可规避**。
若客户端直连 Ollama，需确保其 SDK 始终以字符串形式发送 `arguments`。

## 安全（公网暴露必读）

网关有**两个信任区**，由内核分配的监听 socket 判定——不看客户端 IP、不看 `X-Forwarded-For`、
也不看 Key。端口与分区策略**只写在 `config.yaml`**（`server.port`、`security.public.port`），
不存在端口环境变量（见 `.env.example`）。两个分区节点之下的加固项对两个区同时生效：

| 字段 | 默认 | 说明 |
|------|------|------|
| `blocked_paths` | `[]` | 额外封禁的路径前缀，如 `["api/show"]` 或 `["api/*"]`——两个区都生效 |
| `cors_origins` | `[]` | 显式允许的跨域来源；留空则完全不返回 CORS 头。预检应答是不带 body 的 `204`，且在公网端口只有当该路径的 `OPTIONS` 也列进 `security.public.path_allowlist` 时才会到达这一步 |
| `max_body_bytes` | `33554432` | 请求体上限（32 MiB）：先按 `Content-Length` 预检，chunked 请求边读边计数，超限直接 413，不会把整个 body 读进内存 |
| `allow_query_api_key` | `false` | 是否接受 `?api_key=` 查询参数（默认关，避免密钥进日志/浏览器历史） |
| `allowed_client_ips` | `[]` | 客户端 IP 围栏（支持 CIDR，如 `10.0.0.0/8`），留空不限制。判定只看真实 TCP 对端 / `client_ip()`，不理可伪造的 `X-Forwarded-For` |
| `trusted_proxies` | `[]` | 可信反向代理的 CIDR 列表：只有这些对端发来的 `X-Forwarded-For` 才会影响 `client_ip()`，用于 IP 围栏、限流分桶与审计日志。`client_ip()` 取**最右侧的不受信跳**，伪造最左值无法伪装内网地址。**它不是分区锚点**——无法把请求在两个区之间搬移 |
| `rate_limit` | `{"enabled":false,"per_minute":60,"burst":20,"by":"ip"}` | 内存令牌桶限流（单进程）：`by` 选 `ip` / `key`，`burst` 为桶容量，`per_minute` 为补充速率；超量返回 **429** 并带 `Retry-After`。**公网实例务必开启**（或由反代限流） |

`security.public.admin_endpoints`（`deny` / `readonly` / `allow`）是唯一**不属于**「两个区共享」的一项：
它**只在外网监听**上生效，同时驱动 Ollama 模型管理过滤与管理面（见上方分区说明）。`deny`（默认）拦住 `api/delete`、`api/copy`、
`api/create`、`api/push`、`api/blobs/*` 和 `api/pull`；`readonly` 放行 `pull`、其余照拦；`allow` 全部
转发，**包括 `api/blobs/*`**。`api/show` 与 `api/version` 永不因管理员过滤被拦。内网监听从不应用这个
模式——在可信 LAN 上拉模型是正常操作，因此对着 `server.port` 改它必定是空操作。

这个过滤器同样**永远不打开一条路径**：它位于 `security.public.path_allowlist` *之后*，未列出的
method+path 已经先被 **404**。所以公网开放 `pull` 需要两半都齐——`admin_endpoints: readonly`（或
`allow`）**并且**列出 `POST: ["^/api/pull$"]`；缺了后者你看到的是白名单的 404，而不是模式没生效。
最小 YAML 与第三个成因（`default_upstream` 悬空）见[常见问题](#常见问题)里的
「`admin_endpoints: allow`（或 `readonly`）改了没效果？」。

两个监听端口都**只转发网关自己声明过的 API 路径**。集合之外的路径在哪个区都是 **404**，与有没有 Key
无关，因此内网监听不是通向上游整套 API 的开放代理；公网白名单只能在这个面之上继续收窄，不能拓宽。
完整的转发清单与本地应答清单见[网关转发哪些路径](#网关转发哪些路径)。

`security.internal.expose_docs` 控制**仅内网区**的 `/docs` / `/openapi.json` / Swagger 静态资源；
外网区永不提供这些，也不再有 `auth.allow_access_ip_whitelist`。文档页使用收窄后的 CSP，每个端点的
**Parameters** 区都有 `X-API-Key` 输入框，顶部 **Authorize** 也可填 `Authorization: Bearer` /
`X-API-Key`。

所有响应默认还会带上加固响应头：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、
`Referrer-Policy: no-referrer`、`Cross-Origin-Opener-Policy: same-origin`、
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`，以及严格的 `Permissions-Policy`。
这些头无法关闭。唯一例外是 `/docs` 与 `/openapi.json`（收窄的 CSP
`script-src`/`style-src 'self' 'unsafe-inline'`），以便内置的 Swagger UI 能正常加载。

> **容器健康检查：** Docker 的 `HEALTHCHECK` 每小时探测一次 `/health`（`--interval=1h`）。把它指向
> 内网端口（`server.port`），那里的 `/health` 恒匿名。纯公网实例没有任何匿名 HTTP 路径——改用
> TCP 端口探测。

### 信任区与客户端 IP 的来源

镜像通过 `python serve.py` 启动，显式设置 uvicorn 的 `proxy_headers=False`。直接执行
`python -m uvicorn main:app` 则会让 `ProxyHeadersMiddleware` 用调用方提交的 `X-Forwarded-For`
改写 `scope["client"]`，这正是过去基于 IP 的控制可被绕过的根因。现在分区可信度完全不依赖该地址：
判定依据是内核分配的**本地监听端口**，调用方无法伪造。

端口与分区只写在 `config.yaml`：

```yaml
server:
  port: 8000        # 内网（可信 LAN）监听
security:
  public:
    port: 8001      # null = 不开；= server.port（8000）= 纯公网；其余 = 双监听
    path_allowlist: # 外网的全部暴露面（方法 -> re.fullmatch 正则）
      GET:  ["^/v1/models$"]
      POST: ["^/v1/chat/completions$", "^/v1/messages$", "^/api/generate$", "^/api/chat$"]
  internal:
    allow_anonymous: true
```

| 分区 | 何时生效 | 普通 API | 管理 / 文档 / `/admin` |
|---|---|---|---|
| `internal` | `server.port`，且该端口不是外网端口 | `security.internal.allow_anonymous: true` 时免 Key（否则要 Key/会话）；`/` + `/health` 恒匿名 | 管理接口只认 Cookie；`/docs`、`/openapi.json`、`/admin` 可访问（文档受 `expose_docs` 控制）——**只发布到内网** |
| `public` | `security.public.port`（如 8001），**或 `= server.port` 时的唯一端口** | **一律要 Key/会话**（无开关）；这条检查属于分区（中间件里的 `bridge.public_auth_denial()`）而不属于路由，所以列进白名单永远不会把端点变成匿名。只有 `public.path_allowlist` 里的 方法+`re.fullmatch` 路径存在，其余 **404**（带不带 Key 都一样） | `/v1/management/*`、`/admin*` 及含 `..`/NUL 的路径**硬 404**；`/docs`、`/openapi.json`、`/`、`/probe`、`/health` 与探查类路径除非显式列出否则 404（管理与控制台永远 404） |

`security.public.port: null` = 不开外网监听。`= server.port` = **纯公网**单 socket，
**没有任何匿名 HTTP 路径**——把容器 HEALTHCHECK 与负载均衡改为 TCP 连接探测，或另开一个内网监听。
否则 `serve.py` 会在同一进程绑定两个监听（共享 Key 缓存与限流桶）。把两个**宿主**端口映射到
同一个**容器**端口并不会产生两个区——容器内只看到一个 socket。

旧环境变量 `LLMHUB_HOST`、`LLMHUB_PORT`、`LLMHUB_LOG_LEVEL`、`LLMHUB_PUBLIC_PORTS`、
`LLMHUB_PUBLIC_PORT`、`LLMHUB_PUBLIC_ADMIN` 与 `GATEWAY_AUTH_DISABLED` 只作为一次性迁移提示被读取
（显式配置优先）并打印警告——请从 `.env` / compose 中删掉。特别是 `LLMHUB_PUBLIC_PORTS` 不再打开
任何监听，请改用 `security.public.port`。

### 把密钥移出配置文件（唯一方式）

已经没有任何 Key 变量了。`config.yaml` 不接受 `api_keys`，`GATEWAY_API_KEYS` 被忽略（Windows 启动
脚本还会把它从环境里删掉），`GATEWAY_API_KEYS_FILE` 与 `.keys` 文件也一并移除。Key 只以 hash 行的
形式存在于 `llmhub.db`，由管理台创建：

```bash
# 1. 设置管理台密码并启动网关
export ADMIN_PASSWORD="$(openssl rand -hex 24)"

# 2. 登录并签发第一把 Key（明文只返回这一次）
curl -s -c jar.txt http://127.0.0.1:8000/v1/management/auth/login \
  -H 'content-type: application/json' -d '{"password":"<你的 ADMIN_PASSWORD>"}'
curl -s -b jar.txt http://127.0.0.1:8000/v1/management/api-keys \
  -H 'content-type: application/json' -d '{"label":"first"}'
```

`config.yaml` 里残留的 `api_keys` 只会打印一条忽略警告。已经不存在「某个 IP 白名单可以免 Key」
这回事——可信度由监听区决定：内网区可通过 `security.internal.allow_anonymous` 免 Key，外网区则
一律要 Key 或管理台会话。只有当系统不可能存在任何凭据时（表为空、`ADMIN_PASSWORD` 未设置、
内网区不允许匿名）网关才**拒绝启动**。

### System One（laya-server 与 Ollama 代理）

网关可把 System One 结构化决策 API（`POST /v1/systemone`，兼容 Jev 线协议）统一收敛进来。后端两种都支持：`1panel/laya-server`，以及 **Ollama 0.40+**（原生提供该端点）：

- **调用方鉴权**用网关自己的 Key（在 `/admin` 签发，存于 `api_keys` 表）；
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

详情见 [API 文档](API_CN.md#system-one-决策-apilaya-server-与-ollama-代理)。

### 公网部署 checklist

- [ ] `security.public.admin_endpoints: allow` 要想清楚再开。它同时做两件事：一是不再过滤 Ollama 的
      管理调用（`api/pull`/`push`/`create`/`delete`/`copy`/`blobs/*` 全放行，且每条仍需自己的
      `path_allowlist` 规则）；二是**在公网监听上打开管理面**——`/admin` 与 `/v1/management/*`
      不再 404。管理面永远不匿名（只有静态外壳与登录握手免凭据，其余仍需会话 Cookie），但公网可达
      的管理台必须配 32 位以上随机 `ADMIN_PASSWORD` 并开启 `security.rate_limit`
- [ ] 管理台只在 LAN 使用时把 `security.public.admin_endpoints` 保持在 `deny`
- [ ] 所有密钥都是随机的：管理台签发的 47 字符 `sk-hub-…`，约 238 位熵，已不存在弱 Key 或导入 Key。
      库里只存摘要，所以 `llmhub.db` 要与密钥文件同等保护
- [ ] 前面有反向代理（Nginx/Caddy）终止 TLS
- [ ] 如需限制来源，`security.allowed_client_ips` 填可信网段（两个区同时生效）
- [ ] 除非内网端口确实在可信 LAN 上，否则保持 `security.internal.allow_anonymous: false`；内网端口
      对外可达时保持 `security.internal.expose_docs: false`。已不存在可免 Key 的 IP 白名单——可信度
      就是监听 socket
- [ ] 在 `config.yaml` 设置 `security.public.port: 8001`（纯公网实例则 `= server.port`），并把
      `security.public.path_allowlist` 收敛到最小的方法+正则对；用 `python serve.py` 启动镜像，
      不要用裸 `uvicorn main:app`
- [ ] 从 `.env` / compose 中删掉所有旧 `LLMHUB_*` 端口变量；管理工作走内网端口 / VPN
      （`/v1/management/*` 与 `/admin` 在外网区硬 404）
- [ ] 从公网验收：只有白名单里的组合有响应（无 Key → 401，带 Key → 200），其余一律 404；
      `/v1/management/*` 与 `/admin` → 404
- [ ] 不要暴露 11434/11435 等上游端口，只暴露网关内网 8000（LAN）与外网 8001（经 TLS 反代）

## 本地开发（不依赖 Docker）

### 启动脚本（Windows）

`scripts\start-local.bat`（或直接 `scripts\start-local.ps1`）是最短路径：它定位 python、读取 `.env`、找到 `data\config.yaml`，然后调用应用**自己的**规则做启动前检查——缺少依赖、Pydantic 模型拒绝配置、`api_keys` 表为空且内网区不允许匿名（失败关闭）、弱/占位 Key、**内网或外网端口被占用**、控制台未构建——然后打印解析后的分区布局和各入口地址。**端口与分区只来自 `config.yaml`**（`server.port`、`security.public.port`）；启动脚本会对 `.env` 里残留的旧 `LLMHUB_*` 端口变量告警，但不会使用它们。它按场景自动选择入口：

- 单个内网监听（默认）：uvicorn + `--no-proxy-headers` + 自动重载。
- 配置了独立的外网监听，或带 `-NoReload`：`python serve.py`，同一进程绑定所有监听端口并执行外网路径策略；无自动重载。

```powershell
scripts\start-local.bat                               # 单监听，uvicorn + 自动重载
scripts\start-local.bat -CheckOnly                    # 只做检查，不启动
scripts\start-local.bat -Install                      # 先执行 pip install -r requirements.txt
scripts\start-local.bat -NoReload                     # 加固入口 serve.py，无自动重载
# 要开外网监听：先在 data\config.yaml 里设 security.public.port: 8001，然后
scripts\start-local.bat -NoReload                     # serve.py 同时绑定 8000 + 8001
```

| 参数 | 说明 |
| --- | --- |
| `-Config` | 指定 `config.yaml`（默认取 `.env` 的 `CONFIG_PATH`，按镜像的方式相对 `app/` 解析，解析不到则用 `data\config.yaml`） |
| `-EnvFile` | 指定环境变量文件（默认 `<repo>\.env`；shell 中已设置的变量优先于文件） |
| `-Python` | 指定解释器（默认 `python`，其次 `py -3`） |
| `-NoReload` | 用加固入口 `serve.py` 替代 uvicorn；无自动重载（开外网监听时也必须用） |
| `-Install` | 先执行 `pip install -r requirements.txt` |
| `-CheckOnly` | 打印解析后的分区布局与结论后退出，不绑定端口 |

> 已移除的参数：`-Port`、`-Bind`、`-LogLevel`、`-PublicPorts`、`-PublicAdmin`。绑定地址、端口与分区
> 请改在 `config.yaml` 里设置。

两种入口都在 `app/` 目录下运行，工作目录与镜像的 `WORKDIR /app` 一致，因此 `.env` 中的相对路径（`../data/config.yaml`）在本地和容器里解析结果相同。手动启动 uvicorn（不经脚本）时务必带 `--no-proxy-headers`，避免调用方提交的 `X-Forwarded-For` 改写客户端 IP。

### 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备配置文件
cp data/config.example.yaml data/config.yaml
# 编辑 data/config.yaml，填入真实的上游地址

# 3. 准备环境变量文件（.env）—— 下文的 --env-file 会自动加载
cp .env.example .env
# 编辑 .env：设置 ADMIN_PASSWORD（后端需要时再设 SYSTEMONE_SERVER_API_KEY）

# 4. 启动单监听开发服务（自动加载 ../.env）
cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env --no-proxy-headers
# 生产 / 双区：cd app && python serve.py（在 config.yaml 里设 security.public.port: 8001 开外网监听）
```

凡是会暴露到本机的场景，优先用 `python serve.py`（或 Windows 启动脚本加 `-NoReload`）：
它在同一进程绑定所有监听端口，并从代码层禁用代理头改写。上面的
`--no-proxy-headers` 让单监听 uvicorn 得到同等保护。

### Windows PowerShell 启动

```powershell
# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备环境变量文件（.env）
Copy-Item .env.example .env
# 编辑 .env：设置 ADMIN_PASSWORD（后端需要时再设 SYSTEMONE_SERVER_API_KEY）
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.yaml"

# 3. 启动单监听开发服务（自动加载 ../.env）
cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env --no-proxy-headers
```

### 指定配置文件运行

```bash
cd app
CONFIG_PATH=../data/config.yaml python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file ../.env --no-proxy-headers
```

```powershell
# Windows PowerShell
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.yaml"
cd app
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file ../.env --no-proxy-headers
```

### 后台运行（Linux/macOS）

```bash
cd app
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file ../.env --no-proxy-headers > ../llm-hub.log 2>&1 &
```

绑定的地址/端口不是 loopback 时，请用 `python serve.py` 替代 uvicorn，以便从代码层禁用代理头改写，
并让 `security.public.port` 声明的外网监听生效。

### 注册为 Windows 服务（使用 NSSM）

```powershell
# 安装 NSSM: choco install nssm
nssm install LLMHub "C:\path\to\python.exe" "-m" "uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8000" "--env-file" "../.env"
nssm set LLMHub AppDirectory "D:\workspace\github\llm-hub\app"
nssm set LLMHub AppEnvironmentExtra "CONFIG_PATH=D:\workspace\github\llm-hub\data\config.yaml"
nssm start LLMHub
```

> **说明：**`uvicorn --env-file` 会在启动时一次性读取 `.env` 并把其中的变量注入为环境变量（如 `ADMIN_PASSWORD`、`SYSTEMONE_SERVER_API_KEY` 等），**不会热重载**——改了 `.env` 后需重启 uvicorn。而 `config.yaml` 的改动仍无需重启即可热加载。调用方 Key 完全不在 `.env` 里：它们存于 `llmhub.db`，在 `/admin` 管理。

交互式文档：<http://localhost:8000/docs>（仅内网区可用，且要求 `security.internal.expose_docs: true`；
外网区对文档一律返回 **404**。文档页每个端点的 **Parameters** 区都有 `X-API-Key` 输入框——
**Try it out** 后填写，**Execute** 时随该次请求发送；顶部 **Authorize** 则可一次性填入
`Authorization: Bearer` 或 `X-API-Key` 供所有请求使用）
