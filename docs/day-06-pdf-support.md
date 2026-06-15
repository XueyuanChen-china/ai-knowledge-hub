# Day 6：PDF 支持

## 今天完成了什么

Day 6 的目标是在已有文件上传能力上支持 PDF。

已完成：

```text
接入 pypdf
支持上传 PDF
支持 PDF 文本提取
把提取文本保存到 documents.extracted_text
```

对应代码：

```text
backend/app/api/document.py
backend/app/db/models.py
backend/app/db/database.py
backend/app/schemas/document.py
backend/requirements.txt
```

## 新增依赖

Day 6 新增：

```text
pypdf==5.1.0
```

安装方式：

```bash
pip install -r requirements.txt
```

## documents 表新增字段

`documents` 表新增：

```text
extracted_text
```

作用是保存从上传文件里提取出的纯文本。

现在支持：

```text
txt：直接读取 UTF-8 文本
md：直接读取 UTF-8 文本
pdf：使用 pypdf 提取文本
```

## 为什么加 ensure_document_columns

项目早期使用：

```python
SQLModel.metadata.create_all(engine)
```

它只负责创建不存在的表。

如果表已经存在，它不会自动新增字段。

所以 Day 6 在：

```text
backend/app/db/database.py
```

里加了一个开发期补列函数：

```python
ensure_document_columns()
```

它会检查 `documents` 表里有没有：

```text
extracted_text
```

如果没有，就执行：

```sql
ALTER TABLE documents ADD COLUMN extracted_text TEXT DEFAULT ''
```

这只是开发期方案。

后续项目稳定后，应该换成：

```text
Alembic
```

来管理数据库迁移。

## 上传接口

接口仍然是：

```text
POST /documents
```

支持文件类型从 Day 5 的：

```text
.txt
.md
```

扩展为：

```text
.txt
.md
.pdf
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

先创建知识库：

```text
POST /knowledge-bases
```

然后上传 PDF：

```text
POST /documents
```

表单字段：

```text
knowledge_base_id：知识库 ID
file：选择一个 PDF 文件
```

成功后响应里会包含：

```text
extracted_text
```

如果 PDF 里有可提取文本，`extracted_text` 应该能看到文字内容。

## curl 验收

上传 PDF：

```bash
curl -X POST http://127.0.0.1:8000/documents \
  -F "knowledge_base_id=1" \
  -F "file=@/path/to/test.pdf"
```

检查数据库：

```bash
cd backend
python - <<'PY'
import sqlite3

conn = sqlite3.connect("data/sqlite/ai_knowledge_hub.db")
rows = conn.execute(
    "select id, filename, file_type, length(extracted_text), substr(extracted_text, 1, 120) from documents"
).fetchall()

for row in rows:
    print(row)
PY
```

## 注意事项

`pypdf` 只能提取 PDF 中已经存在的文本层。

如果 PDF 是扫描图片，比如拍照版合同，里面没有文本层，那么 `extracted_text` 可能为空。

这种情况后续需要接：

```text
OCR
```

Day 6 暂时不做 OCR。

## Day 7 建议

下一步可以做文档切分：

```text
读取 documents.extracted_text
按段落切分
生成 knowledge_items
生成 chunks
```
