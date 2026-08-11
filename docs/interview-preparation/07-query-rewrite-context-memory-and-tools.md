# 07 Query Rewrite、上下文、记忆与 Tool Calling

## 一、为什么 Agent 不只是 RAG

RAG 解决“从知识库找证据并回答”，但连续对话还会出现：

- “这个制度的原文展开看看”；
- “刚才那个 chunk 前后还有什么”；
- “我之前最终决定用哪个方案”；
- 长会话超出模型上下文；
- 用户表达不完整，需要补全检索 query。

因此系统增加 Query Rewrite、Context Manager、持久记忆和受控只读工具。

## 二、Query Rewrite

```text
当前问题 + 必要会话上下文
  -> 规则判断是否值得改写
  -> Qwen 生成 1~3 个检索变体
  -> 原始 query 与变体分别 Dense/BM25
  -> RRF 去重融合
  -> 仍以原始问题 rerank
```

不需要改写的例子：完整、明确、包含制度编号或文件名的短问题。可能需要改写的例子：包含“它、这个、刚才那个”，或口语表达过于省略。

规则先行减少延迟、成本和偏移风险；复杂指代和同义扩写再交给 LLM。原问题始终参与检索，变体不能改变权限过滤条件。

## 三、Context Management：解决什么问题

`messages` 表保存完整、可回看的业务历史；但不能把全部历史、全部检索结果和全部工具输出直接发给模型。这样会带来 token 超限、延迟增加、旧信息干扰当前问题，以及引用来源不清晰的问题。

因此每次 LLM 调用前，后端从 PostgreSQL 历史、会话摘要、长期记忆、检索结果和工具结果中，临时组装一个受预算控制的 `ContextPack`。Pack 只是本次请求的输入快照，不会删除或改写 `messages`。

```text
PostgreSQL messages（完整历史）
        + Conversation.context_summary（旧历史摘要）
        + conversation_memories（明确长期记忆）
        + 当前检索 / 工具结果
                    |
                    v
             Context Manager
                    |
                    v
      Router Pack / Rewrite Pack / Answer Pack
```

### ContextPack 的字段

```text
system_instructions    系统约束
persistent_memory      用户明确确认的长期规则、偏好、决定
conversation_summary   较早对话的结构化摘要
recent_messages        最近原文消息窗口
relevant_history       需要时从本会话找回的少量历史
evidence_items         当前检索到的完整 chunk
tool_result_refs       工具结果的摘要、来源 ID、错误信息
budget                 各区域实际使用的估算 Token
omitted_items          因预算未进入 Pack 的内容及原因
truncated              是否发生了任何裁剪
```

`ContextItem` 是 Pack 内可整体加入或移除的普通单元，例如一条长期记忆或历史恢复结果；它不是整个 Pack。`EvidenceItem` 保持完整 chunk，避免把证据切成半段；`ToolResultRef` 默认只放摘要和来源 ID，避免工具原文撑爆 prompt。

## 四、三个 LLM 节点如何复用上下文

三者共享同一份业务历史和摘要，但分别通过 `purpose=router | rewrite | answer` 使用不同预算和字段范围。这叫**上下文隔离**：不是创建三份会话，而是避免低成本节点被无关证据淹没。

| 节点          | 主要目标                    | 主要上下文                                                   | 默认总预算 |
| ------------- | --------------------------- | ------------------------------------------------------------ | ---------- |
| Router        | 判断`direct / rag / tool` | 当前问题、摘要、最近 2 条消息、上一轮引用 ID                 | 800        |
| Query Rewrite | 补全指代、生成检索变体      | 当前问题、最近 12 条消息（约 6 轮）；必要时附加恢复历史      | 1600       |
| Answer        | 基于证据生成答案和引用      | 长期记忆、摘要、最近 12 条、恢复历史、完整检索证据、工具结果 | 6000       |

例如用户说“把刚才那个制度的第 3 条展开”：Router 主要看最近对话和上一轮 citation，决定走工具；Rewrite 只有在问题主体不完整时才使用历史；Answer 才需要看工具返回的正文和可引用证据。

## 五、滑动窗口、摘要、长期记忆与裁剪如何配合

这四者职责不同，不能互相替代。

