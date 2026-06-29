# API 文档

后端启动后，可以通过 Swagger 交互式测试：

```text
http://127.0.0.1:8000/docs
```

基础地址：

```text
http://127.0.0.1:8000
```

## 健康检查

### `GET /health`

确认后端服务是否正常启动。

响应示例：

```json
{
  "status": "ok"
}
```

## 知识库 API

### `POST /knowledge-bases`

创建知识库。

请求体：

```json
{
  "name": "公司制度知识库",
  "description": "保存公司制度、流程和规范"
}
```

成功状态码：

```text
201 Created
```

### `GET /knowledge-bases`

查询知识库列表。

### `GET /knowledge-bases/{knowledge_base_id}`

查询单个知识库。

不存在时返回：

```text
404 Knowledge base not found
```

### `PUT /knowledge-bases/{knowledge_base_id}`

更新知识库。

请求体：

```json
{
  "name": "公司制度与流程知识库",
  "description": "保存公司制度、流程、报销和审批规范"
}
```

### `DELETE /knowledge-bases/{knowledge_base_id}`

删除知识库。

成功状态码：

```text
204 No Content
```

## 知识条目 API

### `POST /knowledge-items`

手动创建知识条目。

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

`status` 支持：

```text
draft
active
disabled
```

非法状态会返回：

```text
400 Invalid status
```

### `GET /knowledge-items`

查询知识条目列表。

支持按知识库过滤：

```text
GET /knowledge-items?knowledge_base_id=1
```

支持按状态过滤：

```text
GET /knowledge-items?status=active
```

两个条件可以组合：

```text
GET /knowledge-items?knowledge_base_id=1&status=draft
```

### `GET /knowledge-items/{knowledge_item_id}`

查询单个知识条目。

不存在时返回：

```text
404 Knowledge item not found
```

### `PUT /knowledge-items/{knowledge_item_id}`

编辑知识条目。

请求体：

```json
{
  "knowledge_base_id": 1,
  "title": "新版报销规则",
  "content": "员工差旅报销需要提交发票、审批单和行程单。",
  "tags": "[\"财务\", \"报销\", \"差旅\"]",
  "status": "active"
}
```

### `DELETE /knowledge-items/{knowledge_item_id}`

删除知识条目。

成功状态码：

```text
204 No Content
```

## 文档上传 API

### `POST /documents`

上传本地文档。

请求类型：

```text
multipart/form-data
```

表单字段：

```text
knowledge_base_id：知识库 ID
file：上传文件
```

支持文件类型：

```text
.txt
.md
.pdf
```

上传成功后：

```text
文件保存到 backend/data/uploads
documents 表写入记录
txt / md / pdf 文本保存到 documents.extracted_text
```

响应示例：

```json
{
  "id": 1,
  "knowledge_base_id": 1,
  "filename": "faq.md",
  "file_path": "data/uploads/xxx_faq.md",
  "file_type": "md",
  "status": "uploaded",
  "extracted_text": "# FAQ\\n\\n这是文件内容。",
  "created_at": "2026-06-15T10:00:00"
}
```

其中 `id` 就是 `document_id`。

不支持的文件类型会返回：

```text
400 Only .md, .pdf, .txt files are supported
```

### `POST /documents/{document_id}/chunks`

把文档提取文本切成 chunk，并写入 `chunks` 表。

处理流程：

```text
读取 documents.extracted_text
  ↓
根据 file_type 选择切分策略
  ↓
创建或复用 KnowledgeItem
  ↓
删除该文档旧 chunks
  ↓
写入新 chunks
```

响应示例：

```json
{
  "document_id": 1,
  "knowledge_item_id": 3,
  "chunk_count": 5
}
```

如果文档不存在，返回：

```text
404 Document not found
```

如果文档没有可提取文本，返回：

```text
400 Document has no extracted text
```

### `GET /documents/{document_id}/chunks`

查询某个文档生成的所有 chunks。

这个接口用于直接在 Swagger 里查看切分结果，不需要额外安装 DB Browser。

响应示例：

```json
[
  {
    "id": 1,
    "knowledge_base_id": 1,
    "document_id": 1,
    "knowledge_item_id": 3,
    "chunk_index": 0,
    "content": "第一段内容……",
    "vector_id": null,
    "metadata_json": "{\"document_id\": 1}",
    "created_at": "2026-06-16T10:00:00"
  }
]
```

## Chunk 查询 API

### `GET /knowledge-items/{knowledge_item_id}/chunks`

查询某个知识条目下的所有 chunks。

这个接口适合检查某条知识最终参与检索的切片内容。

## 推荐验收顺序

1. `GET /health`
2. `POST /knowledge-bases`
3. `GET /knowledge-bases`
4. `POST /knowledge-items` 分别创建 `active`、`draft`、`disabled`
5. `GET /knowledge-items?knowledge_base_id=1`
6. `GET /knowledge-items?status=active`
7. `POST /documents` 上传 `.txt`、`.md`、`.pdf`
8. `POST /documents/{document_id}/chunks`
9. `GET /documents/{document_id}/chunks`
10. `GET /knowledge-items/{knowledge_item_id}/chunks`
