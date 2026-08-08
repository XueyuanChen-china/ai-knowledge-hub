# Phase 2：Context Management 上下文管理

## 1. 本阶段解决什么问题

在最初的问答流程里，程序会把会话历史、检索结果和工具结果拼到一起，再交给
大模型。如果对话变长，会出现几个典型问题：

1. **上下文越来越长**：每一轮都把完整历史发给模型，最终超过模型上下文窗口。
2. **重要信息被挤掉**：最近几条消息可能只是寒暄，但更早的一条消息包含关键金额、
   制度编号或用户已经确认的决定。
3. **检索证据被截断**：如果直接按字符截取，一个 chunk 可能只剩半句话，引用编号
   还可能和实际证据对不上。
4. **不同节点看到的信息不合理**：Router 只需要判断路线，却可能收到大量文档正文；
   Answer 需要检索证据，却可能只拿到 Router 的小上下文。
5. **用户使用指代时缺少主体**：用户说“这个金额谁审批”，但当前窗口里没有“这个”
   所指的原始问题。
6. **摘要不可追踪**：如果摘要只保存一段字符串，很难知道它来自哪些消息，也不知道
   摘要是否已经覆盖某一条历史消息。

本阶段的核心原则是：

> PostgreSQL 保存完整业务历史；Context Manager 只负责为某一次 LLM 请求构造一个
> 有预算、可追踪、可恢复的上下文包。

因此，**上下文管理不是删除历史消息，也不是把数据库历史永久压缩掉**。它只是决定
“这一轮请求给模型看哪些内容”。

## 2. 先区分三个概念

### 2.1 完整业务历史：`messages`

用户消息和助手消息继续保存到 PostgreSQL 的 `messages` 表中。历史接口仍然可以
读取完整消息，前端也可以回显完整对话。

```text
messages 表
  ├── 用户：采购复核的触发条件是什么？
  ├── 助手：单次采购金额超过二十万元需要采购委员会复核。
  ├── 用户：那这个金额谁审批？
  └── 助手：...
```

这张表是业务事实来源。Context Manager 不会因为上下文超限而物理删除这些记录。

### 2.2 会话摘要：`Conversation.context_summary`

当会话历史较长时，系统使用 Router 配置的 LLM 生成一个结构化摘要，保存在
`Conversation.context_summary` 字段中。数据库字段仍然是字符串，但字符串内容是
JSON，而不是任意格式的长文本。

摘要用于保留跨多轮对话的稳定事实，例如：

```json
{
  "version": 2,
  "facts": [
    "采购复核的触发条件是单次采购金额超过二十万元"
  ],
  "decisions": [
    "当前项目先采用 PostgreSQL + Elasticsearch"
  ],
  "open_questions": [
    "是否需要增加供应商分类字段"
  ],
  "entities": ["采购复核", "供应商管理"],
  "source_message_ids": [12, 13, 18],
  "summarized_through_message_id": 18,
  "generated_at": "2026-08-06T10:30:00"
}
```

### 2.3 本次请求上下文：`ContextPack`

ContextPack 是一次具体 LLM 请求的“输入清单”。它可能包含：

```text
系统指令
固定约束
结构化摘要
最近几条消息
按当前问题找出的相关历史
当前检索证据
工具结果摘要或引用
预算使用情况
被省略内容的原因
```

一个会话可以有很多个 ContextPack：Router 有一个，Query Rewrite 有一个，Answer
还有一个。它们来自同一份业务历史，但预算和内容范围不同。

## 3. 代码入口和职责

### 3.1 结构化类型

文件：

`backend/app/services/context_types.py`

这个文件只定义数据结构，不负责查询数据库或调用模型。

| 类型                  | 作用                                   |
| --------------------- | -------------------------------------- |
| `ContextItem`       | 可以整体放入或整体移除的普通上下文单元 |
| `StructuredSummary` | 结构化会话摘要                         |
| `ConversationMemory` | 数据库里的会话级长期记忆记录          |
| `EvidenceItem`      | 一条完整的检索证据，通常对应一个 chunk |
| `ToolResultRef`     | 工具结果的摘要、来源 ID 和错误信息     |
| `BudgetBreakdown`   | 记录不同内容用了多少预算               |
| `OmittedItem`       | 记录哪些内容没有进入本次上下文以及原因 |
| `RecoveryAction`    | 记录是否执行过历史恢复以及恢复来源     |

