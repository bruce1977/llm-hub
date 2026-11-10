# LLM Hub - 部署指南

网关只有一份代码，两种跑法：**Docker（推荐）** 打好的镜像里已经带管理台前端，挂一个 `data` 卷就能起；
**本地 Python** 适合调上游、验证配置，省掉每次构建镜像。两条路读同一份 `data/config.yaml` 与 `.env`，
之后调用方式完全一样（见 [USAGE_CN.md](USAGE_CN.md#api-使用) 与 [API_CN.md](API_CN.md)）。English: [DEPLOY.md](DEPLOY.md)。

| 场景 | 入口 |
|------|------|
| 生产 / 长期运行 | [快速部署](#快速部署) → `docker run`，配置与数据都在 `data/` |
| 自己构建镜像并发布 | [一键构建 / 发布脚本（Windows）](#一键构建--发布脚本windows) |
| 本机调试，不想用 Docker | [本地运行（不使用 Docker）](#本地运行不使用-docker) |
| 改过管理台前端 | [管理台前端与镜像](#管理台前端与镜像)（`app/web` 是提交进仓库的构建产物） |

## 快速部署

### 前置条件

- Docker 20.10+

### 步骤 1: 准备配置

```bash
cp data/config.example.yaml data/config.yaml
cp .env.example .env
```

编辑 `.env` 设置管理台密码：

```bash
ADMIN_PASSWORD=$(openssl rand -hex 24)
TZ=Asia/Shanghai
```

调用方 API Key 不在这个文件里：登录 `/admin` 后在 API Key 页签发。

编辑 `data/config.yaml` 添加上游实例，参考 `data/config.example.yaml`。

> 配置文件是 YAML（镜像里 `CONFIG_PATH=/data/config.yaml`），可以写注释。升级时如果 `/data` 下只有旧版
> `config.json`，网关会带着警告继续用它，不会用模板覆盖；把文件改名为 `config.yaml` 即可（JSON 本身是合法
> YAML）。读取配置需要 `PyYAML`，已列在 `requirements.txt`。

### 步骤 2: 启动服务

**方式 A: 从 Docker Hub 拉取（推荐）**

```bash
docker pull bruce1977/llm-hub:latest

docker run -d --name llm-hub \
  --restart unless-stopped \
  -p 8888:8000 \
  -v ./data:/data \
  --env-file .env \
  bruce1977/llm-hub:latest
```

**方式 B: 从源码构建**

```bash
docker build -t llm-hub:latest .

docker run -d --name llm-hub \
  --restart unless-stopped \
  -p 8888:8000 \
  -v ./data:/data \
  --env-file .env \
  llm-hub:latest
```

### 步骤 3: 验证

```bash
curl http://localhost:8888/health
curl http://localhost:8888/v1/models -H "Authorization: Bearer YOUR_API_KEY"
```

管理台：浏览器打开 `http://localhost:8888/admin/`，用户名 `admin`、密码为 `.env` 里设置的
`ADMIN_PASSWORD`。未设置该变量时页面显示「管理台未启用」，`/v1/management/*` 一律返回 503；
把 `.env` 里的密码改掉后重启容器即可（旧会话同时失效）。

---

## 一键构建 / 发布脚本（Windows）

`scripts\` 下有两个批处理，可在 cmd 或 PowerShell 里从仓库任意位置运行（脚本自己会切到仓库根目录）：

```bat
:: 只构建：产出 bruce1977/llm-hub:latest 与 :<版本号>
scripts\docker-build.bat

:: 构建并推送到 Docker Hub（需先 docker login）
scripts\docker-publish.bat

:: 覆盖镜像名与版本号
scripts\docker-build.bat registry.example.com/team/llm-hub 1.3.0
```

- 版本号默认读 `app\main.py` 的 `__version__`（即网关 `GET /` 上报的版本），第二个参数可显式指定
- 两个脚本都会先确认 Docker 可用，否则直接退出并返回错误码 1
- `docker-publish.bat` 先构建、成功后才依次推送 `latest` 与版本号标签；推送失败立即停止并提示 `docker login`
- 脚本不含任何 git 操作：提交、打 tag 请在发布前自己完成

## 本地运行（不使用 Docker）

### 手动启动

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp data/config.example.yaml data/config.yaml
cp .env.example .env                                # 填 ADMIN_PASSWORD

cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env --no-proxy-headers
# 正式启动或需要公网监听端口时改用加固入口（禁用转发头改写，
# 按 config.yaml 的 server.port / security.public.port 绑定监听）：
# python serve.py
```

即使是本地 uvicorn 启动也必须加 `--no-proxy-headers`：否则 `ProxyHeadersMiddleware` 会用调用方提交的
`X-Forwarded-For` 改写 `scope["client"]`（当 TCP 对端是 `127.0.0.1` 时），使 IP 围栏失效。`serve.py`
则在代码里设置等价的 `proxy_headers=False`。端口与分区策略只来自 `config.yaml`，不再有端口环境变量。

工作目录必须是 `app/`（与镜像的 `WORKDIR /app` 一致），否则 `.env` 里的 `../data/config.yaml`
解析不到；`--env-file` 指向仓库根的 `.env`。管理台在 `http://localhost:8000/admin`，
前提是 `app/web` 已构建过——见下一节。

### 启动脚本（Windows）

想在宿主机上直接跑网关（调试上游、验证配置），也可以用同一目录下的 `start-local.bat` / `start-local.ps1`：

```bat
:: 单内网监听（端口取 config.yaml 的 server.port），uvicorn + 自动重载
:: （会带 --no-proxy-headers）。要改端口请改 config.yaml，不是在命令行上。
scripts\start-local.bat

:: 只做启动前检查，不启动服务
scripts\start-local.bat -CheckOnly

:: 先装依赖，再启动
scripts\start-local.bat -Install

:: 双区（内网 8000 + 公网 8001）：先在 data\config.yaml 里设置
::   security:
::     public:
::       port: 8001
::       path_allowlist: { GET: ['^/v1/models$'], POST: ['^/v1/chat/completions$'] }
:: 公网端口与内网端口不同时会自动切到加固入口 serve.py。
scripts\start-local.bat

:: 在本地验证与生产完全一致的启动路径（serve.py，无自动重载）
scripts\start-local.bat -NoReload
```

`start-local.ps1` 只接受 `-Config`、`-EnvFile`、`-Python`、`-NoReload`、`-Install` 和
`-CheckOnly` ——**没有** `-Port`、`-Bind`、`-LogLevel`、`-PublicPorts` 或 `-PublicAdmin` 参数。
绑定地址、端口、日志级别与分区策略全部来自 `data\config.yaml` 的
`server.host` / `server.port` / `server.log_level` / `security.public.*` / `security.internal.*`。

它先定位 python 与 `data\config.yaml`、按 `.env` 注入环境变量（shell 里已设置的变量优先），然后调用
应用**自己的**模块跑一遍检查——缺依赖、Pydantic 拒绝配置、弱/占位 Key、失败关闭的 Key 守卫
（零 Key 且没有匿名内网区）、**内网或公网端口被占用**、`app\web` 未构建——全部通过后按场景选择入口：

- **默认（单内网监听）**——在 `app/` 目录下用 `--app-dir app` 启动 uvicorn（与镜像 `WORKDIR /app` 一致），
  带 `--no-proxy-headers` 和自动重载，`.env` 里的 `../data/config.yaml` 在本地和容器里
  解析到同一份文件。
- **config 里声明了不同的公网端口**（`security.public.port` 与 `server.port` 不同）**或带
  `-NoReload`**——改用 `python serve.py`，在同一进程绑定内网与公网监听并执行公网区路径策略；
  无自动重载，改完 `app/*.py` 需手动重启。

用 `-CheckOnly` 可以只打印解析后的分区布局（`internal=…`、`public=…`、配置的 method+path 规则数、
所选入口）而不绑定任何端口。环境里若仍有旧版监听变量，会给出警告（显式 config 优先），应从 `.env`
中删除。

## 管理台前端与镜像

管理台（`/admin`）的前端是**预先构建好再提交**的：`npm run build` 把产物写进 `app/web`，该目录
属于版本库内容，`Dockerfile` 只做 `COPY app/web /app/web`，镜像里没有 node 工具链，构建容器时也不会执行 `npm`。
产物与模块同级，因此本地 `app/web` 与镜像 `/app/web` 走同一个解析路径（和 `app/static` 一样）。
前端源码全部为 TypeScript（`*.ts` 模块 + `<script setup lang="ts">` 单文件组件），`npm run build`
会先执行 `vue-tsc --noEmit`，类型错误直接让构建失败；只做检查可用 `npm run typecheck`。

因此改过 `web/src` 之后，必须在构建镜像**之前**重新生成产物并把 `app/web` 一起提交：

```bat
cd web
npm install
npm run typecheck
npm run build
cd ..
scripts\docker-build.bat
```

只改后端或配置时不需要这一步；`npm run dev`（默认 `http://localhost:5173/admin/`，
把 `/v1/management` 代理到本地 8000 的网关）只用于开发，不产生镜像内容。
若 `app/web/index.html` 缺失，`/admin` 会返回 503 并提示运行 `npm run build`，
其余网关功能不受影响。

> **用 `-v ./app:/app` 覆盖模块目录时**（文档里的热补丁跑法）：挂载会把镜像里的 `/app` 整个换成宿主机的
> `app/`，所以宿主机那份也必须先 `npm run build` 生成 `app/web`，否则 `/admin` 变成 503。
> 只挂 `-v ./data:/data` 的常规跑法不受影响。

---

## 环境变量

`.env.example` 只列出这个服务认识的三个变量：`CONFIG_PATH`（注释形式，标注默认值）、
`ADMIN_PASSWORD`、`SYSTEMONE_SERVER_API_KEY`（注释形式，可选）。端口、分区、白名单、上游与超时
全部在 `data/config.yaml`。**调用方 API Key 不再属于环境变量**——它们在 `/admin` 签发，以 hash
形式存进 `llmhub.db`，任何变量、密钥文件或配置节点都无法再注入。

| 变量 | 必需 | 说明 |
|------|------|------|
| `CONFIG_PATH` | ❌ | 配置文件路径，默认 `/data/config.yaml` |
| `TZ` | ❌ | 时区，默认 `UTC` |
| `SYSTEMONE_SERVER_API_KEY` | ❌ | System One 后端密钥，仅当 `systemone.api_key_env` 指向它时才需要；laya-server 必需，Ollama 0.40+ 后端会忽略该头 |
| `OPENCODE_GATEWAY_API_KEY` | ❌ | 联网检索上游密钥，仅当 `websearch.api_key_env` 指向它时才需要（默认留空 = 不签名） |
| `ADMIN_PASSWORD` | ❌ | Web 管理台登录密码（用户名固定 `admin`），与调用方 API Key 是两套凭据：它只用于 `POST /v1/management/auth/login` 换取 12 小时会话 Cookie，本身当作 `Bearer` / `X-API-Key` 提交仍是 401。所有 `/v1/management/*` 接口**只认**该签名会话 Cookie——网关 Key 会发给每个调用方，在这里一律 401，脚本需先登录并保存 Cookie。未设置时 `/admin` 显示「管理台未启用」，`/v1/management/*` 全部返回 503 |
| `ADMIN_SESSION_TTL` | ❌ | 管理台会话有效期（秒），默认 `43200`（12 小时），钳制在 `300` ~ `604800`（5 分钟 ~ 7 天）。会话使用过半后成功请求会自动续期；退出登录只清除浏览器 Cookie，服务端没有会话表 |
| `REQUEST_LOG_DB` | ❌ | 请求明细 SQLite 路径，默认 `CONFIG_PATH` 同目录的 `llmhub.db`（容器内为 `/data/llmhub.db`）。同一个库文件里共有三张表：`requests`（明细）、`bench_results`（测速历史）与 `api_keys`（调用方 Key 的 hash 与掩码） |
| `LLMHUB_DB` | ❌ | `bench_results` 与 `api_keys` 两张表的库路径，默认同样是 `CONFIG_PATH` 同目录的 `llmhub.db`。只在需要把它们与明细库分开时才设置；三张表默认落在同一个文件，容器只需一个可写 `/data` |
| `REQUEST_LOG_RETENTION_DAYS` | ❌ | 请求明细保留天数，默认 `30`；`0` 表示永久保留 |

> 管理台会把请求明细写入 SQLite（WAL 模式），需要 `/data` 目录可写。若使用只读挂载，
> 请把 `REQUEST_LOG_DB` 指到可写路径，或设为空目录下的默认位置。
> 是否入库由 `config.yaml` 的 `request_log.enabled` 控制（默认 `true`）：该开关逐请求读取，
> 改成 `false` 保存后无需重启容器；配置的 mtime 每秒最多比对一次，因此约 1 秒内停止写入。

> **旧版监听变量——请删除。** `LLMHUB_HOST`、`LLMHUB_PORT`、`LLMHUB_LOG_LEVEL`、
> `LLMHUB_PUBLIC_PORTS`（单值别名 `LLMHUB_PUBLIC_PORT`）、`LLMHUB_PUBLIC_ADMIN` 和
> `GATEWAY_AUTH_DISABLED` **不再是有效控制项**——上表已不列出它们，因为端口、绑定地址、日志级别
> 与分区策略现在只来自 `config.yaml`（`server.host` / `server.port` / `server.log_level`、
> `security.public.port`、`security.internal.allow_anonymous`）。启动时应用仍会把残留值**读取一次**
> 作为迁移提示（例如 `LLMHUB_PUBLIC_PORTS` → `security.public.port`、
> `GATEWAY_AUTH_DISABLED` → `security.internal.allow_anonymous`），打警告，并以显式 config 为准。
> 请从 `.env`、shell 以及 compose / `docker run -e` 里删除这些行；迁移后残留值会被忽略，
> 只会在 preflight 里增加噪音。

---

## 内外网鉴权分工

### 信任锚点

请求属于 `internal` 还是 `public`，取决于它**落在哪个本地监听端口上**（`scope["server"]`，由内核分配），
绝不由客户端 IP、`X-Forwarded-For`、API Key 或任何请求头决定。`serve.py` 设置 `proxy_headers=False`，
让 uvicorn 无法用伪造的 XFF 改写 `scope["client"]`；`auth.peer_ip()` 还会拒绝带「端口为 0」代理改写
指纹的 scope。应用侧自己解析 XFF——只对 `security.trusted_proxies` 中声明的 TCP 对端生效，取
**最右侧的不受信跳**，且只用于 IP 围栏、限流分桶与审计行，绝不改变分区。把两个宿主端口映射到同一个
容器端口**不会**产生两个区——容器只看到一个 socket。

| 锚点 | 配置方式 | 调用方可否伪造 |
|---|---|---|
| 内网监听 socket（内核分配） | `server.port` | 不能 |
| 公网监听 socket（内核分配） | `security.public.port` | 不能 |
| 已声明反代的 TCP 对端 | `security.trusted_proxies`（XFF 仅用于 IP 围栏 / 限流 / 审计） | 不能 |

`security.public.port` 决定部署形态：

- `null`——没有公网监听（纯内网实例）。
- 与 `server.port` 不同（如 `8001`）——**同一进程**内同时有内网与公网监听。
- 与 `server.port` 相同（如 `8000`）——**纯公网**单 socket：没有任何匿名 HTTP 路径（连 `/health`
  也没有），探针改用 TCP 端口检查。

硬性规则：

- 开了双监听（`security.public.port: 8001`）时，**内网**端口（`server.port`，8000）凭 socket 即受信：
  `security.internal.allow_anonymous: true` 下普通 API 免 Key，`/docs`、`/admin` 可访问（文档由
  `security.internal.expose_docs` 控制），管理接口仍需会话 Cookie。不涉及 CIDR 白名单，也没有
  `auth` 节点。**8000 只发布到内网**。
- 落在**公网**端口（8001）上的请求**一律**要有效调用方 Key（或已签名的管理台会话 Cookie）。
  `security.public.path_allowlist` 是现有 method+path 组合的完整清单（`re.fullmatch`）；管理接口、
  文档、`/`、`/probe`、`/health` 都是红线——见下方[公网区](#公网区securitypublicport)。
  `/admin` 返回 **404**（没有 admin 外壳开关）。
- 携带 `X-Forwarded-*` / `X-Real-IP` 的请求，只有当真实 TCP 对端声明在 `security.trusted_proxies` 里时，
  才用最右不可信跳参与 IP 围栏 / 限流 / 审计；它永不改变分区。这能防止常见的 `^127\.0\.0\.1$` 围栏
  被伪造的最左侧跳满足。
- 零 API Key 且没有匿名内网区时**失败关闭**：网关**拒绝启动**并打 ERROR。「匿名」现在只表示
  `security.internal.allow_anonymous: true` 作用于真实的内网 socket——既不是全局模式，也不适用于公网区。
- 在**内网** socket 上 `/` 与 `/health` **永远**匿名（存活 / 信息探针），与 `allow_anonymous` 无关。
  在**公网** socket 上它们返回 **404**（除非显式列入 `security.public.path_allowlist`，且即便列入仍需
  Key）：**公网端口上没有匿名的 `/health`**。容器 HEALTHCHECK 与负载均衡要指向内网端口（`server.port`）。
  纯公网实例（`security.public.port == server.port`）根本没有匿名 `/health`——把镜像的 HTTP HEALTHCHECK
  换成 TCP 端口检查，或额外开一个内网监听。
- 后端 Ollama 端口（11434 等）**不要**映射公网，否则请求绕过网关，整套鉴权失效。

### 内网区（`server.port`）

受信的 LAN socket。`/` 与 `/health` **永远匿名**（存活 / 信息探针），不受
`security.internal.allow_anonymous` 影响。该开关只管其他业务 API：`true` 时仅在这个端口上免 Key，
`false` 时需要调用方 Key 或管理台会话。`/docs`、`/openapi.json` 和 Swagger 资源只在
`security.internal.expose_docs: true` 时提供；匿名的 `/` 只在 `security.internal.expose_version: true`
时暴露精确版本号。`/v1/management/*` 始终要求管理台会话 Cookie——网关 API Key 在这里一律 401（两个区都是）。

Key 守卫也在这里。调用方 Key 只能在管理台签发，所以 `api_keys` 表为空是**正常的首次启动状态**：
在登录 `/admin` 签发第一把之前，所有需要 Key 的请求都返回 401。只有彻底无路可走时启动才会拒绝——
表为空、`ADMIN_PASSWORD` 未设置（管理台无法签发）、且没有匿名内网区。

### 公网区（`security.public.port`）

面向公网的 socket。**每个请求都需要有效调用方 Key 或已签名的管理台会话 Cookie**——没有
`allow_anonymous` 开关，公网区永远不能匿名。管理台会话 Cookie（`llmhub_admin`）在两个区都被接受；
网关 API Key 仍然打不开 `/v1/management/*`。

只有列入 `security.public.path_allowlist` 的 **method + 正则** 组合才存在。键是 HTTP method，值是
完整匹配正则（`re.fullmatch`，不是前缀——`^/v1/models$` 匹配不了 `/v1/models/`；末尾斜杠算不同路径）。
method 也要匹配，任何未列出的 method+path 在路由之前就返回 **404**（带不带 Key 都一样，绝不是 403）。
无效正则会被丢弃并打警告（失败关闭）；无效的 method key 会触发 config `ValidationError`。在允许的路径上
用错 method 在这里同样是 404。现在没有全局 method 白名单——这份映射*就是*公网面，新增端点必须显式改 config。

公网 socket 上的红线——管理接口、文档、`/`、`/probe`、`/health` 全都不可达：

- `/v1/management/*`、`/admin` 和 `/admin/*`，以及含 `..` 段或 NUL 字节的路径，
  **即使在 `security.public.path_allowlist` 里列出也是代码层硬 404**。特别是 `/admin` 返回 404
  ——公网区没有 admin 外壳开关，也不提供静态管理台。
- `/docs`、`/openapi.json`、`/static/swagger/*` 在代码层就是内网专属。
- `/`、`/probe`、`/health` 不在默认 allowlist 里；即便加上也仍需 Key——**公网区没有任何匿名 HTTP 路径**。
  纯公网实例用 TCP 端口检查。

### 方案 A：容器只有一个端口——纯内网或纯公网

容器内只有一个监听端口就意味着**只有一个区**：容器只看到**一个 socket**，两个宿主端口对应的
`scope["server"]` 完全相同。把**两个不同宿主端口指向同一个容器端口不会产生两个区**——任何反向代理、
IP 白名单或请求头都拆不开这个 socket。在 `config.yaml` 里把整个实例设成以下两种单 socket 形态之一：

1. **纯内网**——`security.public.port: null`。唯一的 `server.port` socket 是受信 LAN，
   `security.internal.allow_anonymous` 决定业务 API 是否要 Key。只发布到内网（或 VPN/SSH 隧道）后面。
2. **纯公网**——`security.public.port` **等于** `server.port`（例如都为 `8000`）。唯一的 socket 不受信：
   每个请求都要 Key 或会话，只有 `security.public.path_allowlist` 的 method+path 组合存在，且**没有任何
   匿名 HTTP 路径**（连 `/health` 也没有）。把镜像的 HTTP HEALTHCHECK 换成 TCP 端口检查。

```yaml
server:
  host: 0.0.0.0
  port: 8000
  log_level: info
security:
  internal:
    allow_anonymous: false        # 纯内网：局域网独占实例可设为 true 免 Key
    expose_version: false
    expose_docs: true
  public:
    port: null                    # 纯内网；设为 8000（== server.port）即纯公网
    path_allowlist:              # port 为 null 时忽略；纯公网时必填
      GET:
      - ^/v1/models$
      POST:
      - ^/v1/chat/completions$
      - ^/v1/messages$
      - ^/api/generate$
      - ^/api/chat$
```

如果既要有受信的内网区、又要有策略不同的公网区，请用方案 B。不要试图用 `security.trusted_proxies`
表达这种拆分——它不是分区锚点。

### 方案 B：容器内双监听端口（最可靠，推荐）

`serve.py` 在**同一个进程**里绑定 `server.port` 与 `security.public.port`，因此内存中的 API Key
哈希集是共享的（吊销一把 Key，两个端口立即同时失效），限流桶与管理台登录锁定也同样共享。
**端口来自挂载的 `config.yaml`，不是环境变量**——`data/` 卷里同时放 `config.yaml` 与 `llmhub.db`：

```yaml
# data/config.yaml（挂载到容器内的 /data/config.yaml）
server:
  host: 0.0.0.0
  port: 8000
  log_level: info
security:
  internal:
    allow_anonymous: true
    expose_version: false
    expose_docs: true
  public:
    port: 8001                    # 第二 socket：一个进程内 8000 内网 + 8001 公网
    path_allowlist:              # 公网 socket 的完整 method+path 面
      GET:
      - ^/v1/models$
      POST:
      - ^/v1/chat/completions$
      - ^/v1/messages$
      - ^/api/generate$
      - ^/api/chat$
```

```bash
docker run -d --name llm-hub \
  -e ADMIN_PASSWORD='<32 位以上随机字符>' \
  -p 192.168.10.5:8834:8000 \
  -p 127.0.0.1:8835:8001 \
  -v /path/to/data:/data \
  llm-hub:latest
```

`192.168.10.5:8834 -> 8000` 是**内网**监听，`127.0.0.1:8835 -> 8001` 是只供 nginx 访问的
**公网**监听。把两个容器端口映射到**不同的宿主端口**：两个宿主端口指向同一个容器 socket
**不会**产生两个区。让 `serve.py` 打开两个监听的是 `security.public.port: 8001`。若它等于
`server.port`（如 `8000`），只开一个 socket，整个实例为纯公网（没有匿名 HTTP 路径——用 TCP 探测）。
端口与分区策略没有环境变量入口；上文的「旧版监听变量」一节列出了已移除的变量。

| 路径 | 8000 内网 | 8001 公网 |
|---|---|---|
| 普通 API（`/v1/chat/completions`、`/v1/messages`、`/api/generate` …） | `security.internal.allow_anonymous: true` 时免 Key | 无有效 Key 或会话 **401**；有有效 Key 正常可用 |
| 不在 `security.public.path_allowlist` 中的路径 | 正常路由 | **404**（带不带 Key 都一样） |
| `/v1/management/*` | 只认 Cookie（网关 Key 401） | **404**（代码硬红线） |
| `/docs`、`/openapi.json`、`/static/swagger/*` | `security.internal.expose_docs: true` 时可访问 | **404** |
| `/v1/models`、`/api/tags`、`/api/ps`、`/probe`、`/api/version`、`/` | 可访问 | **404**（除非显式列入 allowlist，且仍需 Key） |
| `/admin`、`/admin/*` | 静态管理台 + 管理接口 | **404**（代码硬红线；无 admin 外壳开关） |
| `/health` | 匿名（探针指向这里） | **404**；即便列入仍需 Key |

公网监听**不是第二个代理进程**：它是同一个进程内的应用，只是在路由前套了一层策略，因此在管理台
吊销的 Key 会同时在这两个端口失效，流式响应也不会被第二跳 HTTP 重新封装。config 无法打开的红线：
`/v1/management/*`、`/admin*`、含 `..` 段或 NUL 字节的路径——在公网一律 404。

nginx 必须**覆盖**转发头，而不是把客户端给的链追加进去：

```nginx
location / {
    proxy_pass http://127.0.0.1:8835;
    proxy_set_header X-Forwarded-For $remote_addr;   # 不要用 $proxy_add_x_forwarded_for
    proxy_set_header X-Forwarded-Proto https;        # 会话 Cookie 才会带 Secure
}
```

### 验收

```bash
# 8000 内网：`/` 与 `/health` 永远匿名；`security.internal.allow_anonymous: true` 下业务 API 免 Key
curl -o /dev/null -w '%{http_code}\n' http://<内网IP>:8834/health                                            # 200
curl -o /dev/null -w '%{http_code}\n' http://<内网IP>:8834/v1/models                                          # 200
# 8001 公网：未列出的路径即使带 Key 也是 404（示例 allowlist 里没有 /api/tags）
curl -o /dev/null -w '%{http_code}\n' https://<公网域名>/api/tags                                            # 404
curl -o /dev/null -w '%{http_code}\n' https://<公网域名>/health                                             # 404
# 8001 公网：列出的路径仍需 Key（无 Key 401、有 Key 200）——上面列了 GET ^/v1/models$
curl -o /dev/null -w '%{http_code}\n' https://<公网域名>/v1/models                                          # 401
curl -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" https://<公网域名>/v1/models          # 200
curl -o /dev/null -w '%{http_code}\n' -X POST https://<公网域名>/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hi"}]}'                                   # 401
# 管理接口 / 文档 / 管理台 / 探针在公网是红线——404，不是 403
curl -o /dev/null -w '%{http_code}\n' https://<公网域名>/v1/management/overview                             # 404
curl -o /dev/null -w '%{http_code}\n' https://<公网域名>/docs                                              # 404
curl -o /dev/null -w '%{http_code}\n' https://<公网域名>/admin                                             # 404
curl -o /dev/null -w '%{http_code}\n' https://<公网域名>/probe                                             # 404
# 伪造 XFF 既不能解锁，也不能改变分区
curl -H 'X-Forwarded-For: 192.168.10.5' -o /dev/null -w '%{http_code}\n' \
  -X POST https://<公网域名>/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hi"}]}'                                   # 401
curl -m 3 -o /dev/null -w '%{http_code}\n' http://<公网域名>:11434/api/tags                               # 连不通
```

若公网端口上未列出的路径返回 200、`/admin` 或 `/v1/management/*` 返回的不是 404、或列出的 API
无 Key 就能用，依次检查：是否旧镜像、挂载的 `config.yaml` 里 `security.public.port` /
`security.public.path_allowlist` 是否缺失或写错、`.env` 里是否残留旧版端口变量、反代是否追加而非
覆盖 XFF。

### 公网部署加固清单（2026-11-09 安全审计后）

代码层修复不能替代部署层配置，公网实例请逐条确认：

1. **在反代终止 TLS**：HTTP 301 跳 HTTPS 并下发 `Strict-Transport-Security`；反代要设置
   `X-Forwarded-Proto https`，登录 Cookie 才会带 `Secure`（纯明文 HTTP 下不会置位）。
2. **轮换凭据**：`ADMIN_PASSWORD` 换成 ≥32 位随机值（照抄 `.env.example` 示例值会被启动自检
   直接拒绝启动），轮换全部 `sk-hub-` 调用方 Key 与 `upstreams[].headers` 里的供应商密钥。
3. **开启限流**：`security.rate_limit.enabled: true`，或由反代限流。登录已有「5 次失败锁定
   15 分钟」保护，其他端点没有独立节流。
4. **管理台只留在内网**：`/admin` 与 `/admin/*` 在公网 socket 上是硬 404（没有 admin 外壳开关）；
   只从内网 / VPN 或隧道访问管理台。所有 `/v1/management/*` 只认已签名的会话 Cookie，调用方 API Key
   一律 401，在公网更是 404。
5. **在 config 里声明分区**：把 `security.public.port` 设为与 `server.port` 不同的端口（如 `8001`），
   两个容器端口映射到**不同的宿主端口**，`server.port` 只发布到内网。监听 socket 才是锚点——
   任何 IP 列表、XFF 或端口环境变量都不能把请求在分区间挪动（旧版端口变量只是迁移提示，请从
   `.env` 删除）。在 `security.trusted_proxies` 填反代地址；只有这些对端发来的 `X-Forwarded-For`
   才参与 IP 围栏、限流分桶与审计，且取最右不可信跳。确认镜像以 `python serve.py` 启动
   （`proxy_headers=False`），不要用裸 `uvicorn main:app`——后者绑不了公网端口，还会让 IP 围栏
   变成客户端可控。`docs` 被隐藏时，公网 API 绝不能同时免 Key。
6. **收敛信息面**：`security.internal.expose_version: false`，不需要时
   `security.internal.expose_docs: false`；`security.public.path_allowlist` 只保留最少量的
   `re.fullmatch` method+path 规则（例如 `^/v1/chat/completions$`，而不是宽泛的 `^/v1/.*$`）。
   未列出的组合带不带 Key 都是 404。`GET /v1/management/config` 已对上游 `headers` 打码，
   保存时会自动从磁盘回填真实值。
7. **容器与依赖（部署层待办，代码未改）**：镜像当前以 root 运行且 `requirements.txt` 只用
   `>=`，建议改为非 root + 只读根文件系统（仅 `/data` 可写），并生成带 hash 的 lock 文件。

---

## 常见问题

### 无法连接到宿主机的 Ollama

```bash
# 确保 Ollama 监听所有接口
OLLAMA_HOST=0.0.0.0:11434 ollama serve

# 启动容器时添加网络配置
docker run -d --name llm-hub \
  --add-host=host.docker.internal:host-gateway \
  ...

# 或直接使用宿主机 IP（config.yaml 中）
"base_url": "http://172.17.0.1:11434"
```

### 健康检查返回 401 或 404

探针要指向**内网**端口（`server.port`，默认 8000）：`/health` 与 `/` 在那里**永远匿名**，
与 `security.internal.allow_anonymous` 无关。在公网 socket（`security.public.port`）上 `/health`
是红线，返回 **404**（除非显式把 `GET ^/health$` 写进 `security.public.path_allowlist`——但即便如此仍需
Key，不适合做探针）。如果实例是纯公网（`security.public.port == server.port`），**没有任何匿名 HTTP
路径**：把镜像的 HTTP HEALTHCHECK 换成对该监听端口的 TCP 连接探测，例如：

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1', 8000)) == 0 else 1)"
```

或另开一个内网监听去探测它。

### 配置修改后不生效

```bash
# 检查文件修改时间
stat data/config.yaml

# 某些编辑器不会更新 mtime，手动触发
touch data/config.yaml

# 或重启容器
docker restart llm-hub
```

### 容器启动失败

```bash
docker logs llm-hub
```

常见原因：完全没有可用凭据（`api_keys` 表为空、`ADMIN_PASSWORD` 未设置且没有匿名内网区——
设置 `ADMIN_PASSWORD`，或对纯内网实例设 `security.internal.allow_anonymous: true`）、
`ADMIN_PASSWORD` 还是 `.env.example` 里的占位值、`config.yaml` 被 Pydantic 拒绝（如
`security.public.path_allowlist` 的 method key 非法），或 YAML 语法错误。`.env` 里的旧版端口变量
只是警告，不是启动失败的原因——端口只来自 `config.yaml`。`start-local.bat -CheckOnly`
可以在不绑定任何端口的情况下跑同一套 preflight。

### 流式响应中断

如果使用 Nginx 反向代理，需关闭缓冲并增加超时：

```nginx
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 600s;
```

---

## 运维

```bash
# 日志
docker logs -f llm-hub
docker logs --tail 100 llm-hub

# 重启
docker restart llm-hub

# 更新镜像
docker pull bruce1977/llm-hub:latest
docker stop llm-hub && docker rm llm-hub
# 重新执行上面的 docker run 命令

# 备份配置
tar -czf llm-hub-backup-$(date +%Y%m%d).tar.gz data/ .env

# 资源限制
docker update --cpus="2.0" --memory=2g llm-hub
```
