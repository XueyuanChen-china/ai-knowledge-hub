# Day 27：专家 Agent 问答页

这一阶段把原来的“对话工作台调试页”收成一个真正可用的前端问答页。

目标是把这条链路从前端跑通：

```text
发送问题
  -> 调 /api/chat
  -> completed: 展示答案和引用
  -> interrupted: 展示审核面板
  -> 点击通过 / 拒绝
  -> 调 /api/review/resume
  -> 返回最终答案或拒绝结果
```

## 这次改了什么

### 1. 前端类型补齐了 citations 和 review payload

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/types.ts`

新增：

- `ChatCitation`
- `ChatReviewPayload`

之前前端把这些数据都当成松散 JSON 用，页面能渲染，但类型不清楚。

这次补齐后，前端就能明确知道：

- 引用来源里有哪些字段
- interrupted 时 review panel 能拿到哪些信息

### 2. API client 新增 resume 调用

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/client.ts`

新增：

- `resumeChat()`

对应后端接口：

- `POST /api/review/resume`

这样前端审核面板点“通过 / 拒绝”时，就不需要自己手写 fetch 了。

### 3. `/chat` 页面升级成真正的问答页

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/app/chat/page.tsx`

这次不再只是展示一段原始返回，而是拆成三块：

1. 左侧聊天区
2. 右侧会话状态
3. interrupted 时的审核面板

## 页面现在具备的能力

### 聊天窗口

左侧聊天区现在会按消息展示：

- 用户问题
- Agent 回答
- 系统暂停提示

这样你就能看到一轮会话是怎么往前推进的，而不是每次只看最后一条接口返回。

### 引用来源展示

如果回答里带 citations，会在回答消息下方直接展示来源：

- `title`
- `doc_id`
- `chunk_id`
- `score`

这比直接看 JSON 更接近真实产品页。

### interrupted 时的审核面板

如果 `/api/chat` 返回：

```text
status = interrupted
```

前端会：

1. 记录当前 `thread_id`
2. 在聊天区插入一条“等待审核”的系统消息
3. 右侧打开审核面板

审核面板会展示：

- 当前问题
- review reason
- docs preview
- 审核说明输入框

然后你可以：

- `通过并继续`
- `拒绝并结束`

### resume 之后会发生什么

点按钮后，前端会调：

```text
POST /api/review/resume
```

并带上：

- `thread_id`
- `approved`
- `human_note`
- `retrieve_top_k`

如果通过：

- Graph 会继续执行
- 前端收到最终答案
- 聊天区追加一条 Agent 回答

如果拒绝：

- Graph 会走 `review_rejected`
- 前端同样会收到一个最终结果
- 聊天区会追加“人工复核未通过”的回答

所以 Day 27 的关键点不是“只看到中断”，而是“中断以后还能从前端继续走完”。

## 会话线程怎么处理

这一版前端已经把 `thread_id` 接起来了。

也就是说同一页里连续提问时，会沿用当前线程，而不是每问一次都新建一个独立会话。

另外右上角加了“重置会话”按钮：

- 清空本地消息
- 清空当前 thread
- 开启一轮新对话

## 导航文案也收了一下

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/components/app-frame.tsx`

把原来的：

- `对话工作台`

改成：

- `专家问答`

这样和语义搜索页的职责更容易区分：

- 语义搜索：看召回
- 专家问答：看问答和审核流

## 当前验收方式

1. 打开：

```text
http://localhost:3000/chat
```

2. 选择一个知识库
3. 发送普通问题，验证：

- 能看到用户消息
- 能看到 Agent 回答
- 能看到引用来源

4. 再发送一个容易触发审核的问题，例如知识库里不存在的问题，验证：

- 返回 interrupted
- 右侧出现审核面板
- 点击通过或拒绝能继续调 resume API
- 聊天区出现最终结果

## 当前边界

这版已经满足 Day 27 的最小前端闭环，但还没做：

- 会话历史持久化列表
- 多会话切换
- markdown 富文本答案渲染
- chunk 跳转到知识条目详情
- 人工审核操作日志列表

这些更适合放到下一阶段产品化里继续做。