这些类型都提供 `to_dict()`，所以可以写入 LangGraph state、checkpoint 或 API
响应，而不依赖 Python 对象本身。

### 3.2 预算配置

文件：

`backend/app/services/context_budget.py`

配置集中在：

`backend/app/config.py`

当前主要配置如下：

```text
context_router_max_tokens = 800
context_rewrite_max_tokens = 1600
context_answer_max_tokens = 6000

Router max_recent_messages = 2（由代码中的 router budget 固定）
context_rewrite_recent_messages = 6
context_answer_recent_messages = 6

context_router_history_max_tokens = 0
context_rewrite_history_max_tokens = 400
context_answer_history_max_tokens = 1000
context_answer_retrieval_max_tokens = 4000
context_answer_tool_max_tokens = 1000
```

这里的 Token 是第一版估算值，不是模型 tokenizer 的精确值。当前算法大致使用：

```text
估算 Token 数 ≈ 字符数 / 2
```

这个估算的目标是避免请求无限增长，不是用来计算账单。

### 3.3 Context Manager

文件：

`backend/app/services/context_manager.py`

主要入口：

| 函数                                    | 作用                                                    |
| --------------------------------------- | ------------------------------------------------------- |
| `build_context_pack()`                | 按指定用途和预算构造一个 Pack                           |
| `build_conversation_contexts()`       | 一次构造 Router、Rewrite、Answer 三个上下文             |
| `build_answer_context()`              | 把检索结果转换为完整`EvidenceItem` 后构造 Answer Pack |
| `parse_structured_summary()`          | 解析 JSON 摘要，同时兼容旧纯文本摘要                    |
| `should_refresh_summary()`            | 判断是否满足摘要更新条件                                |
| `summarize_conversation_with_llm()`   | 调用 Router LLM 生成结构化摘要                          |
| `maybe_update_conversation_summary()` | 保存摘要并更新摘要游标                                  |

## 4. ContextPack 的结构

当前 `ContextPack` 同时保留旧字段和新字段，目的是渐进式升级，不要求所有旧的
prompt 一次重写。

### 4.1 兼容字段

```python
summary: str
retrieval_context: str
tool_results: list[str]
recent_messages: list[dict[str, str]]
estimated_tokens: int
truncated: bool
```

旧代码可以继续从这些字段读取文本。

### 4.2 结构化字段

```python
system_instructions: list[str]
pinned_constraints: list[ContextItem]
persistent_memory: list[ContextItem]
conversation_summary: StructuredSummary | None
recent_messages: list[dict[str, str]]
relevant_history: list[ContextItem]
evidence_items: list[EvidenceItem]
tool_result_refs: list[ToolResultRef]
budget: BudgetBreakdown
omitted_items: list[OmittedItem]
recovery_actions: list[RecoveryAction]
current_question: str
```

### 4.3 为什么需要 `EvidenceItem`

不能简单把所有 chunk 拼成一个很长的字符串，然后从字符串中间裁剪。因为这样
可能产生：

```text
原始证据：单次采购金额超过二十万元，需要由采购委员会复核。

错误裁剪：单次采购金额超过二十万元，需要由采购委...
```

`EvidenceItem` 把一个 chunk 当作一个原子单元。放不进预算时，当前实现会将整个
证据放入 `omitted_items`，而不是把 chunk 切成半句。

### 4.4 为什么需要 `omitted_items`

如果只返回 `truncated=true`，我们只知道“发生了裁剪”，不知道丢了什么。
现在会记录类似：

```json
{
  "kind": "evidence",
  "source_id": "chunk-81",
  "reason": "evidence_is_atomic_and_does_not_fit_budget",
  "estimated_tokens": 620
}
```

它有三个用途：

1. 调试为什么某条证据没有进入 Answer。
2. 指标统计上下文预算是否经常不足。
3. 后续实现“按需重新加载”时知道缺失的是哪一类内容。

## 5. 三个 LLM 上下文如何隔离

### 5.1 不是三个独立会话

Router、Rewrite、Answer 仍然属于同一次用户请求，也可以使用同一份 GraphState。
“隔离”指的是：每个节点有独立的上下文构造结果和预算，不是创建三套用户会话。

```text
PostgreSQL messages + Conversation.context_summary
                         |
                         v
              build_conversation_contexts()
                 /          |          \
                /           |           \
             Router       Rewrite      Answer
             800           1600         6000
```

### 5.2 Router 看什么

