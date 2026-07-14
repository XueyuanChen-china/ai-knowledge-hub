# AI Knowledge Hub

企业知识库管理与专家 Agent 平台。

当前阶段先完成后端基础能力：

```text
知识库 CRUD
知识条目 CRUD
本地文档上传
PDF 文本提取
数据库持久化
文档混合切分
文档索引与语义搜索
RAG Service 最小闭环
前端工作台骨架
```

## 技术栈

```text
后端：FastAPI
数据库：PostgreSQL
ORM：SQLModel / SQLAlchemy
配置：pydantic-settings / .env
文件上传：python-multipart
PDF 解析：pypdf
PDF layout：pdfplumber
Word 解析：python-docx
Excel 解析：openpyxl
向量检索：Elasticsearch dense_vector
Embedding：BAAI/bge-m3
工作流：LangGraph
前端：React + Vite + TypeScript + Mantine
```

## 已完成功能

### Day 1：后端基础

- FastAPI 应用入口
- `.env` 配置读取
- 数据库配置
- SQLModel 初始化
- 健康检查接口 `GET /health`

### Day 2：数据库模型

- `knowledge_bases`
- `documents`
- `knowledge_items`
- `knowledge_item_reviews`
- `chunks`
- `conversations`
- `messages`
- `review_tasks`

### Day 3：知识库 CRUD API

- `POST /knowledge-bases`
- `GET /knowledge-bases`
- `GET /knowledge-bases/{id}`
- `PUT /knowledge-bases/{id}`
- `DELETE /knowledge-bases/{id}`

### Day 4：知识条目 CRUD API

- `POST /knowledge-items`
- `GET /knowledge-items`
- `GET /knowledge-items/{id}`
- `PUT /knowledge-items/{id}`
- `DELETE /knowledge-items/{id}`
- 支持按 `knowledge_base_id` 和 `status` 查询

### Day 5：本地文件上传

- `POST /documents`
- 支持上传 `.txt` / `.md`
- 文件保存到 `backend/data/uploads`
- 上传记录写入 `documents` 表

### Day 6：PDF 支持

- 接入 `pypdf`
- 支持上传 `.pdf`
- 提取文本保存到 `documents.extracted_text`

### Day 8：TextSplitter

- 实现混合切分策略
- `chunk_size=1000`
- `chunk_overlap=200`
- `POST /documents/{document_id}/chunks`
- 自动创建文档来源的 `KnowledgeItem`
- 写入 `chunks` 表

### Day 9：Embedding + Elasticsearch

- `POST /documents/{document_id}/index`
- 使用 `BAAI/bge-m3` 生成 dense embedding
- 写入 Elasticsearch `dense_vector`
- 回填 `chunks.vector_id`
- 更新 `documents.status = indexed`

### Day 11：语义搜索 API

- `POST /search/semantic`
- 使用 BGE-M3 对用户问题生成查询向量
- 基于 Elasticsearch `knn` 检索相关 chunk
- 返回标题、预览内容、分数和 metadata

### Day 13：RAG Service

- 实现 `rag_service.retrieve()`
- 实现 `rag_service.format_context()`
- 实现 `rag_service.generate_answer()`
- 打通 `question -> docs -> context -> answer` 内部链路

### Day 14：本周整理

- 补充 RAG 流程文档
- 补充 Elasticsearch / 测试数据说明
- 准备多格式测试样例
- 收口第二周最小知识检索闭环

### Day 15：GraphState + 基础图

- 新增 `graph/state.py`
- 新增 `graph/nodes.py`
- 新增 `graph/workflow.py`
- 实现 `START -> router -> direct/rag`
- direct 问题不走知识库检索
- rag 问题进入 retrieve

### Day 16：LLM Router

- `router_node` 先调用 LLM Router
- 支持输出 `direct / rag / complex`
- 增加 `normalize_route`
- LLM 失败时自动走规则兜底

### Day 17：Retrieve Node

- `retrieve_node` 接 Elasticsearch 检索
- 写回 `retrieved_docs / context / docs_preview / citations`
- 增加 `retrieval_hit_count`
- `rag` 问题可直接打印检索结果预览

### Day 18：Answer Node

- `answer_node` 基于 `context` 调用千问生成答案
- 返回 `answer + citations`
- 引用通过 `used_context_numbers -> doc/chunk` 映射生成
- LLM 失败时回退到本地抽取式答案

### Day 19：Relevance Check Node

- 新增 `relevance_check_node`
- 判断 `docs` 是否为空
- 判断 `top score` 是否低于阈值
- 结果不足时不直接进 `answer_node`

### Day 20：Checkpoint + Interrupt

- 使用 `InMemorySaver`
- `thread_id` 接到 LangGraph checkpoint
- 新增 `human_review_node` + `interrupt`
- 新增 `POST /api/chat` 和 `POST /api/review/resume`
- 支持 `Command(resume)` 恢复执行

### Day 21：Chat API 接入 Graph

- `POST /api/chat` 调用 graph
- 正常结束返回 `answer`
- interrupt 时返回 `review_payload`
- `POST /api/review/resume` 恢复图执行

