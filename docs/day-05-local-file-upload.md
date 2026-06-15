# Day 5：本地文件上传

## 今天完成了什么

Day 5 的目标是实现最基础的文档上传能力。

已完成接口：

```text
POST /documents
```

接口能力：

```text
上传 txt / md 文件
保存文件到 backend/data/uploads
写入 documents 表
返回 document_id
```

对应代码：

```text
backend/app/api/document.py
backend/app/schemas/document.py
backend/app/main.py
backend/requirements.txt
```

## 为什么需要 python-multipart

浏览器上传文件时，请求格式通常是：

```text
multipart/form-data
```

FastAPI 解析这种请求需要：

```text
python-multipart
```

所以 Day 5 在 `requirements.txt` 里新增了：

```text
python-multipart==0.0.20
```

## 接口说明

### 上传文档

```text
POST /documents
```

请求类型：

```text
multipart/form-data
```

字段：

```text
knowledge_base_id：文件所属知识库 ID
file：上传的 txt / md 文件
```

支持的文件类型：

```text
.txt
.md
```

如果上传其他文件，比如 `.pdf`，当前会返回：

```text
400 Bad Request
```

## 保存逻辑

上传成功后，文件会保存到：

```text
backend/data/uploads
```

为了避免同名文件互相覆盖，后端会给文件名前面加 UUID。

例如你上传：

```text
faq.md
```

实际保存可能是：

```text
data/uploads/7a9f0c4d8c1e4f389bbd6f8e9f9f0d11_faq.md
```

数据库 `documents` 表会记录：

```text
knowledge_base_id
filename
file_path
file_type
status
created_at
```

其中：

```text
filename：用户上传时的原始文件名
file_path：保存到本地后的路径
file_type：txt / md
status：uploaded
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

先创建一个知识库：

```text
POST /knowledge-bases
```

然后找到：

```text
POST /documents
```

填入：

```text
knowledge_base_id：刚才创建的知识库 ID
file：选择一个 .txt 或 .md 文件
```

点击 Execute。

成功后会返回类似：

```json
{
  "id": 1,
  "knowledge_base_id": 1,
  "filename": "faq.md",
  "file_path": "data/uploads/xxx_faq.md",
  "file_type": "md",
  "status": "uploaded",
  "created_at": "2026-06-12T10:00:00"
}
```

这里的 `id` 就是：

```text
document_id
```

## curl 验收

创建知识库：

```bash
curl -X POST http://127.0.0.1:8000/knowledge-bases \
  -H "Content-Type: application/json" \
  -d '{"name":"文档上传测试知识库","description":"用于 Day 5 测试"}'
```

上传文件：

```bash
curl -X POST http://127.0.0.1:8000/documents \
  -F "knowledge_base_id=1" \
  -F "file=@/path/to/test.md"
```

检查文件是否保存：

```bash
ls backend/data/uploads
```

检查数据库是否有记录：

```bash
cd backend
python - <<'PY'
import sqlite3

conn = sqlite3.connect("data/sqlite/ai_knowledge_hub.db")
rows = conn.execute(
    "select id, knowledge_base_id, filename, file_path, file_type, status from documents"
).fetchall()

for row in rows:
    print(row)
PY
```

## 今天的关键点

### `UploadFile`

FastAPI 用 `UploadFile` 接收上传文件：

```python
file: UploadFile = File(...)
```

`UploadFile` 里有：

```text
filename：原始文件名
file：文件对象
content_type：浏览器传来的 MIME 类型
```

Day 5 主要使用：

```python
file.filename
file.file.read()
```

### `Form`

文件上传接口不能像普通 JSON 接口那样接收请求体。

所以 `knowledge_base_id` 使用：

```python
knowledge_base_id: int = Form(...)
```

这样它会从 `multipart/form-data` 表单字段里读取。

### 为什么先只支持 txt / md

因为 txt / md 都是纯文本，后续读取和切分简单。

PDF 需要额外解析库，容易把 Day 5 做复杂。

推荐后续再单独做：

```text
PDF 解析
文档切分
生成 KnowledgeItem
生成 Chunk
写入向量库
```

## Day 6 建议

下一步可以做文档读取和切分：

```text
读取 txt / md
按段落切分
生成 knowledge_items
生成 chunks
```
