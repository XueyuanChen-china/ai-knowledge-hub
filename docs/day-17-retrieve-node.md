# Day 17：Retrieve Node

## 今日目标

把 `rag` 分支正式收口成一个明确的 `retrieve_node`。

这一阶段不负责答案生成，只负责把检索结果稳定写回图状态：

```text
Elasticsearch 检索
  -> retrieved_docs
  -> context
  -> docs_preview
  -> citations
```

---

## 这次做了什么

### 1. `rag_retrieve_node` 正式升级成 `retrieve_node`

[backend/app/graph/nodes.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/nodes.py:1)

现在 `rag` 路由进入的是：

```text
retrieve_node
```

它的职责固定成：

- 调 `rag_service.retrieve()`
- 把 top-k 文档写入 `retrieved_docs`
- 把格式化后的上下文写入 `context`
- 把简短检索预览写入 `docs_preview`
- 把引用信息写入 `citations`

旧的 `rag_retrieve_node()` 还保留了一个兼容壳，内部直接转到 `retrieve_node()`。

这样做的原因很简单：

- Day 17 的节点职责已经明确是 Retrieve Node
- 后面再加 `answer_node`、`relevance_check_node` 时，节点边界会更清楚

---

### 2. `docs_preview` 变得更像“打印检索结果”

以前的 `docs_preview` 只放：

```text
标题 + chunk_id + score
```

现在每一条预览会多带一段内容摘要，例如：

```text
[1] 采购制度 | chunk_id=20 | score=0.9100 | 单次采购金额超过二十万元，需要采购委员会复核。
```

这样后面你调 workflow 时，直接看 `docs_preview` 就能判断：

- 召回的是哪条
- 分数大概多少
- 内容是不是对路

不用每次都展开 `retrieved_docs`。

---

### 3. GraphState 补了检索命中数

[backend/app/graph/state.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/state.py:1)

这次新增：

- `retrieval_hit_count`

这个字段的作用很直接：

- 让后面的 `relevance_check_node` 更容易接
- 让日志和调试更直观

比如第一版相关性检查完全可以先用：

```text
retrieval_hit_count == 0 -> need_human_review = True
retrieval_hit_count > 0 -> 先认为有结果
```

---

### 4. workflow 的 `rag` 分支正式接到 Retrieve Node

[backend/app/graph/workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/workflow.py:1)

现在流程是：

```text
START
  -> router
      -> direct
      -> retrieve
      -> rag（复杂总结暂时复用 RAG）
  -> END
```

也就是说，`rag` 现在在图里的落点已经不是“一个临时检索函数”，而是正式节点。

---

## 当前写回了哪些状态

对 `rag` 问题，`retrieve_node()` 现在会写：

- `retrieved_docs`
- `retrieval_hit_count`
- `context`
- `docs_preview`
- `citations`
- `relevance_score`
- `need_human_review`

其中：

- `context` 给 Day 18 的 `answer_node` 用
- `docs_preview` 给调试、日志和人工检查用
- `citations` 给后面答案引用用

---

## 验收结果

测试文件：

[backend/tests/test_graph_workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_graph_workflow.py:1)

这次重点验证了：

### 1. `rag` 问题会进入 `retrieve_node`

并且会写回：

- `context`
- `docs_preview`
- `citations`
- `retrieval_hit_count`

### 2. 没有检索结果时会标记人工复核

如果：

```text
retrieved_docs == []
```

那么会得到：

- `retrieval_hit_count = 0`
- `need_human_review = True`

这就给 Day 19 的 `relevance_check_node` 留好了接点。

---

## 当前阶段的意义

Day 17 完成后，图工作流里已经有了一个真正稳定的“检索节点”。

也就是说，现在不是只有：

```text
路由判断
```

而是已经变成：

```text
问题
  -> Router
  -> Retrieve Node
  -> 写回标准检索状态
```

下一步再做 Day 18，就可以直接基于 `context + citations` 接 `answer_node`。
