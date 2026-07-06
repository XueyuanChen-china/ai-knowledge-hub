# AI Knowledge Hub

企业知识库管理与专家 Agent 平台。

当前阶段先完成后端基础能力：

```text
知识库 CRUD
知识条目 CRUD
本地文档上传
PDF 文本提取
SQLite 数据持久化
文档混合切分
文档索引与语义搜索
RAG Service 最小闭环
```

## 技术栈

```text
后端：FastAPI
数据库：SQLite
ORM：SQLModel / SQLAlchemy
配置：pydantic-settings / .env
文件上传：python-multipart
PDF 解析：pypdf
PDF layout：pdfplumber
Word 解析：python-docx
Excel 解析：openpyxl
向量检索：Elasticsearch dense_vector
Embedding：BAAI/bge-m3
```

## 已完成功能

### Day 1：后端基础

- FastAPI 应用入口
- `.env` 配置读取
- SQLite 数据库配置
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

## 当前主链路

现在项目已经具备这样一条后端主链路：

```text
上传文档
  -> 提取文本
  -> 切分 chunks
  -> 写入 SQLite
  -> 生成 embedding
  -> 写入 Elasticsearch
  -> 语义搜索召回
  -> RAG Service 组装上下文与答案
```

## 项目结构

```text
ai-knowledge-hub/
  backend/
    app/
      main.py
      config.py
      api/
        document.py
        knowledge_base.py
        knowledge_item.py
      db/
        database.py
        models.py
      schemas/
        chunk.py
        document.py
        knowledge_base.py
        knowledge_item.py
      services/
        text_splitter.py
    data/
      uploads/
      sqlite/
    .env.example
    requirements.txt
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

启动服务：

```bash
uvicorn app.main:app --reload
```

打开 Swagger：

```text
http://127.0.0.1:8000/docs
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

SQLite 文件位置：

```text
backend/data/sqlite/ai_knowledge_hub.db
```

可以用 DB Browser for SQLite 打开查看。

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
- `/documents/{id}/index` 是否成功写入 SQLite 和 Elasticsearch
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
