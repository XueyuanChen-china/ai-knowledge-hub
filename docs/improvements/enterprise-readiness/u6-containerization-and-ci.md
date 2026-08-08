# U6：统一容器化环境与 CI

## 本阶段解决什么问题

在 U6 前，项目可以在作者电脑运行，但启动依赖散落在多个脚本里：PostgreSQL、RabbitMQ、Redis、Elasticsearch、API、Celery Worker 和前端需要分别启动。新同事很难确认自己是否漏掉服务、是否用了正确端口，CI 也无法复现同一套环境。

U6 将本地环境固化为：

```text
compose.yml
  -> PostgreSQL
  -> Elasticsearch
  -> RabbitMQ
  -> Redis
  -> FastAPI
  -> Celery Worker
  -> React 静态前端
```

阿里云 OSS 与千问仍是外部服务。它们不能被 Compose “伪装成本地生产服务”，因此本地与 CI 均通过环境变量或 fake/mock 控制，不把真实密钥写进镜像。

## Dockerfile、镜像和容器

```text
Dockerfile
  描述“怎样制作运行包”

image
  Dockerfile 构建出的不可变模板

container
  根据 image 实际启动的一次运行实例
```

本项目有两个镜像：

- `backend/Dockerfile`：安装 Python 依赖，复制 `app/`、Alembic 和启动脚本；同一镜像分别运行 API 与 Celery Worker。
- `frontend/Dockerfile`：第一阶段用 Node 执行 Vite build，第二阶段只用 Nginx 提供构建产物。这叫 **multi-stage build**，最终前端镜像不包含 `node_modules` 与构建工具。

## .dockerignore 为什么重要

Docker 构建时会把整个 build context 发给 Docker daemon。若不排除无关文件，可能出现：

- `backend/.env` 和密钥进入镜像层；
- 原始上传文件、测试数据库进入镜像；
- `node_modules`、`.venv` 导致镜像很大且构建缓存失效。

[.dockerignore](../../../.dockerignore) 把这些文件排除。注意它不会删除本地文件，只决定“哪些文件不参与镜像构建”。

## API 与 Worker 的职责为什么不同

启动顺序：

```text
依赖服务 healthy
  -> backend/scripts/start_api.sh
  -> wait_for_dependencies.py
  -> alembic upgrade head
  -> setup_langgraph_checkpoints.py
  -> uvicorn
  -> worker/scripts/start_worker.sh
  -> wait_for_dependencies.py
  -> check_database_ready()
  -> celery worker
```

API 启动脚本执行迁移，Worker 不执行。原因是 Worker 未来可能扩容为多个副本：如果每个副本启动时都运行 `alembic upgrade head`，就会出现并发 DDL、数据库锁等待和不可预测的发布顺序。

Worker 只调用 `check_database_ready()`：它检查数据库 revision 是否已经是当前代码期望版本。若 migration 失败，Worker 直接退出而不是消费一半旧 schema、一半新 schema 的任务。

## wait_for_dependencies.py 在做什么

它检测的是容器内网络可达性：

- PostgreSQL、RabbitMQ、Redis：尝试 TCP 连接到配置 URL 中的 host/port；
- Elasticsearch：请求 `/_cluster/health` 并等待至少 `yellow`。

“容器进程已启动”不等于“服务已经能用”。例如 Elasticsearch JVM 进程已存在，但索引尚未能接受请求；此时 API 立即启动会在第一次索引时失败。

脚本不等待 OSS 和千问，因为它们是外部网络依赖，启动时不应因没有真实业务密钥而阻断本地 CRUD、迁移或测试。

## Compose healthcheck 和 depends_on

`depends_on` 只表达启动依赖，配合：

```yaml
condition: service_healthy
```

才能让 backend 等待 PostgreSQL、ES、RabbitMQ、Redis 真正通过各自 healthcheck。backend 的 `/health` 通过后，Worker 和 frontend 才启动。

这不替代应用内的 `wait_for_dependencies.py`：Compose 健康检查负责本地栈编排，脚本则保证 API/Worker 镜像独立运行时也有明确等待逻辑。

## CI 四个 Job

| Job             | 验证内容                                                                     |
| --------------- | ---------------------------------------------------------------------------- |
| `backend`     | Python 完整回归测试，使用临时 PostgreSQL、Redis、RabbitMQ                    |
| `migration`   | 从空 PostgreSQL 执行 Alembic 与 LangGraph checkpoint setup                   |
| `frontend`    | `npm run lint`、`npm run build`                                          |
| `integration` | `docker compose config`，并启动 PostgreSQL、ES、RabbitMQ、Redis 等基础容器 |

它们拆开运行的好处是定位快：前端类型错误不需要等待后端回归；migration 问题能明确区分为 schema 问题，而不是业务测试问题。

当前前端还没有独立 unit/E2E 测试命令，因此 U6 的前端门禁是 lint + production build。前端自动化测试属于后续 U8，不会用一个“空 test 命令”伪造覆盖率。

## 推荐阅读顺序

1. [compose.yml](../../../compose.yml)：服务、网络地址、依赖和 volume 如何统一。
2. [backend/Dockerfile](../../../backend/Dockerfile)：后端镜像的运行时边界。
3. [frontend/Dockerfile](../../../frontend/Dockerfile) 与 [nginx.conf](../../../frontend/nginx.conf)：前端多阶段构建与 SPA 回退。
4. [wait_for_dependencies.py](../../../backend/scripts/wait_for_dependencies.py)：服务就绪检查。
5. [start_api.sh](../../../backend/scripts/start_api.sh) 与 [start_worker.sh](../../../backend/scripts/start_worker.sh)：迁移责任如何分离。
6. [ci.yml](../../../.github/workflows/ci.yml)：GitHub Actions 质量门禁。
7. [local-stack.md](../../operations/local-stack.md)：实际启动、停止与排障命令。

## 当前边界

- Compose 是开发和 CI 的生产近似环境，不是 Kubernetes 集群或高可用部署方案。
- API 当前 Compose 默认只有一个副本；进入多副本部署时，应将 migration 进一步抽为发布前的一次性 Job。
- CI 不调用真实 OSS、Qwen，也不下载 BGE-M3 模型执行真实 embedding。
- `/health` 当前是进程存活检查；更细的 liveness/readiness 与指标链路属于后续可观测性阶段。