### Day 22：PostgreSQL 标准化

- 数据库运行时统一切到 PostgreSQL
- 新增本地 PostgreSQL Docker 启动文件
- 完成历史 SQLite 数据迁移
- 后端测试统一切到 PostgreSQL

### Day 23：前端初始化

- 新增 `frontend`
- 使用 `React + Vite + TypeScript + Mantine`
- 配置路由和统一工作台布局
- 配置 API client 并接入首页、知识库页、对话工作台

### Day 24：知识库列表页

- 知识库列表页增加创建弹窗
- 支持删除知识库
- 支持进入详情页
- 详情页支持编辑知识库

### Day 25：文档上传和索引页

- 新增前端文档上传与索引页 `/documents`
- 支持按知识库筛选文档列表
- 支持上传 `.txt / .md / .pdf / .docx / .xlsx`
- 支持手动触发 `POST /documents/{id}/index`
- 前端展示 `uploaded / indexed / failed` 状态
- 后端补充 `GET /documents` 列表接口

### Day 25-X：知识条目管理与 Chunk 可视化

- 知识库详情页增加知识条目列表和手动创建入口
- 支持知识条目编辑 / 删除
- 新增知识条目详情页
- 前端展示知识条目对应的 chunks 和 metadata
- 验证 `GET /knowledge-items/{id}/chunks` 链路

### Day 25-Y：手动知识条目切分与索引

- 新增 `POST /knowledge-items/{id}/chunks`
- 新增 `POST /knowledge-items/{id}/index`
- 手动知识条目可生成 chunks
- 手动知识条目可写入 Elasticsearch 向量索引
- 知识条目详情页增加“生成 Chunks / 构建索引”按钮

### Day 26：语义搜索页面

- 新增前端语义搜索页 `/search`
- 支持选择知识库、输入 query、设置 `top_k`
- 展示 `doc_id / chunk_id / title / content_preview / score`
- 补充高频 metadata 展示，方便判断结果来源

### Day 27：专家 Agent 问答页

- `/chat` 升级为专家问答页
- 支持聊天窗口展示用户消息和 Agent 回答
- 展示引用来源和会话状态
- interrupted 时展示审核面板
- 支持前端调用 `/api/review/resume` 完成通过 / 拒绝

### Day 28：前端迁移到 Vite

- 前端从 `Next.js` 迁移到 `React + Vite`
- 保留现有页面、API client 和 Mantine UI 能力
- 使用 `React Router` 管理路由
- 增加 `next/link` / `next/navigation` 兼容层，降低迁移改动面
- 统一环境变量为 `VITE_API_BASE_URL`

### Day 29：会话持久化 Phase 1

- 新增 `GET /api/conversations`
- 新增 `GET /api/conversations/{id}/messages`
- 专家问答页增加左侧会话列表
- 支持点击历史会话回显消息
- 支持在历史会话上继续追问

## 当前主链路

现在项目已经具备这样一条后端主链路：

```text
上传文档
  -> 提取文本
  -> 切分 chunks
  -> 写入数据库
  -> 生成 embedding
  -> 写入 Elasticsearch
  -> 语义搜索召回
  -> Router
  -> Retrieve Node
  -> Relevance Check Node
  -> Interrupt / Resume
  -> Answer Node / Review
  -> 返回 answer + citations 或 review result
```

## 项目结构

```text
ai-knowledge-hub/
  backend/
    app/
      main.py
      config.py
      api/
        chat.py
        document.py
        knowledge_base.py
        knowledge_item.py
      graph/
        state.py
        nodes.py
        workflow.py
      db/
        database.py
        models.py
      schemas/
        chat.py
        chunk.py
        document.py
        knowledge_base.py
        knowledge_item.py
      services/
        text_splitter.py
    data/
      uploads/
    docker-compose.postgres.yml
    .env.example
    requirements.txt
  frontend/
    app/
      documents/
    components/
    lib/
    .env.example
    package.json
  docs/
    api.md
    day-01-backend-foundation.md
    day-02-database-models.md
    day-03-knowledge-base-crud.md
    day-04-knowledge-item-crud.md
    day-05-local-file-upload.md
    day-06-pdf-support.md
    day-08-text-splitter.md
    day-09-embedding-elasticsearch-index.md
    day-11-semantic-search.md
    day-13-rag-service.md
    day-14-rag-workflow.md
    day-15-graph-workflow.md
    day-16-llm-router.md
    day-17-retrieve-node.md
    day-18-answer-node.md
    day-19-relevance-check-node.md
    day-20-checkpoint-interrupt.md
    day-21-chat-api-graph.md
    day-22-postgresql-migration.md
    day-23-frontend-initialization.md
    day-24-knowledge-base-page.md
    day-25-document-upload-index-page.md
    day-25x-knowledge-item-management.md
    day-25y-manual-knowledge-item-indexing.md
    improvements/
      README.md
      router-upgrade-roadmap.md
      retrieval-quality-improvements.md
  README.md
```