### 1. 滑动窗口：每轮都执行

每次构造 Pack 时，从完整历史中只选择最近消息。它不调用 LLM，也不修改数据库。

```text
完整消息：M1 M2 M3 M4 M5 M6 M7 M8 M9 M10 M11 M12
近期窗口：                  M7 M8 M9 M10 M11 M12
```

近期窗口保证模型能自然理解刚刚发生的上下文。

### 2. 结构化摘要：压缩较早历史

窗口外的消息不会立即丢弃。系统把较早且未摘要的消息交给摘要 LLM，压缩为：

```json
{
  "facts": ["采购复核金额超过二十万元"],
  "decisions": ["最终使用 PostgreSQL 保存 checkpoint"],
  "open_questions": ["是否引入 query rewrite"],
  "entities": ["采购复核", "PostgresSaver"]
}
```

摘要还保存来源消息 ID、摘要游标和生成时间。`context_summary_through_message_id` 只指向真正进入摘要的最后一条消息，不能把近期窗口误标为已摘要。

### 3. Persistent Memory：只保存明确、长期有效的信息

第一版只识别明确指令：`记住：`、`以后都按`、`最终决定`、`不要再改`。这类内容写入 `conversation_memories`，带来源 `message_id`，并在 Answer Pack 中优先于普通摘要进入预算。

它解决的是“重要决定早于窗口、又不应只依赖 LLM 摘要”的问题。例如 M5 的“最终决定使用 PostgreSQL”即使不在近期窗口，也能持续进入 Pack。它不是自动记住所有聊天内容，避免误记忆和事实污染。

### 4. 预算裁剪：模型调用前的最终保护

即使已有摘要，当前检索证据或工具结果仍可能让 Pack 超限。Context Manager 会按优先级选择完整单元，并在 `omitted_items` 记录没有放入的内容。`ContextPack.max_tokens` 是最终硬上限。

当前优先顺序为：

```text
system_instructions
-> persistent_memory
-> conversation_summary
-> recent_messages
-> relevant_history
-> evidence_items
-> tool_result_refs
```

实际是否能放下还取决于节点用途。Answer 的证据预算最大；Router 根本不装检索证据和工具原文。

## 六、软硬水位：什么时候生成摘要

系统最多保留最近 12 条原文（约 6 轮），其中摘要策略优先保留最近 4 条（约 2 轮），窗口外较早消息按批次摘要。历史区域由摘要、近期消息、相关历史和长期记忆组成；检索证据与工具结果不属于历史区域。摘要触发只计算“窗口外且尚未被摘要游标覆盖”的消息，并与历史区域预算比较，不看全部会话字符数。

```text
软水位：本次 Answer 可见历史区域达到 6000 估算 Token 的 70%
      -> 至少有 4 条、100 Token 的旧消息可压缩时，
         本轮回答保存后调用 LLM 生成摘要
      -> 从最早未摘要消息开始动态选择，尽量回落到 60%

Pack 硬水位：当前 Pack 预计达到对应节点 max_tokens 的 95%
      -> 执行一次完整维护，目标回落到约 75%

最终硬边界：Router / Rewrite / Answer 各自的 ContextPack.max_tokens
      -> 即使摘要调用失败，也按预算裁剪，不会超出模型输入边界
```

消息条数只决定“有没有足够内容值得压缩”，不再单独触发摘要。短消息很多但实际占用低时，
系统继续使用滑动窗口；窗口外内容仍保存在 PostgreSQL，需要时由历史恢复补回。

完整维护顺序是：先在回答保存后压缩较早对话并推进摘要游标；下一次 Pack 构建时使用新摘要，
并依次去重工具结果、淘汰已回答或较旧工具结果、减少 `relevant_history`、裁剪低分证据，
最后才允许裁剪近期消息和长期记忆。Pack 构建阶段不会临时同步调用摘要 LLM。

例子：M1-M8 是较早历史，M9-M12 是最近 2 轮原文。如果摘要、最近窗口、恢复历史和长期记忆
合计达到 70%，系统从 M1 开始选择足以让占用回落到约 60% 的一批消息。假设选到 M8，摘要
游标就指向 M8；下一次 Pack 只放摘要和 M9-M12，不会同时重复放 M1-M8 原文。

