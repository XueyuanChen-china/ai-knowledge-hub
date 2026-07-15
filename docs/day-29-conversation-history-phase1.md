# Day 29：会话持久化 Phase 1

这一阶段只做一件事：

```text
把专家问答页从“只在前端内存里显示消息”
升级成“能看到后端持久化会话和历史消息”
```

先不碰 SSE。

## 目标

1. 左侧会话列表可用
2. 点击会话能加载历史消息
3. 新发起的问题仍然走现有 `/api/chat`
4. 会话继续沿用已有 `thread_id`

## 这次后端补了什么

### 1. 会话列表接口

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/chat.py`

新增：

```text
GET /api/conversations?knowledge_base_id=...
```

返回内容包括：

- `id`
- `knowledge_base_id`
- `title`
- `thread_id`
- `created_at`
- `updated_at`
- `message_count`
- `last_message_preview`
- `last_message_role`

这里的重点不是把整条会话一股脑全返回，而是给前端左侧列表提供刚好够用的摘要。

### 2. 会话消息列表接口

同样在：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/chat.py`

新增：

```text
GET /api/conversations/{conversation_id}/messages
```

返回：

- `id`
- `conversation_id`
- `role`
- `content`
- `citations`
- `created_at`

这里把 `metadata_json` 里的 citations 拆出来返回给前端，
这样前端就不用自己再解析一层原始 JSON 字符串。

### 3. conversation.updated_at 现在会跟消息一起刷新

之前 `updated_at` 主要在 `ensure_thread_conversation()` 时更新。

现在增加了：

- `touch_conversation()`

并在：

- `save_user_message()`
- `save_assistant_message()`

之后调用。

这样左侧会话列表按更新时间排序时，结果会更稳定。

不然的话，消息明明更新了，但会话时间不一定跟着动，列表顺序会怪。

## 这次前端补了什么

### 1. API client 增加会话读取能力

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/client.ts`
- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/types.ts`

新增：

- `getConversations()`
- `getConversationMessages()`
- `ConversationSummary`
- `ConversationMessage`

### 2. `/chat` 页面增加左侧会话栏

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/app/chat/page.tsx`

现在布局变成三栏：

```text
左侧：会话列表
中间：聊天消息
右侧：状态 / 审核面板
```

左侧会展示：

- 会话标题
- 最近一条消息摘要
- 更新时间
- 消息数

### 3. 点击会话后回显历史消息

前端点击左侧某个会话时，会调用：

```text
GET /api/conversations/{id}/messages
```

然后把返回的历史消息映射成页面里的消息气泡。

也就是说：

- 不是只显示本轮新问的问题
- 之前持久化到数据库里的 user / assistant 消息也能回来

### 4. 新建会话和继续会话分开了

页面左侧现在有一个：

- `新建`

作用是：

- 清空当前本地消息
- 清空 `thread_id`
- 清空当前 conversation 选择

这样下一次发送问题时，就会创建一条新的 conversation。

如果不点“新建”，而是在已有历史会话里继续问，
那前端会继续带上现有 `thread_id`，等于在同一条会话里往下追加。

## 当前链路

现在专家问答页这条链路变成：

```text
选择知识库
  -> 加载会话列表
点击某个会话
  -> 加载历史消息
继续提问
  -> /api/chat
  -> 保存 user / assistant message
  -> 会话列表刷新
```

## 为什么这个阶段先不做 SSE

因为当前最小目标是：

```text
会话可找回
消息可回显
线程能延续
```

这个阶段的风险点主要是：

- 数据模型怎么回给前端
- 前端如何区分新会话和旧会话
- 选择历史会话后，thread_id 是否还能继续用

这些先跑顺，比一上来就加 SSE 更稳。

## 当前验证方式

1. 打开：

```text
http://localhost:3000/chat
```

2. 选择知识库
3. 先发起一轮问答
4. 左侧应该看到会话记录
5. 点击左侧会话，应该回显历史消息
6. 再继续提问，应该沿用同一会话往下追加

## 下一步自然演进

Phase 2 再做：

- `POST /api/chat/stream`
- 流式答案输出
- assistant message 的 streaming 状态

到那一步时，左侧会话持久化这层就已经可以直接复用了。
