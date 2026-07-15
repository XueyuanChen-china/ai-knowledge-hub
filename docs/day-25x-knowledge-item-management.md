# Day 25-X：知识条目管理与 Chunk 可视化

## 本次目标

把“知识库 -> 知识条目 -> chunks”这一层前端补完整。

这一步完成后，用户不只是能上传文档和建索引，还能真正看到：

```text
某个知识库下有哪些知识条目
某条知识是手动录入还是文档生成
这条知识切出来了哪些 chunks
每个 chunk 的 vector_id 和 metadata 是什么
```

## 这次改了什么

### 1. 知识库详情页增加知识条目管理

文件：

[frontend/app/knowledge-bases/[id]/page.tsx](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/app/knowledge-bases/[id]/page.tsx)

现在这个页面除了知识库本身的编辑，还增加了：

- 知识条目列表
- 新建知识条目
- 编辑知识条目
- 删除知识条目
- 进入知识条目详情页

也就是说，知识库详情页现在变成这个结构：

```text
知识库详情
  -> 知识库信息
  -> 知识条目列表
  -> 手动新建知识条目
```

### 2. 新增知识条目表单和删除确认

文件：

- [frontend/components/knowledge-item-form.tsx](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/components/knowledge-item-form.tsx)
- [frontend/components/knowledge-item-delete-modal.tsx](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/components/knowledge-item-delete-modal.tsx)
- [frontend/components/knowledge-item-table.tsx](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/components/knowledge-item-table.tsx)

这里做的事情比较直接：

- 把知识条目 CRUD 的 UI 抽成独立组件
- 避免在页面里直接堆一大片表单逻辑
- 后面如果要做“知识条目独立列表页”，也能复用这些组件

### 3. 新增知识条目详情页

文件：

[frontend/app/knowledge-items/[id]/page.tsx](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/app/knowledge-items/[id]/page.tsx)

这个页面负责看单条知识的完整信息，包括：

- 标题
- 正文
- 状态
- 来源类型
- 来源文档 ID
- 关联的 chunks

chunk 展示里还把下面这些信息也带出来了：

- `chunk_index`
- `content`
- `vector_id`
- `metadata_json`

这样你就能从前端直接看到一条知识是怎么被切开的，不用再去翻数据库。

### 4. 前端 API client 补齐知识条目能力

文件：

- [frontend/lib/api/client.ts](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/client.ts)
- [frontend/lib/api/types.ts](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/types.ts)

新增：

- `getKnowledgeItem()`
- `createKnowledgeItem()`
- `updateKnowledgeItem()`
- `deleteKnowledgeItem()`
- `getKnowledgeItemChunks()`

以及类型：

- `KnowledgeItemPayload`
- `ChunkRecord`

## 这次后端补了什么

严格来说，这次要用的 chunks 接口其实后端已经有了：

文件：

[backend/app/api/knowledge_item.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/knowledge_item.py)

接口：

```text
GET /knowledge-items/{id}/chunks
```

这次我补的是测试，不是再造接口。

文件：

[backend/tests/test_knowledge_item_api.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_knowledge_item_api.py)

覆盖了两件事：

- 正常返回某个知识条目下的 chunks
- 条目不存在时返回 404

## 现在的数据关系是什么

当前版本的数据关系是：

```text
KnowledgeBase
  -> KnowledgeItem
    -> Chunk
```

对于文档来源知识：

```text
Document
  -> 1 KnowledgeItem
    -> N Chunks
```

对于手动录入知识：

```text
手动创建 KnowledgeItem
  -> 当前不会自动切 chunk
```

所以你现在会看到一种差异：

- 文档来源条目：通常已经有 chunks
- 手动条目：默认可能没有 chunks

这不是页面 bug，而是当前后端链路就是这样设计的。

## 当前前端验收方式

### 1. 打开知识库详情页

```text
http://localhost:3000/knowledge-bases/7
```

### 2. 验收知识条目管理

```text
1. 新建一个手动知识条目
2. 在列表里看到它
3. 编辑这条知识
4. 删除这条知识
```

### 3. 验收知识条目详情页

```text
1. 点击某条知识进入详情
2. 看到正文、状态、来源类型
3. 如果是文档来源条目，看到 chunks 列表
4. 看到每个 chunk 的 vector_id 和 metadata
```

## 当前边界

这一步先把知识条目层可视化补起来，还没做这些增强：

- 手动知识条目一键切 chunk
- 手动知识条目一键建索引
- chunk 内容二次编辑
- chunk 重切分
- chunk diff / 审核

如果后面继续做，这一层比较自然的下一步就是：

```text
手动知识条目 -> 点击切分 -> 生成 chunks -> 点击建索引
```
