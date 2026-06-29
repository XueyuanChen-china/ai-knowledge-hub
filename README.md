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
```

## 技术栈

```text
后端：FastAPI
数据库：SQLite
ORM：SQLModel / SQLAlchemy
配置：pydantic-settings / .env
文件上传：python-multipart
PDF 解析：pypdf
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

## 学习文档

- [Day 1：后端基础](docs/day-01-backend-foundation.md)
- [Day 2：数据库模型](docs/day-02-database-models.md)
- [Day 3：知识库 CRUD API](docs/day-03-knowledge-base-crud.md)
- [Day 4：知识条目 CRUD API](docs/day-04-knowledge-item-crud.md)
- [Day 5：本地文件上传](docs/day-05-local-file-upload.md)
- [Day 6：PDF 支持](docs/day-06-pdf-support.md)
- [Day 8：TextSplitter](docs/day-08-text-splitter.md)