Router 的职责是判断：

```text
direct / rag / complex
```

所以它主要拿：

- 当前问题；
- 少量近期消息；
- 少量摘要；
- 必要的路由约束。

Router 默认不需要看到完整检索证据，否则会增加成本，还可能让它提前被文档内容
影响路线判断。

### 5.3 Rewrite 看什么

Query Rewrite 的职责是把当前问题改造成更适合检索的查询。因此它需要：

- 当前问题；
- 最近几轮对话；
- 会话摘要；
- 如果发生历史恢复，则加入 `relevant_history`。

Rewrite 不负责最终回答，也不应该接收大量无关文档正文。

### 5.4 Answer 看什么

Answer 需要根据证据回答，所以它的 Pack 重点是：

- 当前问题；
- 摘要和必要的历史；
- 当前检索得到的 `EvidenceItem`；
- 只读工具结果；
- 引用编号和引用约束。

Answer 的证据会被格式化成：

```text
[1] sample_supplier_management_policy.pdf | doc_id=13, chunk_id=81
采购复核的触发条件是单次采购金额超过二十万元。

[2] sample_supplier_management_policy.pdf | doc_id=13, chunk_id=83
...
```

模型只能引用当前 Pack 中实际保留的编号。若某条证据因预算被整体省略，后续
`documents_for_context()` 不会继续把它映射成可引用证据，避免出现引用编号错位。

## 6. 上下文预算是怎么分配的

当前构造顺序是：

```text
1. system_instructions
2. pinned_constraints
3. conversation_summary
4. persistent_memory
5. recent_messages
6. relevant_history
7. evidence_items 或旧版 retrieval_context
8. tool_result_refs 或旧版 tool_results
```

每一步都从当前 Pack 的剩余预算中扣除 Token。最终会生成：

```text
BudgetBreakdown
  total
  system_instructions
  pinned_constraints
  summary
  persistent_memory
  recent_messages
  relevant_history
  evidence
  tools
  used
  remaining
```

### 6.1 滑动窗口解决什么问题

最近消息窗口是最低成本的基础保护：

```text
历史消息：M1 M2 M3 M4 M5 M6 M7 M8 M9 M10
Answer 默认保留：                 M5 M6 M7 M8 M9 M10
```

它能保证用户刚刚说过的内容还在，但它有明显缺点：更早的关键决定可能被淘汰。
所以滑动窗口不是完整的记忆方案，只是默认保底方案。

### 6.2 重要内容为什么不会被截半

以下内容按原子单元处理：

- 一个 `EvidenceItem`，通常是一整个 chunk；
- 一个 `relevant_history` 消息；
- 一个结构化工具结果；
- 一个 pinned constraint。

如果剩余预算只能放下 100 Token，而某个证据需要 300 Token，当前实现不会拿前
100 Token，而是：

```text
selected_items  不加入该证据
omitted_items   记录该证据和省略原因
truncated       true
```

旧版的裸字符串 `retrieval_context` 和 `tool_results` 为了兼容历史调用，仍可以
使用文本裁剪。因此新代码应优先传结构化 `evidence_items` 和 `tool_result_refs`。

### 6.3 `persistent_memory` 当前是什么状态

`ContextPack` 的 `persistent_memory` 已经接入会话级长期记忆表。当前实现只保存
用户明确声明的少量事实、决定、偏好或约束，不自动把普通聊天写成长期记忆。

对应代码：

- `backend/app/db/models.py::ConversationMemory`
- `backend/app/services/memory_service.py`
- `backend/alembic/versions/20260806_c1d7a4e9b2f0_conversation_memories.py`

数据库记录包含：

```text
conversation_id / organization_id / user_id
memory_type / content / source_message_id
importance / status
```

支持的明确指令包括：

```text
记住：所有搜索必须按 organization_id 过滤。
以后都按 PostgreSQL + Elasticsearch 的方案实现。
最终决定使用 RabbitMQ + Celery。
```

当前实际使用的长期信息来源有两种：

```text
Conversation.context_summary       -> 较早历史的整体摘要
conversation_memories             -> 少量明确的重要事实/决定/约束
```

读取出的数据库记录会转换为 `ContextItem(kind="persistent_memory")`，再通过独立
预算加入 Pack。`status=archived` 的记忆不会再次进入 Pack。

## 7. 结构化会话摘要

### 7.1 摘要由谁生成

