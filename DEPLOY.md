# LLM Hub - Docker 部署指南

## 目录

- [快速部署](#快速部署)
- [生产环境部署](#生产环境部署)
- [配置说明](#配置说明)
- [常见问题](#常见问题)
- [运维操作](#运维操作)

---

## 快速部署

### 前置条件

- Docker 20.10+
- Docker Compose v2.0+
- 可用端口 8888 (或自定义)

### 步骤 1: 克隆仓库

```bash
git clone https://github.com/your-org/llm-hub.git
cd llm-hub
```

### 步骤 2: 准备配置文件

```bash
# 复制配置模板
cp data/example.config.json data/config.json
cp .example.env .env
```

### 步骤 3: 编辑配置

**编辑 `data/config.json`:**

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000,
    "log_level": "info"
  },
  "auth": {
    "enabled": true,
    "api_keys": [],
    "allow_anonymous_health": true
  },
  "upstreams": [
    {
      "name": "local-main",
      "type": "ollama",
      "base_url": "http://host.docker.internal:11434",
      "models": ["qwen3:8b", "qwen3.5:4b", "bge-m3:latest"],
      "timeout": 600,
      "description": "本地 Ollama 实例"
    }
  ],
  "default_upstream": "local-main",
  "aliases": {
    "qwen3.5:4b-nothink": {
      "model": "qwen3.5:4b",
      "params": {"think": false},
      "description": "禁用思考模式的 4B 模型"
    }
  },
  "rerank": {
    "enabled": true,
    "mode": "logprobs",
    "models": ["qwen3-reranker:4b"],
    "normalize": true,
    "max_concurrency": 8
  }
}
```

**编辑 `.env`:**

```bash
# 生成随机 API 密钥 (≥16 字符)
GATEWAY_API_KEYS=sk-gateway-$(openssl rand -hex 16)

# 时区设置
TZ=Asia/Shanghai
```

### 步骤 4: 启动服务

```bash
# 构建并启动
docker compose up -d

# 查看日志
docker logs -f llm-hub
```

### 步骤 5: 验证部署

```bash
# 健康检查
curl http://localhost:8888/health

# 获取模型列表
curl http://localhost:8888/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"

# 测试聊天
curl http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

---

## Docker Hub 部署（推荐）

无需克隆仓库，直接从 Docker Hub 拉取镜像部署。

### 步骤 1: 拉取镜像

```bash
docker pull bruce1977/llm-hub:latest
```

### 步骤 2: 准备配置文件

```bash
# 创建配置目录
mkdir -p llm-hub/data

# 创建配置文件
cat > llm-hub/data/config.json << 'EOF'
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000,
    "log_level": "info"
  },
  "auth": {
    "enabled": true,
    "api_keys": [],
    "allow_anonymous_health": true
  },
  "upstreams": [
    {
      "name": "local-main",
      "type": "ollama",
      "base_url": "http://host.docker.internal:11434",
      "models": ["qwen3:8b", "qwen3.5:4b", "bge-m3:latest"],
      "timeout": 600,
      "description": "本地 Ollama 实例"
    }
  ],
  "default_upstream": "local-main",
  "aliases": {
    "qwen3.5:4b-nothink": {
      "model": "qwen3.5:4b",
      "params": {"think": false},
      "description": "禁用思考模式的 4B 模型"
    }
  },
  "rerank": {
    "enabled": true,
    "mode": "logprobs",
    "models": ["qwen3-reranker:4b"],
    "normalize": true,
    "max_concurrency": 8
  }
}
EOF

# 创建环境变量文件
cat > llm-hub/.env << 'EOF'
GATEWAY_API_KEYS=sk-gateway-$(openssl rand -hex 16)
TZ=Asia/Shanghai
EOF
```

### 步骤 3: 启动服务

```bash
# 使用 docker run
docker run -d --name llm-hub \
  -p 8888:8000 \
  -v $(pwd)/llm-hub/data:/data \
  --env-file llm-hub/.env \
  --restart unless-stopped \
  bruce1977/llm-hub:latest

# 或使用 docker compose
cd llm-hub
docker compose up -d
```

### 步骤 4: 验证部署

```bash
curl http://localhost:8888/health
curl http://localhost:8888/v1/models -H "Authorization: Bearer YOUR_API_KEY"
```

### 更新镜像

```bash
# 拉取最新版本
docker pull bruce1977/llm-hub:latest

# 停止并删除旧容器
docker stop llm-hub && docker rm llm-hub

# 重新启动
docker run -d --name llm-hub \
  -p 8888:8000 \
  -v $(pwd)/llm-hub/data:/data \
  --env-file llm-hub/.env \
  --restart unless-stopped \
  bruce1977/llm-hub:latest
```

---

## 生产环境部署

### 使用 systemd 管理 (Linux)

**创建服务文件 `/etc/systemd/system/llm-hub.service`:**

```ini
[Unit]
Description=LLM Hub Gateway
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/llm-hub
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose restart
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
```

**启用服务:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable llm-hub
sudo systemctl start llm-hub
```

### 使用 Watchtower 自动更新

**添加到 `docker-compose.yml`:**

```yaml
services:
  llm-hub:
    # ... 原有配置 ...
    labels:
      - "com.centurylinklabs.watchtower.enable=true"

  watchtower:
    image: containrrr/watchtower
    container_name: watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - WATCHTOWER_CLEANUP=true
      - WATCHTOWER_POLL_INTERVAL=3600
    command: llm-hub
```

### 使用 Nginx 反向代理

**Nginx 配置 `/etc/nginx/conf.d/llm-hub.conf`:**

```nginx
upstream llm_hub {
    server 127.0.0.1:8888;
}

server {
    listen 443 ssl http2;
    server_name llm.example.com;

    ssl_certificate /etc/letsencrypt/live/llm.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/llm.example.com/privkey.pem;

    # 安全头
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;

    # 请求体大小限制 (匹配 LLM Hub 配置)
    client_max_body_size 32m;

    location / {
        proxy_pass http://llm_hub;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 超时设置
        proxy_connect_timeout 10s;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;

        # SSE/流式支持
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding off;
    }
}

server {
    listen 80;
    server_name llm.example.com;
    return 301 https://$host$request_uri;
}
```

---

## 配置说明

### 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `GATEWAY_API_KEYS` | ✅ | API 密钥，逗号分隔 |
| `CONFIG_PATH` | ❌ | 配置文件路径，默认 `/data/config.json` |
| `TZ` | ❌ | 时区，默认 `UTC` |
| `GATEWAY_API_KEYS_FILE` | ❌ | 密钥文件路径 (Docker Secrets) |

### 数据卷挂载

| 容器路径 | 说明 |
|----------|------|
| `/data` | 配置目录 (包含 `config.json`) |
| `/app` | 源代码目录 (可选，用于热更新) |

### 网络配置

```yaml
# docker-compose.yml 部分
ports:
  - "8888:8000"           # 主端口映射
  - "127.0.0.1:8835:8000" # 仅本地访问

extra_hosts:
  - "host.docker.internal:host-gateway"  # 访问宿主机服务
```

### 资源限制

```yaml
services:
  llm-hub:
    # ... 原有配置 ...
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
```

---

## 常见问题

### Q1: 无法连接到宿主机的 Ollama

**症状:** 
```
Cannot connect to upstream local-main (http://host.docker.internal:11434/api/tags): Connection refused
```

**解决方案:**

1. 确保 Ollama 监听所有接口:
   ```bash
   OLLAMA_HOST=0.0.0.0:11434 ollama serve
   ```

2. Linux 需要添加网络配置:
   ```yaml
   extra_hosts:
     - "host.docker.internal:host-gateway"
   ```

3. 或者直接使用宿主机 IP:
   ```json
   {
     "base_url": "http://172.17.0.1:11434"
   }
   ```

### Q2: 健康检查失败

**症状:** 
```
Health check keeps failing with 401
```

**解决方案:**

确保 `auth.allow_anonymous_health` 为 `true`:

```json
{
  "auth": {
    "allow_anonymous_health": true
  }
}
```

### Q3: 配置修改后不生效

**症状:** 修改 `config.json` 后，服务行为未改变。

**解决方案:**

1. 检查文件修改时间:
   ```bash
   stat data/config.json
   ```

2. 手动重启容器:
   ```bash
   docker restart llm-hub
   ```

3. 某些编辑器 (如 VSCode) 可能不会更新 mtime，使用:
   ```bash
   touch data/config.json
   ```

### Q4: 容器启动失败

**查看日志:**

```bash
docker logs llm-hub
```

**常见原因:**

1. API 密钥未配置:
   ```
   Refusing to start: auth.enabled is true but no API key is configured
   ```
   解决: 设置 `GATEWAY_API_KEYS` 环境变量

2. 弱密钥:
   ```
   Refusing to start: weak/placeholder API keys detected
   ```
   解决: 使用 ≥16 字符的随机密钥

3. 配置文件语法错误:
   ```
   Failed to parse config
   ```
   解决: 验证 JSON 语法

### Q5: 流式响应中断

**症状:** SSE/NDJSON 流中途断开。

**解决方案:**

1. 检查 Nginx 配置:
   ```nginx
   proxy_buffering off;
   proxy_cache off;
   ```

2. 增加超时时间:
   ```nginx
   proxy_read_timeout 600s;
   ```

3. 检查上游超时配置:
   ```json
   {
     "upstreams": [{
       "timeout": 600
     }]
   }
   ```

---

## 运维操作

### 日志管理

```bash
# 查看实时日志
docker logs -f llm-hub

# 查看最近 100 行
docker logs --tail 100 llm-hub

# 查看特定时间段
docker logs --since 2026-09-07T10:00:00 llm-hub
```

### 配置热更新

```bash
# 编辑配置
vim data/config.json

# 等待自动重载 (~1秒)
# 或手动重启
docker restart llm-hub
```

### 源代码热更新

```bash
# 编辑 app 目录下的 Python 文件
vim app/main.py

# 重启容器 (无需重新构建)
docker restart llm-hub
```

### 备份与恢复

```bash
# 备份配置
tar -czf llm-hub-backup-$(date +%Y%m%d).tar.gz data/ .env

# 恢复配置
tar -xzf llm-hub-backup-20260907.tar.gz
docker restart llm-hub
```

### 扩展部署 (多实例)

```yaml
# docker-compose.override.yml
services:
  llm-hub:
    deploy:
      replicas: 3

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - llm-hub
```

### 监控

```bash
# 容器资源使用
docker stats llm-hub

# 健康检查脚本
#!/bin/bash
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8888/health)
if [ "$HEALTH" != "200" ]; then
  echo "Health check failed: $HEALTH"
  docker restart llm-hub
fi
```

### 升级

```bash
# 拉取最新代码
git pull

# 重新构建并部署
docker compose up -d --build

# 或使用 Watchtower 自动更新
docker compose up -d
```

---

## 安全加固

### 生产环境清单

- [ ] 使用强 API 密钥 (≥16 字符随机字符串)
- [ ] 启用 HTTPS (通过 Nginx/Caddy)
- [ ] 配置 IP 白名单
- [ ] 禁用 API 文档 (`allow_docs: false`)
- [ ] 保持管理端点阻止 (`admin_endpoints: "deny"`)
- [ ] 设置请求体大小限制
- [ ] 配置日志轮转
- [ ] 定期更新镜像

### 使用 Docker Secrets

```yaml
services:
  llm-hub:
    secrets:
      - api_keys
    environment:
      - GATEWAY_API_KEYS_FILE=/run/secrets/api_keys

secrets:
  api_keys:
    file: ./secrets/api_keys.txt
```

### 网络隔离

```yaml
networks:
  frontend:
    driver: bridge
  backend:
    driver: bridge
    internal: true

services:
  llm-hub:
    networks:
      - frontend
      - backend

  ollama:
    networks:
      - backend
```
