# Day 1：后端基础 + 项目骨架

## 今天完成了什么

今天的目标不是马上写知识库 CRUD，而是先把后端项目跑起来，并且让后续数据库、接口、Agent 工作流都有地方放。

已完成：

- 创建 `ai-knowledge-hub` 项目
- 创建 `backend` 后端目录
- 配置 FastAPI
- 配置 `.env.example`
- 配置 SQLite 数据库地址
- 配置 SQLModel 数据库连接
- 创建最基础的数据表模型 `KnowledgeBase`
- 创建健康检查接口 `GET /health`
- 编写 README 初版

## 为什么先做这些

后端项目可以分成三层：

```text
请求入口 FastAPI
  ↓
业务逻辑 service
  ↓
数据库 SQLModel / SQLite
```

Day 1 只搭最底层骨架，避免一上来就把 CRUD、文档上传、RAG、Agent 混在一起。

## 关键文件说明

### `backend/app/main.py`

这是 FastAPI 的应用入口。

```python
app = FastAPI(title=settings.app_name)
```

后面启动时执行：

```bash
uvicorn app.main:app --reload
```

这句话的含义是：

- `app.main`：找到 `backend/app/main.py`
- `app`：使用里面定义的 FastAPI 实例
- `--reload`：代码改动后自动重启，适合开发环境

### `backend/app/config.py`

这里集中读取配置。

```python
class Settings(BaseSettings):
    app_name: str = "AI Knowledge Hub"
    app_env: str = "development"
    database_url: str = "sqlite:///./data/sqlite/ai_knowledge_hub.db"
```

以后数据库地址、API Key、模型名称都不要写死在业务代码里，而是放到 `.env`。

### `backend/app/db/database.py`

这里负责数据库连接。

```python
engine = create_engine(settings.database_url, connect_args=connect_args)
```

`engine` 可以理解成 Python 程序和数据库之间的连接管理器。

```python
SQLModel.metadata.create_all(engine)
```

这句会根据 SQLModel 模型创建数据库表。

### `backend/app/db/models.py`

这里放数据库表模型。今天先放一个 `KnowledgeBase`，为后续 CRUD 做准备。

```python
class KnowledgeBase(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=100)
    description: str = ""
```

这会对应 SQLite 里的 `knowledge_bases` 表。

## 启动步骤

进入项目后端目录：

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

创建 `.env`：

```bash
cp .env.example .env
```

启动：

```bash
uvicorn app.main:app --reload
```

测试：

```bash
curl http://127.0.0.1:8000/health
```

返回：

```json
{"status":"ok"}
```

## Day 2 建议

下一步可以正式写知识库 CRUD：

- `POST /knowledge-bases` 创建知识库
- `GET /knowledge-bases` 查询知识库列表
- `GET /knowledge-bases/{id}` 查询详情
- `PATCH /knowledge-bases/{id}` 修改名称和描述
- `DELETE /knowledge-bases/{id}` 删除知识库

建议新增目录：

```text
backend/app/api/
backend/app/schemas/
backend/app/services/
```