摘要不是简单用 Python 拼接，而是使用 Router 的 LLM 配置生成。调用位置是：

`backend/app/services/context_manager.py::summarize_conversation_with_llm`

模型收到的要求是：

```text
只总结输入对话中已经出现的事实、已确认决定、未解决问题和关键实体。
禁止添加外部知识。
只输出固定 JSON。
```

输出结构固定为：

```json
{
  "facts": [],
  "decisions": [],
  "open_questions": [],
  "entities": []
}
```

后端再补充版本、来源消息 ID、摘要游标和生成时间。

### 7.2 什么时候触发摘要

保存助手消息后，`chat.py` 会尝试调用：

```text
maybe_update_conversation_summary(conversation_id, session)
```

当前默认条件：

```text
整个会话内容达到 context_summary_trigger_chars = 12000 字符
并且存在尚未摘要的新消息
首次摘要：满足总长度即可
后续摘要：新增内容至少达到 context_summary_min_new_chars = 3000 字符
```

系统保留最近 6 条消息不放入摘要，让近期原文继续直接进入 ContextPack。

### 7.3 摘要游标为什么重要

假设历史为：

```text
M1 M2 M3 M4 M5 M6 M7 M8 M9 M10
```

系统每次保留最近 6 条消息，因此第一次摘要的输入可能是：

```text
M1 M2 M3 M4
```

正确的游标是：

```text
context_summary_through_message_id = M4
```

不能错误地把游标写成 M10，因为 M5 到 M10 只是近期窗口，并没有进入摘要。否则
下一次摘要会误以为 M5 到 M10 已经被总结过，造成历史事实丢失。

### 7.4 摘要失败怎么办

以下情况都不会阻塞已经完成的问答：

- LLM 未配置；
- 请求超时；
- HTTP 调用失败；
- 返回内容不是合法 JSON；
- JSON 字段不符合预期结构。

失败时：

```text
不覆盖旧摘要
继续使用最近消息窗口
记录 llm_context_summary 指标
```

这是一种“摘要是增强能力，不能成为主流程单点故障”的设计。

长期记忆和摘要失败互不阻塞：已经写入 `conversation_memories` 的明确记忆仍然可以
加载；摘要失败只影响较早历史的概括，不会删除或覆盖长期记忆。

## 8. 上下文缺口检测

文件：

`backend/app/services/context_gap_detector.py`

### 8.1 为什么不每轮都让 LLM 判断

如果每一轮都额外调用一个 LLM 判断“是否需要找历史”，会带来：

- 额外延迟；
- 额外费用；
- 结果不稳定；
- 模型可能无意义地重复查询历史。

所以第一版使用可解释的确定性规则，只有识别到缺口时才恢复历史。

### 8.2 当前规则

会检查这些信号：

```text
之前说过、上次提到、刚才说的、前面提到
它、这个、那个、上述、这个条件
过短的续问，例如“然后呢？”、“怎么改？”
以“那、然后、继续、还要、为什么、怎么”开头的续问
```

关键判断是：

```text
当前问题出现历史引用信号
且当前 ContextPack 中没有足够的摘要/近期消息主体
-> need_recovery = true
```

显式历史请求，例如“之前说过的采购复核条件是什么”，即使当前还有一部分上下文，
也会触发一次历史查询，因为用户明确要求回看历史。

### 8.3 缺口检测不是答案相关性判断

这两个概念不能混淆：

```text
context_gap_detector
  判断当前问题是否缺少会话历史主体

relevance_check
  判断当前检索证据是否足够支持答案
```

前者解决“我不知道这个它指什么”，后者解决“检索结果和问题相关吗”。

## 9. 会话历史只读工具

文件：

- `backend/app/agent_tools/conversation_tools.py`
- `backend/app/agent_tools/schemas.py`
- `backend/app/agent_tools/registry.py`
- `backend/app/agent_tools/authorization.py`

工具名：

```text
search_conversation_history
```

### 9.1 工具参数为什么没有 `conversation_id`

工具参数只有：

```json
{
  "query": "之前说过的采购复核条件",
  "limit": 5
}
```

模型不能自己传入任意 `conversation_id`。会话 ID、组织 ID、知识库 ID 和用户 ID
由后端从可信的 GraphState / 当前认证主体注入。这样可以避免模型通过修改参数搜索
其他用户或其他组织的会话。

### 9.2 工具内部的授权边界

执行前会同时校验：

