# Day 2：数据库模型

## 今天完成了什么

Day 2 的目标是把项目后续会用到的核心数据表先定义出来。

已完成 8 张表：

- `knowledge_bases`：知识库表
- `documents`：上传文档表
- `knowledge_items`：知识条目表
- `knowledge_item_reviews`：知识条目审核记录表
- `chunks`：文本切片表
- `conversations`：会话表
- `messages`：消息表
- `review_tasks`：人工审核任务表

这些表都写在：

```text
backend/app/db/models.py
```

启动 FastAPI 时会执行：

```python
SQLModel.metadata.create_all(engine)
```

它会根据 `models.py` 里的 SQLModel 类自动创建 SQLite 表。

## 表之间的关系

整体关系可以先按这个流程理解：

```text
knowledge_bases
  ↓
documents
  ↓
chunks

knowledge_bases
  ↓
knowledge_items
  ↓
knowledge_item_reviews
  ↓
knowledge_items
  ↓
chunks

knowledge_bases
  ↓
conversations
  ↓
messages
  ↓
review_tasks
```

换成业务语言就是：

```text
一个知识库可以有多个文档
一个文档可以被切成多个 chunk

一个知识库可以有多个知识条目
一个知识条目可以有多次审核记录
一个知识条目也可以被切成多个 chunk
chunk 必须归属于某个知识条目

一个知识库可以产生多轮问答会话
一个会话里有多条消息
一个会话可能触发人工审核任务
```

## 每张表的作用

### 1. `knowledge_bases`

知识库表。

例如：

```text
公司制度知识库
论文阅读知识库
客服 FAQ 知识库
```

核心字段：

```text
id
name
description
created_at
updated_at
```

后续 Day 3 可以围绕这张表写知识库 CRUD。

### 2. `documents`

上传文档表。

这张表不存文件本体，只存文件信息。

文件本体后续会放在：

```text
backend/data/uploads/
```

核心字段：

```text
knowledge_base_id
filename
file_path
file_type
status
```

`status` 当前设计为：

```text
uploaded：已上传，尚未完成向量化
indexed：已切分并写入向量库
failed：处理失败
```

### 3. `knowledge_items`

知识条目表。

它可以来自两种来源：

```text
manual：用户手动录入
document：从上传文档中提取
```

核心字段：

```text
knowledge_base_id
title
content
tags
status
source_type
source_document_id
```

`status` 当前设计为：

```text
draft：草稿，不参与 RAG
active：启用，参与 RAG
disabled：停用，不参与 RAG
```

### 4. `knowledge_item_reviews`

知识条目审核记录表。

这张表是为了给后续 Agent 审核提前留好结构。

它不直接保存知识正文，而是保存“谁审核了某条知识、审核结果是什么、为什么这样判断”。

核心字段：

```text
knowledge_item_id
reviewer_type
status
confidence_score
review_reason
reviewer_note
created_at
```

`reviewer_type` 当前设计为：

```text
human：人工审核
agent：Agent 自动审核
```

`status` 当前设计为：

```text
pending：等待审核
approved：审核通过
rejected：审核拒绝
need_human：Agent 不确定，需要转人工
```

这样以后可以支持：

```text
Agent 先审核
  ↓
高置信度直接 approved
  ↓
低置信度标记 need_human
  ↓
人工只处理少量疑难知识
```

注意：

```text
knowledge_items.status 控制这条知识最终是否参与 RAG
knowledge_item_reviews.status 记录某一次审核的结果
```

二者职责不同。

例如：

```text
Agent 审核通过：
knowledge_item_reviews.status = approved
knowledge_items.status = active
```

```text
Agent 不确定，需要人工：
knowledge_item_reviews.status = need_human
knowledge_items.status = draft
```

### 5. `chunks`

文本切片表。

RAG 不是直接把整篇文档丢给模型，而是先切成小段。

每一段就是一个 chunk。

核心字段：

```text
knowledge_base_id
document_id
knowledge_item_id
chunk_index
content
vector_id
metadata_json
```

`vector_id` 用来记录向量库里的 ID。

按当前设计：

```text
knowledge_item_id 必填
document_id 可选
```

原因是无论知识来自文档还是手动录入，都应该先形成 `KnowledgeItem`，再切成 `Chunk`。

`document_id` 只是为了方便从 chunk 反查原始上传文件：

```text
上传文档 → Document → KnowledgeItem → Chunk
手动录入 → KnowledgeItem → Chunk
```

也就是说：

```text
SQLite 负责存业务数据
向量库负责存向量数据（当前实现是 Elasticsearch）
vector_id 负责把两边关联起来
```

### 6. `conversations`

会话表。

用户在某个知识库里提问，会形成一次会话。

核心字段：

```text
knowledge_base_id
title
thread_id
created_at
updated_at
```

`thread_id` 很重要，后续会和 LangGraph 的 checkpoint 对应。

### 7. `messages`

消息表。

保存一次会话里的用户问题和助手回答。

核心字段：

```text
conversation_id
role
content
metadata_json
created_at
```

`role` 通常是：

```text
user
assistant
system
```

`metadata_json` 后续可以保存引用来源、模型名称、token 使用量等信息。

### 8. `review_tasks`

人工审核任务表。

当 RAG 检索结果不够可信时，系统可以暂停工作流，让人判断是否继续。

核心字段：

```text
conversation_id
question
docs_preview
status
human_note
created_at
updated_at
```

`status` 当前设计为：

```text
pending：等待审核
approved：审核通过
rejected：审核拒绝
```

## 为什么很多 JSON 先用字符串

例如：

```text
tags
metadata_json
docs_preview
```

这些字段第一版先用字符串保存 JSON。

原因是：

```text
1. SQLite 本身适合轻量开发
2. 第一版重点是功能闭环，不需要过早拆复杂表
3. 后续如果查询变复杂，再拆 tags 表、citations 表、review_docs 表
```

这是一种适合学习项目的渐进式设计。

## 如何验收

进入后端目录：

```bash
cd backend
```

启动服务：

```bash
uvicorn app.main:app --reload
```

启动后会自动创建数据库文件：

```text
backend/data/sqlite/ai_knowledge_hub.db
```

用 DB Browser for SQLite 打开这个文件，应该能看到：

```text
knowledge_bases
documents
knowledge_items
knowledge_item_reviews
chunks
conversations
messages
review_tasks
```

也可以用 Python 检查：

```bash
python - <<'PY'
import sqlite3

conn = sqlite3.connect("data/sqlite/ai_knowledge_hub.db")
tables = conn.execute(
    "select name from sqlite_master where type='table' order by name"
).fetchall()

for table in tables:
    print(table[0])
PY
```

## Day 3 建议

下一步建议开始写知识库 CRUD：

```text
POST /knowledge-bases
GET /knowledge-bases
GET /knowledge-bases/{id}
PATCH /knowledge-bases/{id}
DELETE /knowledge-bases/{id}
```

这样就能真正通过接口操作 Day 2 创建的第一张核心表。