## 七、摘要失败和历史缺口恢复

摘要 LLM 未配置、超时或返回非法 JSON 时：

- 不覆盖旧摘要；
- 继续使用近期窗口、长期记忆和当前检索证据；
- ContextPack 仍执行最终预算裁剪；
- 记录日志与 `llm_context_summary` 指标。

因此摘要是增强能力，不是回答主流程的单点故障。

如果用户问“它的金额是多少”“之前说过的方案呢”，但当前窗口和摘要都找不到主体，会走受控历史恢复：

```text
context_gap_check
  -> search_conversation_history（仅当前会话、当前用户权限范围）
  -> relevant_history
  -> 重建 ContextPack
  -> Query Rewrite / 检索
```

每轮最多恢复一次，恢复结果仍受 `relevant_history` 的独立预算控制。它是窗口和摘要之外的按需兜底，不会把整段历史重新塞给模型。

## 八、Qwen 原生 Tool Calling

后端把工具注册表转换为 OpenAI-compatible `tools`。模型返回结构化 `tool_calls`，随后后端执行：

```text
工具名白名单
  -> Pydantic 参数校验
  -> 权限校验
  -> 资源组织/知识库校验
  -> handler
  -> 审计记录
  -> ContextPack ToolResultRef
```

模型只能提出调用，不能绕过后端直接访问数据库。

当前只读工具：

- `search_knowledge_base`；
- `get_document`；
- `get_knowledge_item`；
- `get_chunk_neighbors`；
- `list_knowledge_base_documents`；
- `search_conversation_history`。

`radius=2` 表示以目标 chunk 为中心取前后最多 2 个相邻 chunk，不是几何半径。

## 九、为什么工具判断有时在检索后

普通 RAG 问题先检索，工具选择器可使用候选中的真实 ID，避免模型凭空编参数。明确引用上一轮结果的工具路线可由 Router 直接进入 tool decision，不必重复检索。

```text
具体知识问题 -> retrieve -> optional tool enrichment
“展开刚才文档” -> tool route -> use previous citations
```

当前实现是受限工具调用，不开放无限 ReAct 循环。这样减少延迟、成本、不可预测行为和权限风险。

## 十、关键代码

- [Query Rewrite](../../backend/app/services/query_rewrite_service.py)
- [Context Manager](../../backend/app/services/context_manager.py)
- [预算](../../backend/app/services/context_budget.py)
- [Context 类型](../../backend/app/services/context_types.py)
- [缺口判断](../../backend/app/services/context_gap_detector.py)
- [会话记忆](../../backend/app/services/memory_service.py)
- [工具注册与执行](../../backend/app/agent_tools/registry.py)
- [知识工具](../../backend/app/agent_tools/knowledge_tools.py)
- [上下文详细学习文档](../improvements/phase-2-context-management.md)

## 十一、当前上下文压缩策略

本项目把 Answer 原文窗口定义为最多 6 轮，而不是 6 条消息。压缩时保护最近 2 轮原文，
再从更早且尚未摘要的消息中动态选择摘要批次；批次不是固定 4 轮。完整消息仍然保存在
PostgreSQL，不会物理删除。

```text
T1 T2 T3 T4 T5 T6
动态摘要候选    保留两轮
```

Pack 使用滞回水位：达到当前 Pack `max_tokens` 的 95% 时触发维护，目标回落到约
75%，避免每新增一轮就重复压缩。维护顺序是：去重、淘汰旧工具结果、将较早会话交给
摘要 LLM 压缩、减少相关历史；仍超限时才裁剪低分 evidence，最后才缩减近期消息和长期记忆。

工具结果采用“完整结果存档、上下文只放引用”的策略：

```text
工具执行
  -> conversation_tool_results 保存完整结果
  -> ContextPack 放 summary + source_ids + result_ref
  -> 回答完成后标记 used_in_answer / citation_used
  -> 后续优先淘汰已回答的旧工具结果
```

普通工具调用不额外调用 LLM 生成摘要，先使用工具类型对应的确定性投影；只有用户
要求总结超长文档，或结果需要跨轮保留语义结论时，才使用摘要模型。`result_ref` 使
被淘汰的工具结果可以按需恢复。
