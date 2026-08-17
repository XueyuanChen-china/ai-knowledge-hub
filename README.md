# AI Knowledge Hub

企业知识库与专家 Agent 问答平台。

项目围绕一条完整链路构建：用户上传企业文档，系统解析并切分内容，生成向量索引，通过 Elasticsearch 检索证据，再由 LangGraph 编排 Router、RAG、原生 Tool Calling、人工审核和流式回答。

## 核心能力

- 知识库和知识条目 CRUD
- TXT、Markdown、PDF、DOCX、CSV、XLSX 多格式解析
- 结构感知的 section / block / chunk 切分
- BGE-M3 embedding 和 Elasticsearch dense vector 检索
- PostgreSQL 持久化业务数据
- 阿里云 OSS Multipart Upload 和断点续传协议
- RabbitMQ + Celery 阶段级文档处理任务
- LangGraph Router、RAG、relevance gate 和 human-in-the-loop
- Qwen 原生 Tool Calling、受控只读工具和上下文预算管理
- SSE 流式回答、引用来源和持久化会话记录
- React + Vite + TypeScript + Mantine 管理与问答工作台

## 技术栈

| 层次 | 技术 |
| --- | --- |
| API | FastAPI、Uvicorn、Pydantic Settings |
| 业务数据库 | PostgreSQL、SQLModel、SQLAlchemy |
| 搜索 | Elasticsearch 8、BGE-M3 dense vector |
| 文件存储 | Aliyun OSS Multipart Upload |
| 异步任务 | RabbitMQ、Celery |
| 文档解析 | pypdf、pdfplumber、python-docx、openpyxl |
| 工作流 | LangGraph、Qwen OpenAI-compatible API |
| 前端 | React、Vite、TypeScript、Mantine、React Router |

## 系统主链路

```text
上传文件
  -> OSS Multipart Upload
  -> PostgreSQL upload_tasks / upload_parts
  -> RabbitMQ / Celery stage jobs
  -> download -> validate -> parse -> split -> embed -> index
  -> PostgreSQL documents / chunks
  -> Elasticsearch dense vector
  -> hybrid retrieval / relevance gate
  -> LangGraph answer / native Tool Calling / human review
  -> SSE answer + citations
```

## 目录结构

```text
ai-knowledge-hub/
  backend/
    app/api/                 FastAPI 路由
    app/db/                  SQLModel 模型和数据库连接
    app/graph/               LangGraph 状态与节点
    app/services/            解析、切分、RAG、向量和上传服务
    app/tasks/               Celery 阶段任务
    scripts/                 本地依赖、测试数据和 E2E 脚本
    tests/                   后端单元、集成和回归测试
  frontend/
    app/                     页面模块
    components/              Mantine 组件
    lib/api/                 类型化 API client
    src/router.tsx           React Router
  docs/
    api.md                   API 参考
    day-*.md                 按阶段学习文档
    improvements/            后续改造路线和阶段学习文档
    large-file-upload/       大文件上传专项设计
    message-queue/            MQ 与 Celery 学习资料
```

## 本地启动

### 推荐方式：统一 Docker Compose

前置条件：安装并启动 Docker Desktop。首次启动至少需要拉取基础镜像并构建项目镜像，建议给 Docker 分配约 4 GB 可用内存。

