---
name: docker-publish
description: One-click Docker build, push to Docker Hub with latest+version tags, auto-commit changes with generated message. Use when user says "publish", "deploy", "release", "docker push", or "打包发布".
---

# Docker Publish Skill

一键完成 Docker 构建、发布到 Docker Hub、Git 提交的全流程。

## 执行流程

按以下步骤依次执行，任何一步失败立即停止并报告错误：

### Step 1: 检查前置条件

```bash
# 确认 Docker 可用
docker info > /dev/null 2>&1 || { echo "ERROR: Docker 未运行"; exit 1; }

# 确认已登录 Docker Hub
docker login --username bruce1977 > /dev/null 2>&1 || { echo "ERROR: 未登录 Docker Hub，请先执行 docker login"; exit 1; }

# 确认 Git 仓库状态
git status --porcelain
```

### Step 2: 自动提交未跟踪的修改

检查 `git status --porcelain` 输出：

1. **如果有未提交的修改**：
   ```bash
   git add -A
   # 自动生成提交信息：列出变更的文件名
   CHANGED_FILES=$(git diff --cached --name-only | head -10 | tr '\n' ', ')
   COMMIT_MSG="chore: update files before publish ($(date +%Y-%m-%d))"
   if [ -n "$CHANGED_FILES" ]; then
     COMMIT_MSG="chore: update before publish - ${CHANGED_FILES} ($(date +%Y-%m-%d))"
   fi
   git commit -m "$COMMIT_MSG"
   ```
2. **如果没有修改**：跳过提交步骤

### Step 3: 读取版本号

从 `pyproject.toml` 提取版本号：

```bash
VERSION=$(grep '^version' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')
IMAGE_NAME="bruce1977/llm-hub"
echo "Version: $VERSION, Image: $IMAGE_NAME"
```

### Step 4: 构建 Docker 镜像

```bash
docker build -t ${IMAGE_NAME}:latest -t ${IMAGE_NAME}:${VERSION} .
```

### Step 5: 推送到 Docker Hub

```bash
docker push ${IMAGE_NAME}:latest
docker push ${IMAGE_NAME}:${VERSION}
```

### Step 6: 推送 Git 提交

```bash
git push origin main
```

### Step 7: 输出结果摘要

打印以下信息：
- 镜像名称和版本号
- Docker Hub 链接：`https://hub.docker.com/r/${IMAGE_NAME}`
- Git 提交哈希（短格式）
- 各步骤执行状态

## 注意事项

- 如果用户指定了版本号参数（如 `$ARGUMENTS` 为 `1.2.0`），则使用该版本号覆盖 pyproject.toml 中的值
- 构建失败时提供 `--no-cache` 选项让用户重试
- 如果 Docker 未登录，提示用户先执行 `docker login`
- 所有 git 操作使用 `--porcelain` 格式便于解析
