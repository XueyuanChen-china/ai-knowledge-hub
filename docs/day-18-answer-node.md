# Day 18：Answer Node

## 今日目标

把图工作流里的 `rag` 分支从“只有检索”推进到“检索后能生成答案”。

这一阶段的职责边界是：

```text
retrieve_node
  -> 负责检索和上下文组装

answer_node
  -> 负责基于 context 调千问生成答案
  -> 返回 answer
  -> 返回 doc/chunk 级 citations
```

---

## 这次做了什么

### 1. 新增 `llm_answer_service.py`

[backend/app/services/llm_answer_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/llm_answer_service.py:1)

这个文件负责 Answer Node 的模型调用。

它做了三件事：

#### 第一件：组装 Answer Prompt

`build_answer_messages()`

输入是：

- `question`
- `context`

要求模型只基于当前上下文回答，并输出结构化 JSON：

```json
{
  "answer": "回答正文",
  "used_context_numbers": [1, 2]
}
```

这里的 `used_context_numbers` 很关键。

我没有让模型直接返回：

- `doc_id`
- `chunk_id`

因为那样更容易编错。

现在是让模型只说“用了第几个 context 片段”，
然后应用端再把它映射回真实的：

- `doc_id`
- `chunk_id`

这样引用来源更稳。

#### 第二件：调用 Qwen

`call_openai_compatible_chat()`

这里继续走 OpenAI 兼容接口，并显式开启 JSON Mode：

```json
{
  "response_format": {
    "type": "json_object"
  }
}
```

同时 prompt 里也明确要求输出 JSON。

#### 第三件：做 fallback

`generate_answer()`

这一层不是“只要 LLM 失败就直接报错”，而是：

```text
先尝试 Qwen
  -> 成功则用 LLM 结果
  -> 失败则退回 rag_service.generate_answer()
```

所以 Day 18 的 Answer Node 是可用的，不是脆的。

---

### 2. 新增 `answer_node`

[backend/app/graph/nodes.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/nodes.py:1)

`answer_node()` 负责：

- 读取 `question`
- 读取 `retrieved_docs`
- 调 `llm_answer_service.generate_answer()`
- 把结果写回 GraphState

写回的字段有：

- `answer`
- `context`
- `citations`
- `answer_used_fallback`

其中：

- `answer_used_fallback=False` 表示走了 Qwen
- `answer_used_fallback=True` 表示退回本地抽取式答案

---

### 3. `rag` 分支现在是完整两步

[backend/app/graph/workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/workflow.py:1)

现在的 `rag` 分支已经变成：

```text
router
  -> retrieve_node
  -> answer_node
  -> END
```

这意味着 Day 18 完成后，图工作流已经不只是“检索到了什么”，而是“能产出最终答案”。

---

### 4. Answer 配置支持回退到 Router 配置

[backend/app/config.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/config.py:1)

这次新增了：

- `llm_answer_base_url`
- `llm_answer_api_key`
- `llm_answer_model`
- `llm_answer_timeout_seconds`

但第一版又做了一个实用处理：

如果没有单独配置 Answer 参数，就自动回退到 Router 的配置。

也就是说，你现在只配一套：

- base_url
- api_key
- model

项目也能直接跑通。

---

## 为什么引用不用模型直接返回 doc/chunk

因为那样容易出现这类问题：

- 模型编了一个不存在的 `chunk_id`
- 引用了检索列表外的来源
- `doc_id / chunk_id` 对不上

现在的设计是：

```text
模型返回 used_context_numbers
  -> 应用端按上下文顺序映射成 citations
```

这个映射是确定性的。

所以引用来源的主事实来源仍然是应用端，不是模型幻想出来的 ID。

---

## 验收结果

测试覆盖了两类情况：

[backend/tests/test_graph_workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_graph_workflow.py:1)

- `rag` 路径会进入 `retrieve -> answer`
- 最终会返回 `answer + citations`

[backend/tests/test_llm_answer_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_llm_answer_service.py:1)

- 能解析 Answer JSON
- 能把 `used_context_numbers` 映射成真实 citations
- 无配置时会退回本地抽取式答案

---

## 当前阶段的意义

Day 18 做完后，图工作流的最小回答闭环已经成立：

```text
question
  -> router
  -> retrieve
  -> answer
  -> answer + citations
```

后面 Day 19 再做 `relevance_check_node`，重点就不再是“能不能回答”，
而是“什么时候不该直接回答”。