## 本地启动

进入后端目录：

```bash
cd backend
```

创建虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

安装依赖：

```bash
pip install -r requirements.txt
```

创建本地环境变量文件：

```bash
cp .env.example .env
```

启动本地 Elasticsearch：

```bash
docker run -d \
  --name ai-knowledge-hub-es \
  -p 9200:9200 \
  -e discovery.type=single-node \
  -e xpack.security.enabled=false \
docker.elastic.co/elasticsearch/elasticsearch:8.14.3
```

先启动本地 PostgreSQL：

```bash
bash scripts/start_postgres_local.sh
```

启动服务：

```bash
uvicorn app.main:app --reload
```

打开 Swagger：

```text
http://127.0.0.1:8000/docs
```

启动前端：

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

打开前端首页：

```text
http://127.0.0.1:3000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

期望返回：

```json
{"status":"ok"}
```

验证 Elasticsearch：

```bash
curl http://localhost:9200
```

## 数据库

当前项目默认数据库就是 PostgreSQL，连接串在 `backend/.env`：

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ai_knowledge_hub
```

开发早期使用：

```python
SQLModel.metadata.create_all(engine)
```

自动创建不存在的表。Day 6 为 `documents.extracted_text` 增加了一个开发期补列逻辑；后续项目稳定后建议切换到 Alembic 管理迁移。

## API 文档

详细 API 说明见：

[docs/api.md](docs/api.md)

## 测试数据

项目里已经准备了一组多格式样例文件，目录：

[backend/data/sample_index_files](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/data/sample_index_files)

包含：

- `sample_policy_notice.txt`
- `sample_project_knowledge.md`
- `sample_supplier_management_policy.pdf`
- `sample_customer_success_review.docx`
- `sample_budget_and_risk_register.xlsx`

如果你改了样例内容，可以重新生成：

```bash
cd backend
python scripts/generate_sample_index_files.py
```

这组数据主要用于验证：

- 文本提取是否合理
- chunk 切分是否保住标题/表格/列表
- `/documents/{id}/index` 是否成功写入数据库和 Elasticsearch
- `/search/semantic` 是否能召回相关 chunk

## 推荐验收顺序

```text
1. POST /documents
2. POST /documents/{id}/chunks
3. GET /documents/{id}/chunks
4. POST /documents/{id}/index
5. POST /search/semantic
```

可以用这些问题做第一轮语义搜索验证：

- `采购复核的触发条件是什么？`
- `供应商准入流程怎么走？`
- `差旅报销单常见退回原因有哪些？`
- `客户成功团队本季度的主要问题是什么？`

## 学习文档

- [Day 1：后端基础](docs/day-01-backend-foundation.md)
- [Day 2：数据库模型](docs/day-02-database-models.md)
- [Day 3：知识库 CRUD API](docs/day-03-knowledge-base-crud.md)
- [Day 4：知识条目 CRUD API](docs/day-04-knowledge-item-crud.md)
- [Day 5：本地文件上传](docs/day-05-local-file-upload.md)
- [Day 6：PDF 支持](docs/day-06-pdf-support.md)
- [Day 8：TextSplitter](docs/day-08-text-splitter.md)
- [Day 9：Embedding + Elasticsearch](docs/day-09-embedding-elasticsearch-index.md)
- [Day 11：语义搜索 API](docs/day-11-semantic-search.md)
- [Day 13：RAG Service](docs/day-13-rag-service.md)
- [Day 14：RAG 流程整理](docs/day-14-rag-workflow.md)
- [Day 15：GraphState + 基础图](docs/day-15-graph-workflow.md)
- [Day 16：LLM Router](docs/day-16-llm-router.md)
- [Day 17：Retrieve Node](docs/day-17-retrieve-node.md)
- [Day 18：Answer Node](docs/day-18-answer-node.md)
- [Day 19：Relevance Check Node](docs/day-19-relevance-check-node.md)
- [Day 20：Checkpoint + Interrupt](docs/day-20-checkpoint-interrupt.md)
- [Day 21：Chat API 接入 Graph](docs/day-21-chat-api-graph.md)
- [Day 22：PostgreSQL 标准化](docs/day-22-postgresql-migration.md)
- [Day 23：前端初始化](docs/day-23-frontend-initialization.md)
- [Day 24：知识库列表页](docs/day-24-knowledge-base-page.md)
- [Day 25：文档上传和索引页](docs/day-25-document-upload-index-page.md)
- [Day 25-X：知识条目管理与 Chunk 可视化](docs/day-25x-knowledge-item-management.md)
- [Day 25-Y：手动知识条目切分与索引](docs/day-25y-manual-knowledge-item-indexing.md)
- [Day 26：语义搜索页面](docs/day-26-semantic-search-page.md)
- [Day 27：专家 Agent 问答页](docs/day-27-expert-agent-chat-page.md)
- [Day 28：前端迁移到 Vite](docs/day-28-vite-migration.md)
- [Day 29：会话持久化 Phase 1](docs/day-29-conversation-history-phase1.md)
