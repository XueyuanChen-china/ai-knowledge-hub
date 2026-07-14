# Day 11：语义搜索 API

## 今日目标

在已经完成 `文档切分 -> embedding -> Elasticsearch 入库` 的基础上，补上第一版语义检索接口。

这一阶段完成后，链路会变成：

```text
上传文档
  -> 切分 chunk
  -> 生成 embedding
  -> 写入 Elasticsearch
  -> 输入问题
  -> 检索相关 chunk
```

---

## 本次实现内容

### 1. 新增语义搜索接口

文件：

[backend/app/api/search.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/search.py:1)

新增接口：

```text
POST /search/semantic
```

请求体：

```json
{
  "knowledge_base_id": 1,
  "query": "差旅报销怎么走流程？",
  "top_k": 5
}
```

返回每条结果包含：

- `doc_id`
- `chunk_id`
- `title`
- `content_preview`
- `score`
- `metadata`

---

### 2. 在 vector_service 里补检索能力

文件：

[backend/app/services/vector_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/vector_service.py:1)

新增了两个核心函数：

- `encode_query_text()`
- `search_similar_chunks()`

思路很直接：

```text
用户问题
  -> BGE-M3 编码成 query vector
  -> 用 Elasticsearch knn 查询 embedding 字段
  -> 返回最相近的 chunk
```

这里还是沿用当前 Day 9 的同一套模型：

```text
BAAI/bge-m3
```

这样可以保证：

- 文档入库向量和查询向量来自同一个模型
- 相似度空间一致
- 后续更容易继续加 rerank

---

### 3. 为什么 title 不是直接从 Elasticsearch 里拿

当前第一版索引文档里，重点保存的是：

- `content`
- `embedding`
- `document_id`
- `knowledge_item_id`
- `chunk_index`
- `metadata`

标题信息还是以 PostgreSQL 主表为准。

所以搜索返回后，会再根据 `knowledge_item_id` 去数据库里补标题。

这样做的好处是：

- 不会把业务主数据过早复制很多份
- 后面如果标题被人工修改，数据库仍然是主事实来源
- 当前检索结果量很小，补一次标题成本很低

---

### 4. content_preview 是做什么的

语义搜索结果里通常不会直接把整段 chunk 全量返回给列表页。

所以这里加了：

`build_content_preview()`

作用是：

- 先把换行压成空格
- 再截成较短预览文本

这样 Swagger 和后续前端列表更容易看。

---

## 当前验收方式

### 1. 先上传文档并构建索引

```text
POST /documents
POST /documents/{document_id}/index
```

### 2. 再调用语义搜索

```text
POST /search/semantic
```

### 3. 验收点

- 输入一个自然语言问题
- 能返回相关 chunk
- 结果里能看到标题、预览、分数和 metadata

---

## 本次测试

新增测试文件：

- [backend/tests/test_vector_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_vector_service.py:1)
- [backend/tests/test_semantic_search_api.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_semantic_search_api.py:1)

覆盖点：

- Elasticsearch kNN 查询参数是否构造正确
- 检索结果是否能正确解析成 hit
- API 是否能补齐知识条目标题
- 空查询是否会返回 400
