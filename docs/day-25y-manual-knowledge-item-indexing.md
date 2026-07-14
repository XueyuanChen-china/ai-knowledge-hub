# Day 25-Y：手动知识条目切分与索引

## 本次目标

把手动知识条目补成真正能进入检索链路的数据，而不是只停留在 `knowledge_items` 表里。

这次补完后，链路变成：

```text
手动新建 KnowledgeItem
  -> 生成 chunks
  -> 写 chunks 表
  -> 构建向量索引
  -> 写 Elasticsearch
  -> 回填 chunks.vector_id
```

## 这次改了什么

### 1. 后端新增两个接口

文件：

[backend/app/api/knowledge_item.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/knowledge_item.py)

新增：

```text
POST /knowledge-items/{id}/chunks
POST /knowledge-items/{id}/index
```

它们的职责分别是：

- `/chunks`
  - 只做切分
  - 只写 PostgreSQL `chunks`
- `/index`
  - 重新切分
  - 写 PostgreSQL `chunks`
  - 生成 embedding
  - 写 Elasticsearch
  - 回填 `chunks.vector_id`

### 2. 手动知识条目切分时自动补标题前缀

因为手动知识条目没有原始文档 heading 结构，所以如果直接拿正文去切，后续检索时上下文容易弱。

这次的处理是：

- 先按 plain text 规则切正文
- 每个 chunk 如果没有现成标题，就自动补：

```text
# 知识条目标题

chunk 正文
```

这样对后续语义检索和回答生成更友好。

### 3. 前端知识条目详情页补了两个按钮

文件：

[frontend/app/knowledge-items/[id]/page.tsx](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/app/knowledge-items/[id]/page.tsx)

新增：

- `生成 Chunks`
- `构建索引`

点击后会：

- 调后端接口
- 刷新当前条目的 chunk 列表
- 展示最近一次处理结果

### 4. 删除知识条目时，先清相关 chunks / vectors

还是这个文件：

[backend/app/api/knowledge_item.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/knowledge_item.py)

这一步很重要。

如果条目已经切分或已经索引，再直接删 `knowledge_items` 主记录，会遇到两类问题：

- PostgreSQL 外键拦住
- Elasticsearch 里残留脏向量

所以现在删除条目时会先：

```text
删 Elasticsearch vectors
删 PostgreSQL chunks
再删 KnowledgeItem
```

## 当前链路长什么样

### 只切分

```text
前端点“生成 Chunks”
  -> POST /knowledge-items/{id}/chunks
  -> 读取 knowledge_item.content
  -> split_document_text(..., file_type="txt")
  -> 删除旧 chunks
  -> 写新 chunks
```

### 切分 + 索引

```text
前端点“构建索引”
  -> POST /knowledge-items/{id}/index
  -> 删除旧 vectors
  -> 重新生成 chunks
  -> add_chunks()
  -> 写 Elasticsearch
  -> 回填 chunks.vector_id
```

## 这次补的前端类型和 API

文件：

- [frontend/lib/api/types.ts](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/types.ts)
- [frontend/lib/api/client.ts](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/client.ts)

新增：

- `KnowledgeItemChunkResponse`
- `KnowledgeItemIndexResponse`
- `splitKnowledgeItemIntoChunks()`
- `indexKnowledgeItem()`

## 验收方式

### 1. 先新建一个手动知识条目

入口：

```text
http://localhost:3000/knowledge-bases/7
```

### 2. 进入知识条目详情页

例如：

```text
http://localhost:3000/knowledge-items/{id}
```

### 3. 点“生成 Chunks”

预期：

- 页面显示 chunk 数增加
- 下方 chunks 表出现内容

### 4. 点“构建索引”

预期：

- 页面显示 vector_count
- chunks 表里的 `vector_id` 不再为空

## 当前边界

这一步先让手动知识条目可以进入检索链路，但还没做：

- 手动条目切分参数自定义
- 手动条目重切分预览 diff
- 批量为多个条目建索引
- 按条目状态自动限制是否允许进入 RAG

后面如果要继续做，下一步比较自然的是：

```text
只有 active 条目参与语义搜索
```

这样业务语义会更完整。
