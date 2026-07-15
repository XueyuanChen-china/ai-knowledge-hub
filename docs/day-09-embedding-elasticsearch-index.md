# Day 9：Embedding + Elasticsearch 入库

## 今日目标

把“文档切片”继续往前推进到“向量化 + 向量入库”。

这一阶段完成后，项目会具备下面这条链路：

```text
上传文档
  -> 提取文本
  -> 切成 chunks
  -> 写入 PostgreSQL
  -> 生成 embedding
  -> 写入 Elasticsearch
```

---

## 本次实现内容

### 1. 接入 BGE-M3

文件：

[backend/app/services/vector_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/vector_service.py:1)

当前直接使用：

```text
sentence_transformers.SentenceTransformer("BAAI/bge-m3")
```

默认模型配置放在：

[backend/app/config.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/config.py:1)

默认模型：

```text
BAAI/bge-m3
```

当前这样选的原因是：

- 支持 100+ 语言
- 更适合中文知识库
- 支持长文本和多粒度检索场景
- 后续更容易升级 hybrid retrieval

---

### 2. 接入 Elasticsearch

同样在：

[backend/app/services/vector_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/vector_service.py:1)

当前使用：

```text
elasticsearch Python client
```

默认连接地址配置在：

[backend/app/config.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/config.py:1)

```text
http://localhost:9200
```

索引命名规则：

```text
knowledge_chunks_{knowledge_base_id}
```

也就是说，每个知识库单独一个 index。

当前第一版只落：

- dense embedding
- Elasticsearch `dense_vector`
- cosine 相似度

这样底座已经切到长期方案，但不会一次把 hybrid / sparse / multi-vector 一起做复杂。

---

### 3. 实现稳定 vector_id

函数：

`build_stable_vector_id(chunk: Chunk)`

设计思路：

不是用数据库自增主键，而是用下面这些字段组合后做哈希：

- `knowledge_base_id`
- `document_id`
- `knowledge_item_id`
- `chunk_index`
- `content_sha256`

这样做的好处是：

- 同一个 chunk 重建时，`vector_id` 仍然稳定
- 不依赖数据库里的 `chunk.id`
- 更适合重建索引和回归调试

---

### 4. 实现 `vector_service.add_chunks()`

核心职责：

1. 接收一批 `Chunk` 行对象
2. 生成稳定 `vector_id`
3. 调 embedding 模型生成向量
4. 把文本、metadata、embedding、id 一起写入 Elasticsearch

metadata 会先做一层清洗：

- 原生字符串、数字、布尔值直接保留
- `list` 保留为标量数组
- `dict` 转成 JSON 字符串

另外，Elasticsearch 里的向量字段 mapping 会显式建成：

- `embedding: dense_vector`
- `content: text`
- `vector_id: keyword`
- `knowledge_base_id / document_id / knowledge_item_id / chunk_index`

---

### 5. 新增 index 入口

文件：

[backend/app/api/document.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/document.py:1)

新增接口：

```text
POST /documents/{document_id}/index
```

这条接口会做：

1. 读取文档
2. 重新切 chunk
3. 旧 chunk 先删掉
4. 旧向量先从 Elasticsearch 删除
5. 新 chunk 写入 PostgreSQL
6. 新向量写入 Elasticsearch
7. 把 `chunks.vector_id` 回写到 PostgreSQL
8. 把 `documents.status` 更新成 `indexed`

返回：

- `document_id`
- `knowledge_item_id`
- `chunk_count`
- `vector_count`
- `index_name`

---

## 为什么保留 `/chunks`，又新增 `/index`

当前保留了两条接口：

### 1. `POST /documents/{id}/chunks`

只做：

```text
切片 + 写 PostgreSQL
```

适合你在开发时先单独确认：

- 文本提取对不对
- 切片结果对不对

### 2. `POST /documents/{id}/index`

做完整链路：

```text
切片 + PostgreSQL + Embedding + Elasticsearch
```

这样拆开后更适合学习，也更方便排查问题。

---

## 当前验收方式

### 1. 先上传一个 txt / md / pdf / docx / xlsx 文档

接口：

```text
POST /documents
```

### 2. 调 index

接口：

```text
POST /documents/{document_id}/index
```

### 3. 验收点

- `chunks` 表里能看到记录
- `chunks.vector_id` 不再是空
- `documents.status == indexed`
- Elasticsearch 对应 index 已创建
- 向量文档已写入 Elasticsearch

---

## 本次新增测试

### 1. 向量服务测试

文件：

[backend/tests/test_vector_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_vector_service.py:1)

覆盖：

- 稳定 `vector_id`
- metadata 清洗
- `add_chunks()`
- `delete_vectors()`

### 2. 文档索引测试

文件：

[backend/tests/test_document_indexing.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_document_indexing.py:1)

覆盖：

- `/index` 这条链路会把 chunk 写入数据库
- 会回写 `vector_id`
- 会把文档状态更新为 `indexed`

---

## 当前阶段说明

Day 9 现在已经实现了“本地 embedding + Elasticsearch dense vector”的第一版。

但这还是基础版，后面还可以继续增强：

- 检索接口
- 相似度查询
- kNN 查询
- hybrid retrieval
- sparse / multi-vector 升级
- 重建知识库全量索引
- 失败重试
- 更细的索引状态
- Alembic 迁移
