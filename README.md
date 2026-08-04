# AI Knowledge Hub

企业知识库与专家 Agent 问答平台。

项目围绕一条完整链路构建：用户上传企业文档，系统解析并切分内容，生成向量索引，通过 Elasticsearch 检索证据，再由 LangGraph 编排 Router、RAG、人工审核和流式回答。

## 核心能力

- 知识库和知识条目 CRUD
- TXT、Markdown、PDF、DOCX、CSV、XLSX 多格式解析
- 结构感知的 section / block / chunk 切分
- BGE-M3 embedding 和 Elasticsearch dense vector 检索
- PostgreSQL 持久化业务数据
- 阿里云 OSS Multipart Upload 和断点续传协议
- RabbitMQ + Celery 阶段级文档处理任务
- LangGraph Router、RAG、relevance gate 和 human-in-the-loop
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
  -> LangGraph answer 或 human review
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

### 1. 准备后端环境

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

在 `backend/.env` 中补充本地 PostgreSQL、Elasticsearch、RabbitMQ、OSS 和 LLM 配置。密钥只放在本地环境变量中，不提交到 Git。

### 2. 启动本地依赖

推荐使用统一容器栈：

```bash
docker compose up -d
docker compose ps
```

它会统一启动 PostgreSQL、Elasticsearch、RabbitMQ、Redis、API、Celery Worker 和前端。完整配置、外部 OSS/LLM 密钥与排障方式见 [本地容器栈说明](docs/operations/local-stack.md)。

如果需要沿用拆分启动方式：

```bash
bash scripts/start_postgres_local.sh
bash scripts/start_rabbitmq_local.sh
```

Elasticsearch 需要单节点开发实例，启动后确认：

```bash
curl http://localhost:9200
```

### 3. 启动 API

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

Swagger：<http://127.0.0.1:8000/docs>

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

### 4. 启动 Celery Worker

另开终端：

```bash
cd backend
source .venv/bin/activate
bash scripts/start_celery_worker.sh
```

### 5. 启动前端

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

前端地址：<http://localhost:3000>

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
npm run lint
npm run build
```

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

## U10 企业化验收

完整验收入口是 [Demo 与 U10 验收](docs/operations/demo-and-acceptance.md)。先运行显式 demo seed，再使用真实 OSS 多格式脚本；不要把 OSS/Qwen 密钥写进脚本或提交到 Git。

```bash
cd backend
./.venv/bin/python scripts/seed_demo_environment.py --password 'U10-Demo-Only-Change-Me!'
```

U10 的测试门禁默认不访问外部服务；需要真实验证时显式设置 `RUN_ENTERPRISE_E2E=1`、`E2E_ACCESS_TOKEN` 和 `E2E_KNOWLEDGE_BASE_ID`。架构、备份恢复和证据记录分别见 [系统架构](docs/architecture/system-overview.md)、[备份与恢复](docs/operations/backup-and-recovery.md) 和 [企业化验收证据](docs/operations/enterprise-readiness-evidence.md)。

## 当前工程边界

当前版本适合作为实习项目和企业 RAG 工程演示，重点展示数据处理、检索、权限、任务编排、人工审核和可恢复工作流。生产化路线，包括 Alembic、身份权限、持久化 checkpoint、容器化 CI、可观测性和 hybrid retrieval，记录在 [企业化改造路线图](docs/improvements/internship-enterprise-readiness-roadmap.md)。

当前明确不包含 Kubernetes 集群、多地域容灾、完整 SaaS 计费、企业 SSO 和 RabbitMQ/Elasticsearch 高可用集群。

## 文档入口

- [API 文档](docs/api.md)
- [文档索引](docs/README.md)
- [后续改造目录](docs/improvements/README.md)
- [U1：测试基线学习文档](docs/improvements/enterprise-readiness/u1-testing-baseline.md)
- [大文件上传专项](docs/large-file-upload/README.md)
- [MQ 与 Celery 学习资料](docs/message-queue/README.md)
- [U10 最终 E2E 与证据学习文档](docs/improvements/enterprise-readiness/u10-final-e2e-and-evidence.md)