```text
conversation.id == 当前会话
conversation.organization_id == 当前组织
conversation.knowledge_base_id == 当前知识库
conversation.created_by_user_id == 当前用户
当前角色拥有 chat 权限
```

工具只读取当前会话最近最多 200 条消息，然后通过确定性规则打分：

- 整个 query 出现在消息中：加较高分；
- 关键词、中文 n-gram、数字或编号命中：加分；
- 时间较新的消息：在同等命中情况下略优。

第一版没有给历史消息单独建立向量索引，也没有搜索整个组织的全部会话。

### 9.3 为什么工具结果还要再次进入 Context Manager

工具查到的内容不能直接无限拼进 prompt。正确链路是：

```text
工具执行
  -> ToolExecutionResult
  -> history_tool_results
  -> relevant_history / ToolResultRef
  -> Context Manager 预算裁剪
  -> Rewrite / Answer
```

工具本身负责权限和数据范围，Context Manager 负责长度和内容预算。这是两个不同
的保护层。

## 10. LangGraph 中的完整链路

RAG 路线现在是：

```text
START
  -> router
  -> context_gap_check
  -> history_recovery
  -> query_rewrite
  -> retrieve
  -> tool_decision
  -> tool_call
  -> relevance_check
       ├── 证据足够 -> answer -> END
       └── 证据不足 -> human_review -> answer/rejected
```

### 10.1 `context_gap_check`

读取当前问题和已有的 Router/Rewrite/Answer ContextPack，调用
`detect_context_gap()`，写入：

```python
state["context_gap"] = {
    "need_recovery": True,
    "reason": "用户明确要求引用之前的对话，需要查询当前会话历史。",
    "missing_terms": ["之前"],
    "triggers": ["explicit_history_reference"],
}
```

### 10.2 `history_recovery`

如果没有缺口，节点只记录“不需要恢复”，不查询数据库。

如果有缺口：

1. 构造 `search_conversation_history` 工具调用；
2. 通过统一 registry 做权限、参数和审计；
3. 把结果写入 `history_tool_results`；
4. 转成 `relevant_history`；
5. 把相关历史注入三个 ContextPack；
6. 记录 `context_recovery_actions` 和指标；
7. 标记 `history_recovery_used = true`。

当前每轮最多执行一次历史恢复，避免工具无限循环。

### 10.3 `query_rewrite`

Query Rewrite 会读取已经恢复的 `relevant_history`，把它们作为 `history` 内容
加入自己的上下文。这样“这个金额”这类问题有机会恢复出真实主体，再生成更明确的
检索 query。

### 10.4 `answer`

Answer 使用 `build_answer_context()` 重新构造 Answer Pack。这个步骤很重要：
检索完成后，Answer 的证据已经确定，不能继续沿用检索前那个没有证据的 Pack。

最终答案模型只收到当前 Pack 中的证据编号。模型返回的 `used_context_numbers`
再映射为真实的 `doc_id / chunk_id / knowledge_item_id`。

## 11. 一个完整例子：从历史恢复到最终回答

下面用采购制度对话说明整个链路。假设当前知识库里有文档
`sample_supplier_management_policy.pdf`。

### 11.1 第一轮问题

用户输入：

```text
采购复核的触发条件是什么？
```

流程：

```text
1. API 将问题写入 messages 表。
2. Router 判断为 rag。
3. context_gap_check：没有历史指代，直接 not_needed。
4. history_recovery：不查询。
5. query_rewrite：原问题本身已经足够明确。
6. retrieve：Dense + BM25 + RRF + rerank 找到采购制度 chunk。
7. relevance_check：证据分数和内容支持性通过。
8. answer：基于证据生成答案并记录 citations。
9. assistant 答案写回 messages 表。
```

检索证据可能是：

```text
[1] sample_supplier_management_policy.pdf | chunk_id=81
若单次采购金额超过二十万元，需要由采购委员会复核。
```

助手消息会保存答案和引用元数据，后续前端历史回显时仍然可以看到引用。

### 11.2 第二轮引用之前的内容

用户输入：

```text
之前说过的采购复核条件，具体是谁审批？
```

这次可能因为历史较长，当前 Pack 没有保留第一轮完整消息；或者用户明确要求
“之前说过”。处理过程如下：

```text
router
  -> context_gap_check
       triggers = [explicit_history_reference]
       need_recovery = true
  -> history_recovery
       调用 search_conversation_history
       query = "之前说过的采购复核条件，具体是谁审批？"
```

