# Day 14：RAG 流程整理

## 今日目标

把第二周已经完成的能力收口成一条清晰的端到端流程，并把测试入口、数据流向、存储分工讲清楚。

这一阶段不是继续加新功能，而是回答三个问题：

1. 文档从上传到可检索，中间到底经过了哪些步骤？
2. PostgreSQL、Elasticsearch、RAG Service 各自负责什么？
3. 本地应该怎么准备测试数据和验收这条链路？

---

## 一、当前最小闭环

现在后端已经具备这样一条最小 RAG 链路：

```text
上传文档
  -> 提取文本
  -> 文档切分
  -> chunk 写入 PostgreSQL
  -> embedding 写入 Elasticsearch
  -> 语义搜索召回 chunk
  -> RAG Service 组装上下文
  -> 生成第一版答案
```

如果拆成接口和内部服务，可以看成：

```text
POST /documents
  -> documents.extracted_text

POST /documents/{id}/index
  -> split_document_text()
  -> chunks 表
  -> vector_service.add_chunks()
  -> Elasticsearch

POST /search/semantic
  -> vector_service.search_similar_chunks()
  -> 返回命中 chunk 摘要

rag_service.retrieve()
  -> rag_service.format_context()
  -> rag_service.generate_answer()
```

---

## 二、每一层在做什么

### 1. 上传层：Document

入口：

```text
POST /documents
```

这一层负责：

- 保存原文件到 `backend/data/uploads`
- 提取纯文本，保存到 `documents.extracted_text`
- 写入 `documents` 表

这里的 `extracted_text` 主要是：

- 方便你先确认上传内容有没有提对
- 作为文档内容的开发期快照

但它不是所有格式最终切分的唯一依据。

特别是：

- `pdf`
- `docx`
- `xlsx`

后面的切分阶段会优先直接走结构化 parser，而不是只依赖这段纯文本。

---

### 2. 切分层：Chunk

入口：

```text
POST /documents/{document_id}/chunks
POST /documents/{document_id}/index
```

内部主流程：

```text
parse
  -> normalize
  -> build_sections
  -> build_blocks
  -> assemble_chunks
```

这一层负责把文档转成真正参与检索的最小单元。

当前支持：

- Markdown 标题/段落/列表/表格/代码块
- TXT 标题检测 fallback
- PDF layout heading/paragraph/table
- DOCX heading/paragraph/list/table
- Excel sheet/table region

切分后会生成：

- `chunks.content`
- `chunks.metadata_json`
- `chunks.chunk_index`

并写入 PostgreSQL 的 `chunks` 表。

---

### 3. 向量层：Elasticsearch

入口：

```text
vector_service.add_chunks()
```

这一层负责：

- 用 `BAAI/bge-m3` 给每个 chunk 编码
- 生成稳定 `vector_id`
- 把向量文档写入 Elasticsearch

当前 ES 文档重点保存：

- `content`
- `embedding`
- `document_id`
- `knowledge_item_id`
- `chunk_index`
- `metadata`

这里要注意一个设计点：

> PostgreSQL 是业务主库，Elasticsearch 是检索索引。

也就是说：

- 主数据以 PostgreSQL 为准
- 检索召回以 Elasticsearch 为准

这也是为什么语义搜索结果里的标题，不是直接从 ES 里硬取，而是根据 `knowledge_item_id` 回 PostgreSQL 补齐。

---

### 4. 检索层：Semantic Search

入口：

```text
POST /search/semantic
```

链路：

```text
query
  -> encode_query_text()
  -> Elasticsearch knn
  -> top_k hits
  -> 回 PostgreSQL 补 title
  -> 返回搜索结果
```

返回结果里最重要的字段有：

- `doc_id`
- `chunk_id`
- `title`
- `content_preview`
- `score`
- `metadata`

其中：

- `content_preview` 主要给接口展示和列表预览
- 真正给模型用的应该是完整 `chunk.content`

---

### 5. 生成层：RAG Service

入口：

```text
rag_service.retrieve()
rag_service.format_context()
rag_service.generate_answer()
```

当前先做成“抽取式第一版”：

- `retrieve()`：召回 chunk，并补齐标题
- `format_context()`：把检索结果拼成统一上下文
- `generate_answer()`：从命中的 chunk 里抽取更相关的句子，形成第一版回答

它现在还没有接正式大模型 API，但接口形状已经稳定了。

后面如果接：

- OpenAI
- 通义千问
- LangGraph workflow

都可以继续复用这一层。

---

## 三、当前存储分工

可以这样记：

### PostgreSQL

负责：

- 知识库主数据
- 文档主数据
- 知识条目主数据
- chunk 结构化记录

适合回答：

- 这个 chunk 属于哪个文档？
- 这个文档属于哪个知识库？
- 标题是什么？
- 文档当前状态是 `uploaded` 还是 `indexed`？

### Elasticsearch

负责：

- 向量检索
- 语义相似度召回

适合回答：

- 和这个问题最接近的是哪些 chunk？

所以可以用一句话概括：

> PostgreSQL 负责“这是什么”，Elasticsearch 负责“最像什么”。

---

## 四、测试数据怎么准备

当前项目已经准备了一组多格式测试文件：

目录：

[backend/data/sample_index_files](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/data/sample_index_files)

包含：

- `sample_policy_notice.txt`
- `sample_project_knowledge.md`
- `sample_supplier_management_policy.pdf`
- `sample_customer_success_review.docx`
- `sample_budget_and_risk_register.xlsx`

生成脚本：

[backend/scripts/generate_sample_index_files.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/scripts/generate_sample_index_files.py:1)

如果你改了样例内容，可以重新生成：

```bash
cd backend
python scripts/generate_sample_index_files.py
```

这组文件的设计目标不是“随机凑几段文本”，而是尽量覆盖 splitter 和索引链路里真正容易出问题的边界：

- TXT 标题检测
- Markdown 层级标题 / 列表 / 表格 / 代码块
- PDF 页眉页脚 / 标题 / 双页 / 表格
- DOCX 标题 / 列表 / 表格混排
- Excel 多 sheet / 多 table region

---

## 五、推荐验收顺序

### 第一步：上传

```text
POST /documents
```

先看：

- 文件是否保存成功
- `documents.extracted_text` 是否合理

### 第二步：切分

```text
POST /documents/{id}/chunks
GET /documents/{id}/chunks
```

重点看：

- chunk 有没有明显脏数据
- 标题前缀是否正确
- 表格是否独立成块
- metadata 是否带来源信息

### 第三步：索引

```text
POST /documents/{id}/index
```

成功后应该看到：

- `chunk_count > 0`
- `vector_count > 0`
- `documents.status == indexed`

### 第四步：语义搜索

```text
POST /search/semantic
```

重点看：

- 能不能搜到相关 chunk
- 标题是否补齐
- 预览是否可读

### 第五步：RAG Service

当前先通过测试和内部调用验收：

- `retrieve()`
- `format_context()`
- `generate_answer()`

看它能不能完成：

```text
question -> docs -> context -> answer
```

---

## 六、这一周完成后的状态

Day 14 整理完成后，第二周已经有了一个能跑通的最小知识库问答闭环：

```text
多格式文档上传
  -> 结构化切分
  -> embedding 入库
  -> 语义搜索
  -> RAG answer
```

这条链路的价值在于：

- 你现在已经不是只有 CRUD
- 也不是只有“切分一下文本”
- 而是已经有了一条真正面向知识检索和问答的后端主链路

后面再继续往上搭：

- 对话接口
- LangGraph workflow
- 人工审核节点
- 权限过滤
- rerank

都会更顺。
