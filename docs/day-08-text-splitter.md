# Day 8：TextSplitter

## 今天完成了什么

Day 8 的目标是把文档里的 `extracted_text` 切成可以用于后续 RAG 的 chunks。

已完成：

```text
实现 backend/app/services/text_splitter.py
chunk_size=1000
chunk_overlap=200
支持 txt / md / pdf 混合切分
新增 POST /documents/{document_id}/chunks
新增 GET /documents/{document_id}/chunks
新增 GET /knowledge-items/{knowledge_item_id}/chunks
自动创建文档来源的 KnowledgeItem
写入 chunks 表
```

## 为什么不用简单固定切分

最简单的做法是：

```text
每 1000 字切一块，前后重叠 200 字
```

但这会有几个问题：

```text
1. 可能把标题和正文切开
2. 可能把一句话切断
3. 可能丢失 PDF 页码信息
4. Markdown 的章节结构无法保留
```

所以 Day 8 采用混合切分策略：

```text
结构切分 > 段落切分 > 句子切分 > 固定窗口切分
```

## 不同文件类型的策略

### Markdown

Markdown 优先按标题结构切：

```text
# 一级标题
## 二级标题
### 三级标题
```

切出来的 chunk metadata 会尽量保存：

```json
{
  "file_type": "md",
  "heading_path": ["报销制度", "差旅报销"],
  "splitter": "markdown_structure"
}
```

### TXT

TXT 主要按空行段落切：

```text
段落 1

段落 2

段落 3
```

如果段落太长，再按句子切。

如果句子还太长，最后才用固定窗口切。

### PDF

PDF 切分时会重新按页读取文件：

```text
第 1 页
第 2 页
第 3 页
```

然后页内按段落切。

metadata 会保存：

```json
{
  "file_type": "pdf",
  "page_start": 1,
  "page_end": 2,
  "splitter": "pdf_page_paragraph"
}
```

这样后续 Agent 回答时可以引用页码。

## 数据链路

按我们当前设计，所有 chunk 都必须归属于某个 `KnowledgeItem`。

所以 Day 8 的链路是：

```text
Document
  ↓
KnowledgeItem
  ↓
Chunk
```

当前第一版采用：

```text
一个 Document → 一个 KnowledgeItem → 多个 Chunk
```

后续可以升级成：

```text
一个 Document → 多个 KnowledgeItem → 每个 KnowledgeItem 多个 Chunk
```

## 新增接口

### `POST /documents/{document_id}/chunks`

把某个文档切成 chunks。

响应示例：

```json
{
  "document_id": 1,
  "knowledge_item_id": 3,
  "chunk_count": 5
}
```

处理流程：

```text
查 Document
  ↓
确认 extracted_text 不为空
  ↓
根据 file_type 选择 splitter
  ↓
创建或复用 KnowledgeItem
  ↓
删除该文档旧 chunks
  ↓
写入新 chunks
```

为什么会删除旧 chunks？

因为你可能会重复点击切分接口。如果不删除旧数据，chunks 表里会出现重复 chunk。

### `GET /documents/{document_id}/chunks`

查询某个文档生成的所有 chunks。

这个接口用来替代手动打开 DB Browser：

```text
上传文档
  ↓
触发切分
  ↓
直接在 Swagger 里查看 chunk 列表
```

### `GET /knowledge-items/{knowledge_item_id}/chunks`

查询某个知识条目下的所有 chunks。

因为当前设计是：

```text
Document → KnowledgeItem → Chunk
```

所以这个接口可以让你检查某个 KnowledgeItem 最终有哪些切片会参与后续 RAG。

## metadata_json 保存什么

每条 chunk 都会保存 metadata：

```json
{
  "document_id": 1,
  "filename": "员工手册.pdf",
  "file_type": "pdf",
  "knowledge_item_id": 3,
  "chunk_index": 0,
  "chunk_size": 1000,
  "chunk_overlap": 200,
  "splitter": "pdf_page_paragraph",
  "page_start": 1,
  "page_end": 1
}
```

这些信息后续用于：

```text
1. RAG 引用来源
2. 展示来自哪个文件
3. 展示 PDF 页码
4. 调试 chunk 是怎么切出来的
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
1. POST /knowledge-bases 创建知识库
2. POST /documents 上传 txt / md / pdf
3. POST /documents/{document_id}/chunks 触发切分
4. 查看返回 chunk_count
5. GET /documents/{document_id}/chunks 查看文档切片
6. GET /knowledge-items/{knowledge_item_id}/chunks 查看知识条目切片
```

## curl 验收

上传文档后，拿到 `document_id`。

触发切分：

```bash
curl -X POST http://127.0.0.1:8000/documents/1/chunks
```

检查 chunks 表：

```bash
curl http://127.0.0.1:8000/documents/1/chunks
```

按知识条目查询 chunks：

```bash
curl http://127.0.0.1:8000/knowledge-items/3/chunks
```

## Day 9 建议

下一步可以做 embedding 和向量库：

```text
读取 chunks 表
生成 embedding
写入 Chroma
回填 chunks.vector_id
```
