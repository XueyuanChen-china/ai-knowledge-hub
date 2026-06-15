# Day 4：知识条目 CRUD API

## 今天完成了什么

Day 4 的目标是让用户可以通过 API 手动管理知识条目。

已完成接口：

```text
POST   /knowledge-items
GET    /knowledge-items
GET    /knowledge-items/{id}
PUT    /knowledge-items/{id}
DELETE /knowledge-items/{id}
```

其中列表接口支持过滤：

```text
GET /knowledge-items?knowledge_base_id=1
GET /knowledge-items?status=active
GET /knowledge-items?knowledge_base_id=1&status=draft
```

对应代码：

```text
backend/app/api/knowledge_item.py
backend/app/schemas/knowledge_item.py
backend/app/main.py
```

## 知识条目和知识库的关系

知识条目必须属于某个知识库。

所以创建知识条目时必须传：

```text
knowledge_base_id
```

如果传入的知识库不存在，接口会返回：

```text
404 Knowledge base not found
```

这样可以避免数据库里出现“没有归属知识库的知识条目”。

## status 的作用

知识条目目前支持 3 种状态：

```text
draft：草稿，不参与 RAG
active：启用，参与 RAG
disabled：停用，不参与 RAG
```

Day 4 已经在接口层做了校验。

如果传入其他值，比如：

```json
{
  "status": "published"
}
```

接口会返回：

```text
400 Bad Request
```

## 接口说明

### 1. 创建知识条目

```text
POST /knowledge-items
```

请求体：

```json
{
  "knowledge_base_id": 1,
  "title": "报销规则",
  "content": "员工差旅报销需要提交发票和审批单。",
  "tags": "[\"财务\", \"报销\"]",
  "status": "active"
}
```

成功状态码：

```text
201 Created
```

返回示例：

```json
{
  "id": 1,
  "knowledge_base_id": 1,
  "title": "报销规则",
  "content": "员工差旅报销需要提交发票和审批单。",
  "tags": "[\"财务\", \"报销\"]",
  "status": "active",
  "source_type": "manual",
  "source_document_id": null,
  "created_at": "2026-06-12T10:00:00",
  "updated_at": "2026-06-12T10:00:00"
}
```

### 2. 查询知识条目列表

```text
GET /knowledge-items
```

支持按知识库过滤：

```text
GET /knowledge-items?knowledge_base_id=1
```

支持按状态过滤：

```text
GET /knowledge-items?status=active
GET /knowledge-items?status=draft
GET /knowledge-items?status=disabled
```

也可以两个条件一起用：

```text
GET /knowledge-items?knowledge_base_id=1&status=active
```

### 3. 查询单个知识条目

```text
GET /knowledge-items/{id}
```

如果不存在，返回：

```text
404 Knowledge item not found
```

### 4. 编辑知识条目

```text
PUT /knowledge-items/{id}
```

请求体：

```json
{
  "knowledge_base_id": 1,
  "title": "更新后的报销规则",
  "content": "员工差旅报销需要提交发票、审批单和行程单。",
  "tags": "[\"财务\", \"报销\", \"差旅\"]",
  "status": "draft"
}
```

更新时会刷新：

```text
updated_at
```

### 5. 删除知识条目

```text
DELETE /knowledge-items/{id}
```

成功状态码：

```text
204 No Content
```

## Swagger 验收

启动服务：

```bash
cd backend
uvicorn app.main:app --reload
```

打开：

```text
http://127.0.0.1:8000/docs
```

按顺序测试：

```text
1. POST /knowledge-bases 先创建一个知识库
2. POST /knowledge-items 创建 active 知识
3. POST /knowledge-items 创建 draft 知识
4. POST /knowledge-items 创建 disabled 知识
5. GET /knowledge-items?knowledge_base_id=1 按知识库查询
6. GET /knowledge-items?status=active 按状态查询
7. PUT /knowledge-items/{id} 编辑知识条目
8. DELETE /knowledge-items/{id} 删除知识条目
```

## curl 验收

创建知识库：

```bash
curl -X POST http://127.0.0.1:8000/knowledge-bases \
  -H "Content-Type: application/json" \
  -d '{"name":"公司制度知识库","description":"用于 Day 4 测试"}'
```

创建 active 知识：

```bash
curl -X POST http://127.0.0.1:8000/knowledge-items \
  -H "Content-Type: application/json" \
  -d '{"knowledge_base_id":1,"title":"报销规则","content":"员工差旅报销需要提交发票和审批单。","tags":"[\"财务\", \"报销\"]","status":"active"}'
```

创建 draft 知识：

```bash
curl -X POST http://127.0.0.1:8000/knowledge-items \
  -H "Content-Type: application/json" \
  -d '{"knowledge_base_id":1,"title":"请假规则草稿","content":"请假规则待确认。","tags":"[\"人事\"]","status":"draft"}'
```

创建 disabled 知识：

```bash
curl -X POST http://127.0.0.1:8000/knowledge-items \
  -H "Content-Type: application/json" \
  -d '{"knowledge_base_id":1,"title":"旧版制度","content":"这是一条旧版制度。","tags":"[\"历史\"]","status":"disabled"}'
```

按知识库查询：

```bash
curl "http://127.0.0.1:8000/knowledge-items?knowledge_base_id=1"
```

按状态查询：

```bash
curl "http://127.0.0.1:8000/knowledge-items?status=active"
```

编辑：

```bash
curl -X PUT http://127.0.0.1:8000/knowledge-items/1 \
  -H "Content-Type: application/json" \
  -d '{"knowledge_base_id":1,"title":"新版报销规则","content":"员工差旅报销需要提交发票、审批单和行程单。","tags":"[\"财务\", \"报销\", \"差旅\"]","status":"active"}'
```

删除：

```bash
curl -X DELETE http://127.0.0.1:8000/knowledge-items/1
```

## 今天的关键点

### 为什么创建知识条目前要检查知识库

因为 `KnowledgeItem` 有这个字段：

```python
knowledge_base_id: int = Field(foreign_key="knowledge_bases.id", index=True)
```

它表示知识条目必须归属于某个知识库。

接口里用：

```python
ensure_knowledge_base_exists(payload.knowledge_base_id, session)
```

提前检查知识库是否存在，错误会更清楚。

### 为什么 status 要校验

如果不校验，数据库里可能会出现：

```text
actvie
published
enable
```

这种拼错或不统一的状态会让后续 RAG 过滤很麻烦。

所以 Day 4 先固定为：

```python
{"draft", "active", "disabled"}
```

### source_type 为什么固定为 manual

Day 4 做的是“手动创建知识条目”。

所以接口里创建时写死：

```python
source_type="manual"
source_document_id=None
```

等后面做文档上传和自动抽取时，再由文档处理流程创建：

```text
source_type = document
source_document_id = 对应 documents.id
```

## Day 5 建议

下一步可以做文档上传入口：

```text
POST /documents
GET /documents?knowledge_base_id=1
```

先把文件保存到 `backend/data/uploads`，再写入 `documents` 表。