工具只在当前会话中查找，可能返回：

```json
{
  "message_id": 101,
  "role": "user",
  "content": "采购复核的触发条件是什么？",
  "score": 3.75,
  "matched_terms": ["采购复核", "条件"]
}
```

然后 GraphState 变成：

```text
context_gap.need_recovery = true
history_recovery_used = true
relevant_history = [M101, M102]
context_recovery_actions = [
  {
    action: "search_conversation_history",
    success: true,
    source_ids: ["101", "102"]
  }
]
```

Rewrite 看到恢复的历史后，可以把问题改成更明确的查询：

```text
采购制度中，单次采购金额超过二十万元时，采购委员会的复核和审批流程是什么？
```

之后继续走：

```text
改写 query
  -> Dense/BM25 检索
  -> RRF 融合
  -> rerank
  -> relevance gate
  -> Answer
```

### 11.3 预算不足时的行为

假设检索得到 5 个 chunk，但 Answer 预算只能容纳前 3 个完整证据：

```text
evidence_items = [chunk-81, chunk-83, chunk-75]
omitted_items = [chunk-79, chunk-76]
truncated = true
```

Answer 只能引用 `[1]`、`[2]`、`[3]`。被省略的 chunk 不会继续作为可引用对象返回，
这样不会产生“模型引用了前端没有展示的第 4 条证据”的编号错乱。

### 11.4 摘要触发后的行为

当对话累计超过 12000 字符时，保存助手消息后会尝试生成摘要：

```text
messages 表完整保留
       |
       +--> 最近 6 条继续作为原文窗口
       |
       +--> 更早的新增消息交给 Router LLM 摘要
                  |
                  +--> JSON 写入 Conversation.context_summary
                  +--> 更新 context_summary_through_message_id
```

下次请求时，Context Manager 同时使用：

```text
结构化摘要 + 最近 6 条原文 + 必要时恢复的相关历史
```

所以不是“只剩摘要”，而是摘要、窗口和按需恢复三层组合。

### 11.5 重要消息不等于摘要：长期记忆的行为

用户发送：

```text
M5：记住：所有搜索必须按 organization_id 过滤。
```

保存用户消息时会同步提取并写入：

```text
conversation_memories
  content = 所有搜索必须按 organization_id 过滤
  source_message_id = M5
  memory_type = constraint
  status = active
```

之后即使滑动窗口从：

```text
M5 M6 M7 M8 M9 M10
```

移动到：

```text
M6 M7 M8 M9 M10 M11
```

新的 Answer Pack 仍然可以是：

```text
persistent_memory:
  所有搜索必须按 organization_id 过滤

recent_messages:
  M6 M7 M8 M9 M10 M11
```

这就是 `persistent_memory` 补上滑动窗口缺口的地方。

## 12. 和 Checkpoint / Message 的关系

这三个东西职责不同：

| 对象                             | 保存什么                 | 用途                       |
| -------------------------------- | ------------------------ | -------------------------- |
| `messages`                     | 用户可见的完整业务对话   | 历史列表和消息回显         |
| `Conversation.context_summary` | 压缩后的跨轮事实         | 长对话时降低输入长度       |
| LangGraph checkpoint             | 某次工作流运行状态       | interrupt/resume、重启恢复 |
| `ContextPack`                  | 某个节点本次要看到的内容 | 控制 LLM 输入              |

可以这样理解：

```text
messages       = 完整录像
context_summary = 录像的结构化索引
checkpoint     = 工作流暂停时的存档点
ContextPack    = 这一幕实际给演员看的剧本
```

ContextPack 可以进入 GraphState 和 checkpoint，但它不能替代 messages；checkpoint
也不能替代用户可见的消息记录。

## 13. 安全和失败降级

### 13.1 权限边界

历史恢复工具必须同时经过：

```text
工具注册检查
  -> 参数 Pydantic 校验
  -> 角色权限检查
  -> 当前组织/知识库范围检查
  -> conversation 所有者检查
  -> SQL 条件过滤
  -> 工具审计日志
```

模型不能通过参数自行扩大查询范围。

### 13.2 历史工具失败

如果历史查询失败：

```text
history_recovery_used = true
context_recovery_actions.success = false
relevant_history = []
继续使用摘要和近期消息
不把数据库异常细节暴露给模型
```

它不会因为一次历史恢复失败而直接导致整个问答服务崩溃。

