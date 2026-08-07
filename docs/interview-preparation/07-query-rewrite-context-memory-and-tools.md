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

## 三、ContextPack

完整聊天记录存在 PostgreSQL，但一次 LLM 请求只使用受预算控制的 ContextPack：

```text
system_instructions
pinned_constraints
persistent_memory
conversation_summary
recent_messages
relevant_history
evidence_items
tool_result_refs
budget / omitted_items / truncated
```

`ContextItem` 是 Pack 内一个可整体加入或移除的普通单元，不能装整个 Pack。`EvidenceItem` 保持完整 chunk；`ToolResultRef` 保存工具摘要和来源 ID，超长原文不必全部塞入 prompt。

## 四、滑动窗口、摘要和裁剪如何并存

### 滑动窗口

每次构建 Pack 都执行，只选择最近 N 条消息，不调用模型、不修改历史。

### LLM 结构化摘要

历史超过阈值时受控触发，把窗口外旧消息压缩为事实、决定、未决问题、实体和来源 message ID。它不是每轮都生成。

### 预算裁剪

每次调用模型前执行。即使摘要和最近消息都存在，总 token 仍可能超预算，此时按优先级移除低价值完整单元，并记录 `omitted_items`。

例子：

```text
M1-M4 -> 已进入结构化摘要
M5     -> 重要决定，进入 persistent_memory
M6-M11 -> 最近消息窗口
当前证据 -> evidence_items，优先级最高
```

当 M12 到来时，窗口可能变成 M7-M12，但 M5 由持久记忆保留；M1-M4 的核心结论由摘要保留。

## 五、摘要失败怎么办

LLM 未配置、超时或 JSON 非法时：

- 不覆盖旧摘要；
- 仍使用最近消息、持久记忆和当前证据；
- 继续执行预算裁剪；
- 记录指标和日志。

摘要是增强能力，不能成为回答主流程的单点故障。

## 六、持久记忆与历史恢复

第一版只保存明确、高价值、可解释的信息，例如“记住”“以后都按”“最终决定”。规则可识别候选，但写入要保留来源 message ID、创建人、时间和删除能力。

当问题包含指代且当前 Pack 无法找到主体时：

```text
context_gap_check
  -> history_recovery
  -> search_conversation_history
  -> relevant_history
  -> rebuild ContextPack
  -> Query Rewrite
```

每轮最多恢复一次，只能查当前用户有权限的当前会话，结果仍受预算控制。

## 七、Qwen 原生 Tool Calling

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

## 八、为什么工具判断有时在检索后

普通 RAG 问题先检索，工具选择器可使用候选中的真实 ID，避免模型凭空编参数。明确引用上一轮结果的工具路线可由 Router 直接进入 tool decision，不必重复检索。

```text
具体知识问题 -> retrieve -> optional tool enrichment
“展开刚才文档” -> tool route -> use previous citations
```

当前实现是受限工具调用，不开放无限 ReAct 循环。这样减少延迟、成本、不可预测行为和权限风险。

## 九、关键代码

- [Query Rewrite](../../backend/app/services/query_rewrite_service.py)
- [Context Manager](../../backend/app/services/context_manager.py)
- [预算](../../backend/app/services/context_budget.py)
- [Context 类型](../../backend/app/services/context_types.py)
- [缺口判断](../../backend/app/services/context_gap_detector.py)
- [会话记忆](../../backend/app/services/memory_service.py)
- [工具注册与执行](../../backend/app/agent_tools/registry.py)
- [知识工具](../../backend/app/agent_tools/knowledge_tools.py)
- [上下文详细学习文档](../improvements/phase-2-context-management.md)

