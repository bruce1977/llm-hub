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

编辑 `.env` 设置 API 密钥：

```bash
GATEWAY_API_KEYS=sk-gateway-$(openssl rand -hex 16)
TZ=Asia/Shanghai
```

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
cp .env.example .env                                # 填 GATEWAY_API_KEYS、ADMIN_PASSWORD

cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env
```

工作目录必须是 `app/`（与镜像的 `WORKDIR /app` 一致），否则 `.env` 里的 `../data/config.yaml`、
`../data/.keys` 解析不到；`--env-file` 指向仓库根的 `.env`。管理台在 `http://localhost:8000/admin`，
前提是 `app/web` 已构建过——见下一节。

### 启动脚本（Windows）

想在宿主机上直接跑网关（调试上游、验证配置），也可以用同一目录下的 `start-local.bat` / `start-local.ps1`：

```bat
:: 检查通过后启动（默认带 --reload）
scripts\start-local.bat

:: 只做启动前检查，不启动服务
scripts\start-local.bat -CheckOnly

:: 先装依赖，再换端口启动
scripts\start-local.bat -Install -Port 8888
```

它做三件事：定位 python 与 `data\config.yaml`、按 `.env` 注入环境变量（shell 里已设置的变量优先），然后
调用应用**自己的**模块跑一遍检查——缺依赖、Pydantic 拒绝配置、开了鉴权却没有 API Key、弱/占位 Key、端口被占用、
`app\web` 未构建——全部通过才用 `--app-dir app` 在 `app/` 目录下启动 uvicorn，与镜像的 `WORKDIR /app` 一致，
所以 `.env` 里的 `../data/config.yaml`、`../data/.keys` 在本地和容器里解析到同一份文件。

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

| 变量 | 必需 | 说明 |
|------|------|------|
| `GATEWAY_API_KEYS` | ✅* | API 密钥，逗号分隔（*与下一行的密钥文件二选一；`auth.allow_access_ip_whitelist` 非空时可为空）。**只在 `api_keys` 表为空的首次启动导入一次**，之后调用方 Key 以 `llmhub.db` 为准，在 `/admin` 的 API Key 管理页签发与吊销；改这个变量不再有效果 |
| `GATEWAY_API_KEYS_FILE` | ❌ | 密钥文件路径。不设置时自动读取 `CONFIG_PATH` 同目录的 `.keys`（如挂载的 `/config/.keys`、默认布局的 `/data/.keys`）；显式设置则以其为准。同样只用于首次导入，且表里已有的 Key 不会被重复写入 |
| `CONFIG_PATH` | ❌ | 配置文件路径，默认 `/data/config.yaml` |
| `TZ` | ❌ | 时区，默认 `UTC` |
| `SYSTEMONE_SERVER_API_KEY` | ❌ | System One 后端密钥，仅当 `systemone.api_key_env` 指向它时才需要；laya-server 必需，Ollama 0.40+ 后端会忽略该头 |
| `OPENCODE_GATEWAY_API_KEY` | ❌ | 联网检索上游密钥，仅当 `websearch.api_key_env` 指向它时才需要（默认留空 = 不签名） |
| `ADMIN_PASSWORD` | ❌ | Web 管理台登录密码（用户名固定 `admin`），与调用方 API Key 是两套凭据：它只用于 `POST /v1/management/auth/login` 换取 12 小时会话 Cookie，本身当作 `Bearer` / `X-API-Key` 提交仍是 401。`/v1/management/*` 除该 Cookie 外也接受 `api_keys` 表里的网关 Key（供脚本复用），但 `/api-keys` 三个接口例外——只认会话 Cookie，否则任何客户端都能用它自己的 Key 再造一把。未设置时 `/admin` 显示「管理台未启用」，`/v1/management/*` 全部返回 503 |
| `REQUEST_LOG_DB` | ❌ | 请求明细 SQLite 路径，默认 `CONFIG_PATH` 同目录的 `llmhub.db`（容器内为 `/data/llmhub.db`）。同一个库文件里共有三张表：`requests`（明细）、`bench_results`（测速历史）与 `api_keys`（调用方 Key 的 hash 与掩码） |
| `LLMHUB_DB` | ❌ | `bench_results` 与 `api_keys` 两张表的库路径，默认同样是 `CONFIG_PATH` 同目录的 `llmhub.db`。只在需要把它们与明细库分开时才设置；三张表默认落在同一个文件，容器只需一个可写 `/data` |
| `REQUEST_LOG_RETENTION_DAYS` | ❌ | 请求明细保留天数，默认 `30`；`0` 表示永久保留 |

> 管理台会把请求明细写入 SQLite（WAL 模式），需要 `/data` 目录可写。若使用只读挂载，
> 请把 `REQUEST_LOG_DB` 指到可写路径，或设为空目录下的默认位置。
> 是否入库由 `config.yaml` 的 `request_log.enabled` 控制（默认 `true`）：该开关逐请求读取，
> 改成 `false` 保存后无需重启容器；配置的 mtime 每秒最多比对一次，因此约 1 秒内停止写入。

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

### 健康检查返回 401

确保 `config.yaml` 中 `auth.allow_anonymous_health` 为 `true`。

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

常见原因：API 密钥未配置、弱密钥（需 ≥16 字符）、JSON 语法错误。

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
