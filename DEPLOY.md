# LLM Hub - Docker 部署指南

## 快速部署

### 前置条件

- Docker 20.10+

### 步骤 1: 准备配置

```bash
cp data/example.config.json data/config.json
cp .example.env .env
```

编辑 `.env` 设置 API 密钥：

```bash
GATEWAY_API_KEYS=sk-gateway-$(openssl rand -hex 16)
TZ=Asia/Shanghai
```

编辑 `data/config.json` 添加上游实例，参考 `data/example.config.json`。

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

---

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `GATEWAY_API_KEYS` | ✅ | API 密钥，逗号分隔 |
| `CONFIG_PATH` | ❌ | 配置文件路径，默认 `/data/config.json` |
| `TZ` | ❌ | 时区，默认 `UTC` |

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

# 或直接使用宿主机 IP（config.json 中）
"base_url": "http://172.17.0.1:11434"
```

### 健康检查返回 401

确保 `config.json` 中 `auth.allow_anonymous_health` 为 `true`。

### 配置修改后不生效

```bash
# 检查文件修改时间
stat data/config.json

# 某些编辑器不会更新 mtime，手动触发
touch data/config.json

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