### 13.3 摘要失败

摘要失败不会覆盖旧摘要，也不会阻塞主流程。系统仍然可以使用最近消息窗口。

### 13.4 指标

当前会记录低基数指标，例如：

```text
context_pack_total{purpose, truncated}
context_pack_omitted_items_total{purpose}
context_recovery_total{outcome}
llm_context_summary_duration_seconds{outcome}
```

不会把完整问题、完整 token、密码或 API Key 写入指标标签。

## 14. 如何学习和阅读代码

建议按下面顺序阅读：

1. `backend/app/services/context_types.py`
   - 先理解每种上下文对象代表什么。
2. `backend/app/services/context_budget.py`
   - 看预算如何按 Router、Rewrite、Answer 区分。
3. `backend/app/services/context_manager.py`
   - 重点看 `build_context_pack()` 和 `_select_atomic()`。
4. `backend/app/services/context_gap_detector.py`
   - 看规则如何判断是否缺少历史主体。
5. `backend/app/services/memory_service.py`
   - 看明确记忆指令如何提取、去重、读取和归档。
6. `backend/app/agent_tools/conversation_tools.py`
   - 看历史查询如何限制在当前会话和当前用户。
7. `backend/app/graph/nodes.py`
   - 重点看 `context_gap_check_node()`、`history_recovery_node()` 和 `answer_node()`。
8. `backend/app/graph/langgraph_workflow.py`
   - 看节点如何连接成 `context_gap_check -> history_recovery -> query_rewrite`。
9. `backend/app/api/chat.py`
   - 看消息保存、明确记忆提取和摘要更新的时机。
10. `backend/tests/test_context_manager.py`、`test_context_gap_detector.py`、
    `test_context_recovery_nodes.py`、`test_memory_service.py`
   - 用测试理解边界，而不是只看 happy path。

## 15. 推荐练习

### 练习一：观察原子证据省略

在测试中构造 3 个较长的 `EvidenceItem`，把 Answer 预算调小，观察：

```text
selected evidence
omitted_items
budget.used
truncated
```

确认不会出现半个 chunk。

### 练习二：观察摘要游标

构造 10 条消息，设置保留最近 6 条，检查摘要输入只覆盖前面的消息，并确认：

```text
context_summary_through_message_id
```

不会错误地指向最近窗口中的消息。

### 练习三：观察历史恢复

先写入一轮包含“采购复核”的历史，再提出：

```text
之前说过的采购复核条件是什么？
```

检查 GraphState 中是否出现：

```text
context_gap.need_recovery = true
history_recovery_used = true
relevant_history 非空
```

再测试另一个用户的 conversation_id，确认工具不会返回越权历史。

## 16. 验证命令

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend

./.venv/bin/python -m unittest \
  tests.test_context_manager \
  tests.test_context_gap_detector \
  tests.test_context_recovery_nodes \
  tests.test_memory_service

./.venv/bin/python -m unittest \
  tests.test_readonly_tools \
  tests.test_tool_authorization

./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

当前全量后端测试结果：

```text
Ran 225 tests in the latest full run
OK (skipped=5)
```

## 17. 当前边界和后续方向

本阶段已经完成的是“可控上下文构建、一次历史恢复和最小会话级长期记忆”，不是
完整的长期记忆平台。

当前明确边界：

- Token 仍是字符估算，不是具体模型 tokenizer 的精确计算；
- 历史消息相关性使用 PostgreSQL 查询加确定性关键词/时间排序；
- 没有用户级或组织级全局记忆表，当前只支持会话级记忆；
- 只识别用户明确的记忆指令，不自动判断普通消息是否重要；
- 记忆冲突的复杂裁决和人工审核界面尚未实现；
- 每轮最多自动恢复一次历史，不支持无限工具循环；
- 摘要没有人工编辑、审核和版本回滚界面；
- 上下文恢复失败时不会自动扩大到其他会话或整个组织；
- 大规模历史搜索后续可以增加 PostgreSQL FTS 或 Elasticsearch 索引；
- 后续可以接模型 tokenizer、摘要质量评测和上下文命中率评测。

最终可以用一句话概括本阶段：

> 完整历史由数据库负责保存，摘要负责压缩，滑动窗口负责保底，历史工具负责按需
> 找回，Context Manager 负责预算和质量，LangGraph 负责把这些步骤编排成可恢复的
> 工作流。
