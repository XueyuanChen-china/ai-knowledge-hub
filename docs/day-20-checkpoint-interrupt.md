# Day 20：Checkpoint + Interrupt

## 今日目标

把当前图工作流从“低置信度时给出一条 review 提示”升级成真正可暂停、可恢复的流程。

这一阶段的目标是：

```text
retrieve 后暂停
外部通过 thread_id 恢复
批准后继续 answer
拒绝后结束
```

---

## 这次做了什么

### 1. 新增 LangGraph 工作流模块

[backend/app/graph/langgraph_workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/langgraph_workflow.py:1)

这里单独落了一份 Day 20 用的 LangGraph 工作流。

这样做的原因是：

- Day 15~19 的基础节点函数还能继续单元测试
- Day 20 的 `checkpoint / interrupt / resume` 能独立编排
- 不需要把前面的基础骨架一次性推倒重来

这份工作流使用：

- `StateGraph`
- `InMemorySaver`
- `interrupt()`
- `Command(resume=...)`

当前图结构是：

```text
START
  -> router
      -> direct
      -> retrieve
      -> complex

retrieve
  -> relevance_check
      -> answer
      -> human_review

human_review
  -> answer
  -> review_rejected
```

---

### 2. 使用 `InMemorySaver`

当前 checkpointer 是：

```python
CHECKPOINTER = InMemorySaver()
```

这表示：

- 当前能暂停和恢复
- 但状态只存在进程内存里
- 服务重启后 checkpoint 会丢失

这正适合 Day 20 的开发验收。

正式项目后面如果要持久化，就要把它换成更稳的 saver。

---

### 3. `thread_id` 正式接上了

这次 `thread_id` 不只是数据库字段了，而是真的参与到 LangGraph checkpoint 里：

```python
config = {"configurable": {"thread_id": thread_id}}
```

它的作用是：

- 标识一条执行线程
- 让 LangGraph 知道该从哪个 checkpoint 恢复

所以 Day 20 之后，`thread_id` 已经成为真正的工作流恢复键。

---

### 4. 新增 `human_review_node`

在 LangGraph 模块里，这个节点不是简单返回文本，而是：

```python
resume_value = interrupt(payload)
```

也就是说：

- 第一次运行到这里时，会暂停
- 把 `payload` 抛给外部
- 外部稍后用 `Command(resume=...)` 恢复

当前抛出去的 payload 包含：

- `question`
- `thread_id`
- `route`
- `docs_preview`
- `retrieval_hit_count`
- `relevance_score`
- `review_reason`
- `citations`

这样前端、Swagger 或脚本就能看到这次为什么被卡住。

---

### 5. `Command(resume=...)` 恢复方式

恢复时现在传的是：

```json
{
  "approved": true,
  "human_note": "允许继续生成答案"
}
```

或者：

```json
{
  "approved": false,
  "human_note": "证据不足，拒绝直接回答"
}
```

恢复后：

- `approved=true` -> 继续走 `answer_node`
- `approved=false` -> 进入 `review_rejected_node`

---

## 新增 API

[backend/app/api/chat.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/chat.py:1)

这次新增了两个接口：

### 1. `POST /api/chat`

作用：

- 启动图工作流
- 如果命中 interrupt，就返回 `interrupted`
- 如果能直接完成，就返回 `completed`

请求体：

```json
{
  "knowledge_base_id": 7,
  "question": "采购复核的触发条件是什么？"
}
```

可能返回：

#### 正常完成

```json
{
  "status": "completed",
  "thread_id": "...",
  "answer": "...",
  "citations": [...]
}
```

#### 进入人工复核

```json
{
  "status": "interrupted",
  "thread_id": "...",
  "need_human_review": true,
  "review_payload": {...}
}
```

### 2. `POST /api/review/resume`

作用：

- 对一个已经暂停的 thread 做恢复

请求体：

```json
{
  "thread_id": "...",
  "approved": true,
  "human_note": "允许继续生成答案"
}
```

恢复后返回：

- `completed`

---

## ReviewTask 与 Conversation

这次没有只做“纯内存 interrupt”，还顺手把最基本的业务记录接上了：

- 会自动创建 `Conversation`
- 低置信度 interrupt 时会创建 `ReviewTask`
- resume 后会把 `ReviewTask.status` 更新成：
  - `approved`
  - `rejected`

这意味着 Day 20 不只是技术演示，也已经有一点业务壳了。

---

## 验收结果

测试文件：

[backend/tests/test_chat_api.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_chat_api.py:1)

现在验证了三条主线：

### 1. 空结果会 interrupt

`POST /api/chat`

- 返回 `status=interrupted`
- 返回 `thread_id`
- 返回 `review_payload`

### 2. resume 批准后继续生成答案

`POST /api/review/resume`

- `approved=true`
- 继续进 `answer_node`
- 返回 `answer + citations`

### 3. resume 拒绝后停止

`POST /api/review/resume`

- `approved=false`
- 返回拒绝提示
- 不再进入 `answer_node`

---

## 当前阶段的意义

Day 20 做完后，系统已经具备了真正的 HITL 基础闭环：

```text
question
  -> retrieve
  -> relevance_check
  -> interrupt
  -> resume
  -> answer / reject
```

Day 21 接口层再收口后，这条可暂停、可恢复的图就能直接暴露给前端和 Swagger 使用。
