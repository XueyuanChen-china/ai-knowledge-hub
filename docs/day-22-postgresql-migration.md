# Day 22：PostgreSQL 标准化

这一阶段的目标已经不是“数据库可切换”，而是把项目明确收口为：

```text
PostgreSQL = 唯一关系型主库
Elasticsearch = 检索与向量索引层
```

也就是说，SQLite 不再参与运行时。

## 这次做了什么

1. `DATABASE_URL` 默认改成 PostgreSQL
2. 后端数据库初始化改成 PostgreSQL-only
3. 删除 SQLite 运行时分支
4. 本地测试统一切到 PostgreSQL
5. 历史 SQLite 数据迁移到 PostgreSQL

## 核心代码改动

### 1. `backend/app/config.py`

当前默认数据库地址直接是：

```env
postgresql+psycopg://postgres:postgres@localhost:5432/ai_knowledge_hub
```

不再保留 SQLite 作为默认值。

### 2. `backend/app/db/database.py`

现在这个文件只保留 PostgreSQL 主路径：

- `is_postgresql_url(database_url)`
- `validate_database_url(database_url)`
- `build_engine(database_url)`

这里的设计意思是：

- 如果给了 PostgreSQL 连接串，正常启动
- 如果还是给 SQLite 连接串，启动时直接报错

这样能避免“看起来启动了，实际还连着旧 SQLite 文件”的隐性问题。

### 3. 保留最小补列逻辑

`ensure_document_columns()` 还在，但它不再是 SQLite 专属逻辑。

它现在只是一个开发期兜底：

- 如果旧 PostgreSQL 库里缺 `documents.extracted_text`
- 启动时补一次列

后面等表结构稳定，还是建议改成 Alembic。

## 本地运行方式

### 启动 PostgreSQL

```bash
cd backend
bash scripts/start_postgres_local.sh
```

### 默认连接信息

```text
host: localhost
port: 5432
database: ai_knowledge_hub
username: postgres
password: postgres
```

### 启动后端

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

## 数据迁移说明

项目之前确实积累过一份 SQLite 数据。

这次标准化时，已经把历史数据迁移到了 PostgreSQL。迁移过程中还发现并修复了一处旧脏数据：

- 有两条 `documents` 记录引用了已经不存在的 `knowledge_base_id=3`
- SQLite 之前没有把这个问题拦下来
- PostgreSQL 外键在迁移时把它拦下来了

最后处理方式是：

- 先补齐一个“历史恢复知识库”
- 再把整套数据迁过去

这说明 PostgreSQL 比 SQLite 更适合作为后续长期主库，因为它会更早暴露数据一致性问题。

## 为什么现在不再保留 SQLite 双栈

因为双栈在这个项目阶段已经没有收益，反而会带来三类问题：

1. 文档和讲解越来越容易分叉
2. 测试环境和真实运行环境不一致
3. SQLite 会放过一部分 PostgreSQL 会拦住的数据问题

现在统一 PostgreSQL 后，整条链路更一致：

```text
本地开发
测试
后续部署
```

都围绕同一个数据库类型展开。

## 这次改动的边界

这次只改数据库底座，不改这些东西：

- Elasticsearch 索引结构
- embedding 逻辑
- RAG / Graph 工作流
- 前端页面交互

所以如果切库后出问题，优先看：

1. PostgreSQL 是否启动
2. `DATABASE_URL` 是否正确
3. 表是否创建成功
4. 历史数据是否已经迁移