在仓库根目录执行：

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，填写本地 JWT secret，以及需要使用的 OSS / Qwen 配置
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/health/live
```

Compose 会启动 PostgreSQL、Elasticsearch、RabbitMQ、Redis、FastAPI、普通 Celery Worker、embedding Worker 和 React 静态前端。backend 容器会执行 Alembic migration 和 checkpoint 初始化，Worker 只检查数据库版本后消费任务。

访问地址：

- 前端：<http://localhost:3000>
- Swagger：<http://127.0.0.1:8000/docs>
- RabbitMQ 管理台：<http://localhost:15672>

`backend/.env` 只保存在本机，已被 Git 忽略。未配置 OSS 或 Qwen 密钥时，基础服务、登录、CRUD、迁移和大部分自动化测试仍可运行，但真实文件上传和模型问答不能完成。完整配置和排障方式见 [本地容器栈说明](docs/operations/local-stack.md)。

停止服务但保留数据：

```bash
docker compose down
```

删除本地数据库、搜索索引、消息和 Redis 数据卷：

```bash
docker compose down -v
```

### 手动开发方式

只有在需要单独调试某个进程时，才使用 `uvicorn`、Celery 和 Vite 的手动启动方式。手动方式要求你自行准备 PostgreSQL、Elasticsearch、RabbitMQ、Redis 和对应环境变量，不能替代上面的完整 Compose 环境。

## 主要接口

| 场景 | 接口 |
| --- | --- |
| 健康检查 | `GET /health` |
| 知识库 CRUD | `/knowledge-bases` |
| 知识条目 CRUD | `/knowledge-items` |
| 文档上传与索引 | `/documents`、`POST /documents/{id}/index` |
| 语义搜索 | `POST /search/semantic` |
| 大文件上传 | `/uploads/*` |
| SSE 问答 | `POST /api/chat/stream` |
| 审核恢复 | `POST /api/review/resume/stream` |
| 会话历史 | `/api/conversations` |

完整请求和响应示例见 [docs/api.md](docs/api.md)。

## 测试

后端回归测试覆盖 Markdown、TXT、CSV、DOCX、XLSX 和 PDF 的 elements、sections、blocks、chunks 快照及质量指标：

```bash
cd backend
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

前端质量检查：

```bash
cd frontend
npm test -- --run
npm run lint
npm run build
```

当前自动化验证基线（2026-08-11）：

```text
后端 unittest：243 tests passed（CI 环境跳过 12 个外部服务测试）
前端 Vitest：9 passed
前端 lint：passed
前端 production build：passed
```

`skipped` 主要对应需要真实 OSS、Qwen 或其他外部服务的受控测试；真实环境验收按 [最终交付清单](docs/operations/final-delivery-checklist.md) 执行并记录。

大文件上传端到端脚本：

```bash
cd backend
./.venv/bin/python scripts/test_large_upload_e2e.py \
  --knowledge-base-id <knowledge_base_id> \
  tests/fixtures/splitter_regression/samples/plain_text_policy.txt
```

## 测试数据

多格式索引样例在 [backend/data/sample_index_files](backend/data/sample_index_files)；切分回归样例在 [backend/tests/fixtures/splitter_regression](backend/tests/fixtures/splitter_regression)。

推荐验证问题：

- `采购复核的触发条件是什么？`
- `供应商准入流程怎么走？`
- `差旅报销单常见退回原因有哪些？`
- `客户成功团队本季度的主要问题是什么？`

## 本地验收与演示

完整验收入口是 [Demo 与验收](docs/operations/demo-and-acceptance.md)，收尾检查见 [最终交付清单](docs/operations/final-delivery-checklist.md)。先运行显式 demo seed，再使用真实 OSS 多格式脚本；不要把 OSS/Qwen 密钥写进脚本或提交到 Git。

```bash
cd backend
./.venv/bin/python scripts/seed_demo_environment.py --password 'U10-Demo-Only-Change-Me!'
```

普通测试默认不访问外部服务；需要真实验证时按 [Demo 与验收](docs/operations/demo-and-acceptance.md) 设置 token、知识库 ID 和外部服务配置。架构、备份恢复和证据记录分别见 [系统架构](docs/architecture/system-overview.md)、[备份与恢复](docs/operations/backup-and-recovery.md) 和 [企业化验收证据](docs/operations/enterprise-readiness-evidence.md)。

## 当前工程边界

当前版本适合作为实习项目和企业 RAG 工程演示，重点展示数据处理、检索、权限、任务编排、人工审核、可恢复工作流和受控 Agent 工具调用。Alembic、身份权限、持久化 checkpoint、容器化 CI、可观测性和 hybrid retrieval 已落地；后续路线图只记录超出本项目范围的增强项和已知边界。

当前明确不包含 Kubernetes 集群、多地域容灾、完整 SaaS 计费、企业 SSO 和 RabbitMQ/Elasticsearch 高可用集群。

## 文档入口

- [API 文档](docs/api.md)
- [系统架构总览](docs/architecture/system-overview.md)
- [本地容器栈](docs/operations/local-stack.md)
- [Demo 与验收](docs/operations/demo-and-acceptance.md)
- [最终交付清单](docs/operations/final-delivery-checklist.md)
- [企业化验收证据](docs/operations/enterprise-readiness-evidence.md)
- [文档索引](docs/README.md)
- [后续改造目录](docs/improvements/README.md)
- [U1：测试基线学习文档](docs/improvements/enterprise-readiness/u1-testing-baseline.md)
- [大文件上传专项](docs/large-file-upload/README.md)
- [MQ 与 Celery 学习资料](docs/message-queue/README.md)
- [最终 E2E 与证据学习文档](docs/improvements/enterprise-readiness/u10-final-e2e-and-evidence.md)
