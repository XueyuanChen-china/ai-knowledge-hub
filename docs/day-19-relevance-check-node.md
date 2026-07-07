# Day 19：Relevance Check Node

## 今日目标

在 `retrieve_node` 和 `answer_node` 之间插入一个真正的判断节点：

```text
retrieve
  -> relevance_check
      -> confident -> answer
      -> need_review -> review
```

这一阶段最重要的目标不是“提高回答质量”，而是：

```text
检索结果为空时不直接编答案
```

---

## 这次做了什么

### 1. 新增 `relevance_check_node`

[backend/app/graph/nodes.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/nodes.py:1)

这个节点现在做两件最基础的判断：

#### 第一层：`docs` 是否为空

如果：

```text
retrieval_hit_count == 0
```

那么直接判成：

```text
need_review
```

原因很直接：

- 当前没有任何证据
- 继续进入 `answer_node` 很容易变成硬答

#### 第二层：`top score` 是否低于阈值

如果：

```text
relevance_score < relevance_low_score_threshold
```

也判成：

```text
need_review
```

当前阈值走配置：

[backend/app/config.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/config.py:1)

```text
relevance_low_score_threshold = 0.35
```

这样后面你调不同语料集时，可以改配置，不用改代码。

---

### 2. GraphState 补了判断结果字段

[backend/app/graph/state.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/state.py:1)

这次新增：

- `relevance_decision`
- `review_reason`

现在状态里会明确写：

```text
confident
need_review
```

以及为什么进入 `need_review`，例如：

- `no retrieved documents`
- `top score 0.1200 below threshold 0.35`

这样后面接前端、日志、人工审核时，就不用重新推断原因。

---

### 3. `rag` 路径不再无条件进 `answer_node`

[backend/app/graph/workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/workflow.py:1)

以前是：

```text
router
  -> retrieve
  -> answer
```

现在改成：

```text
router
  -> retrieve
  -> relevance_check
      -> answer
      -> review
```

也就是说：

- 检索结果可信，才继续回答
- 结果为空或分数太低，就先停下来

---

### 4. 新增 `review_required_node`

这一版还没上真正的 `interrupt / resume`。

所以 Day 19 先加了一个临时 review 分支：

```text
review_required_node
```

它当前只做一件事：

```text
返回“当前检索结果不足以支持直接回答，需要人工复核。”
```

这一步的意义是：

- 明确不让系统乱答
- 给 Day 20 的 `human_review_node` 留出升级位置

后面 Day 20 会把这里替换成真正的中断与恢复。

---

## 当前判断逻辑

现在的最小逻辑是：

```text
if retrieval_hit_count == 0:
    need_review
elif relevance_score < threshold:
    need_review
else:
    confident
```

它还不是最终版，但已经能挡住两类最危险情况：

1. 一个结果都没有
2. 虽然有结果，但最相关分数明显太低

---

## 验收结果

测试文件：

[backend/tests/test_graph_workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_graph_workflow.py:1)

这次新增验证了：

### 1. 空检索会进入 `need_review`

- 不调用 `answer_node`
- 返回 review 提示

### 2. 低分检索也会进入 `need_review`

- 不调用 `answer_node`
- `review_reason` 会写清楚低分原因

### 3. 高分正常命中仍然会进入 `answer_node`

- `relevance_decision = confident`
- 正常返回 `answer + citations`

---

## 当前阶段的意义

Day 19 做完后，图工作流已经不再是“只要检索了就回答”。

而是变成：

```text
question
  -> retrieve
  -> relevance_check
  -> confident / need_review
```

这一步是后面做人审、interrupt、resume 的前提。
