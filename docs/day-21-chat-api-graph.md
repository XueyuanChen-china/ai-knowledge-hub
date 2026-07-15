# Day 21：Chat API 接入 Graph

## 今日目标

把 Day 20 已经具备的 LangGraph 工作流正式接成 API 入口，并且把路径收口成项目对外接口口径：

```text
POST /api/chat
POST /api/review/resume
```

这样后面无论你是用：

- Swagger
- 前端
- 脚本

都能直接按统一路径调用图工作流。

---

## 这次做了什么

### 1. API 路径正式切到 `/api/*`

[backend/app/api/chat.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/chat.py:1)

这次把 Day 20 的：

- `POST /chat`
- `POST /chat/resume`

收口成：

- `POST /api/chat`
- `POST /api/review/resume`

这更符合后端接口的统一命名方式。

同时为了避免你本地已有脚本立刻失效，代码里保留了隐藏的 legacy 路径：

- `/api/_legacy/chat`
- `/api/_legacy/chat/resume`

它们不会出现在 Swagger 里。

---

### 2. `POST /api/chat` 现在真正调用 graph

`POST /api/chat` 内部会：

```text
创建或读取 Conversation
保存 user message
调用 LangGraph invoke
根据执行结果返回 completed / interrupted
```

也就是说，这个接口不是简单包一下 service，而是真正接到了：

- checkpoint
- interrupt
- review task
- answer generation

---

### 3. `POST /api/review/resume` 负责恢复图执行

这个接口的职责是：

```text
根据 thread_id 找到对应 checkpoint
调用 Command(resume=...)
继续执行 human_review 后半段
```

如果：

- `approved=true`

就继续走 `answer_node`

如果：

- `approved=false`

就走 `review_rejected_node`

---

## 当前完整流程

现在已经满足这条链：

```text
用户提问
  -> /api/chat
  -> 检索
  -> relevance_check
  -> interrupt
  -> /api/review/resume
  -> 继续生成答案
```

这就是 Day 21 的验收目标。

---

## 请求示例

### 1. 发起提问

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "knowledge_base_id": 7,
    "question": "采购复核的触发条件是什么？"
  }'
```

如果直接能回答，会返回：

```json
{
  "status": "completed",
  "thread_id": "...",
  "answer": "...",
  "citations": [...]
}
```

如果需要人工复核，会返回：

```json
{
  "status": "interrupted",
  "thread_id": "...",
  "review_payload": {...}
}
```

### 2. 恢复审核

```bash
curl -X POST http://127.0.0.1:8000/api/review/resume \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "...",
    "approved": true,
    "human_note": "允许继续生成答案"
  }'
```

---

## 测试覆盖

[backend/tests/test_chat_api.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_chat_api.py:1)

现在验证了：

- `/api/chat` 命中 interrupt
- `/api/review/resume` 批准后继续生成答案
- `/api/review/resume` 拒绝后停止流程

---

## 当前阶段的意义

Day 21 做完后，这个项目已经不是“内部有 graph 能跑”，而是：

```text
对外有标准 API
  -> 能触发 graph
  -> 能返回 interrupt
  -> 能恢复执行
```

这一步是把底层工作流真正产品化的开始。
