# Day 15：GraphState + 基础图

## 今日目标

把已经完成的语义检索和 RAG Service 接成一条最小可控工作流。

这一阶段先不急着接真正大模型 router，也不急着引入更复杂节点。
先把最重要的三件事固定下来：

1. GraphState 长什么样
2. router / direct / rag 三个节点分别负责什么
3. 工作流最小分支怎么跑通

---

## 本次新增文件

### 1. 状态定义

[backend/app/graph/state.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/state.py:1)

这里新增了 `GraphState`。

当前先按 `TypedDict` 设计，而不是上来就绑死到某个框架类，目的是：

- 字段清楚
- 节点之间传值简单
- 后面切到 LangGraph 时还能直接复用

这次先保留的核心字段有：

- `question`
- `knowledge_base_id`
- `route`
- `retrieved_docs`
- `context`
- `docs_preview`
- `answer`
- `citations`
- `node_trace`

其中：

- `route` 表示当前问题走 `direct` 还是 `rag`
- `node_trace` 用来记录这次执行到底经过了哪些节点

---

### 2. 节点实现

[backend/app/graph/nodes.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/nodes.py:1)

这次先实现了三个节点：

#### `router_node`

负责把问题分成：

- `direct`
- `rag`

第一版先走规则，不调用 LLM。

当前规则很保守：

- `你好`
- `什么是 RAG`
- `什么是 Agent`
- `什么是 Docker`

这类明显不需要查知识库的问题，先走 `direct`

如果：

- 问题不是明显 direct
- 且提供了 `knowledge_base_id`

就进入 `rag`

这一步的目标不是把所有问题分到极致，而是先把最基础的工作流边界跑通。

#### `direct_answer_node`

当前 direct 分支先不检索知识库，直接返回一个占位回答。

这样做的目的很简单：

- 先验证 router 分支是否正确
- 保证 direct 问题不会误入检索链路
- 给后面 Day 16 的 LLM direct answer 留位置

#### `rag_retrieve_node`

当前 rag 分支第一版先做：

- 调 `rag_service.retrieve()`
- 调 `rag_service.format_context()`
- 生成 `docs_preview`
- 生成 `citations`

所以它现在更像：

```text
rag 入口节点
  -> 进入检索
  -> 把检索结果写回 GraphState
```

后面 Day 18 再继续加 answer node。

---

### 3. 工作流入口

[backend/app/graph/workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/workflow.py:1)

这里新增了 `BasicGraphWorkflow`。

当前流程非常简单：

```text
START
  -> router
    -> direct
    -> rag
  -> END
```

它现在还不是完整的 LangGraph 编排器，而是一个轻量基础图包装。

这样做是有意的：

- Day 15 先把节点和状态固定住
- 不把问题扩大到框架集成
- 等 Day 16/17/18 再继续加 LLM router、retrieve node、answer node

---

## 为什么 direct 问题不检索

因为不是所有问题都该走 RAG。

比如：

- `你好`
- `什么是 RAG`

这类问题本质上不依赖你的企业知识库。

如果这时候还强行做：

```text
embedding
  -> Elasticsearch
  -> retrieve
```

会有两个问题：

1. 浪费检索成本
2. 容易把本来不该查库的问题，回答成“知识库里某段文本”

所以 Day 15 的关键验收点之一就是：

> direct 问题不进入 retrieve

---

## 为什么 rag 问题要先停在 retrieve

因为现在工作流还在搭骨架。

当前已经有：

- `rag_service.retrieve()`
- `rag_service.format_context()`
- `rag_service.generate_answer()`

但如果 Day 15 一口气把：

- router
- retrieve
- answer
- save_message

全塞进图里，调试会很难。

所以这次先把 rag 分支接到 retrieve：

- 验证分支判断没问题
- 验证知识库检索会被触发
- 验证 context / docs_preview 能写回状态

等这个稳定了，再继续加 answer node。

---

## 本次测试

测试文件：

[backend/tests/test_graph_workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_graph_workflow.py:1)

覆盖了两个核心验收点：

### 1. direct 问题不触发 retrieve

问题示例：

```text
你好
```

期望：

- `route == direct`
- 不调用 `rag_service.retrieve()`

### 2. rag 问题进入 retrieve

问题示例：

```text
采购复核的触发条件是什么？
```

期望：

- `route == rag`
- 会调用 `rag_service.retrieve()`
- `retrieved_docs / context / docs_preview` 会写回状态

---

## 当前阶段的意义

Day 15 完成后，项目从“只有检索服务函数”变成了“有可控工作流骨架”。

这很重要，因为后面你再往上叠：

- LLM router
- relevance check
- human review
- answer generation
- save message

都不再是散着写函数，而是沿着一个明确的图在扩展。
