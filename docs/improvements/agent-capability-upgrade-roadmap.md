# Agent 能力增强路线图

## 定位

当前项目已经具备：

```text
意图路由
  -> 混合检索
  -> RRF
  -> rerank
  -> relevance gate
  -> Answer
  -> 引用 / 人工审核
```

本阶段不引入多 Agent，也不重写现有 LangGraph。目标是把当前的 RAG 工作流升级为更完整、可控的企业知识 Agent。

四个阶段：

1. Query Rewrite：改善复杂、口语化和上下文依赖问题的召回。
2. Context Management：控制会话历史、检索上下文和模型 Token 消耗。
3. Read-only Tool Calling：让模型通过受控工具访问知识库数据。
4. Tool Evaluation and Authorization：评估工具选择质量，并建立后端权限边界。

---

## 总体目标

用户问题进入系统后，目标链路为：

```text
用户问题
  -> router
  -> query rewrite
  -> dense + BM25 + RRF + rerank
  -> context manager
  -> relevance gate
  -> answer 或 read-only tool
  -> citations / human review
```

需要保持的工程约束：

- 原始问题永远保留，改写问题只能作为补充。
- 所有检索和工具调用都必须继承 organization、knowledge_base 和用户权限。
- 工具权限由后端校验，不能依赖 LLM 自己判断。
- Context Management 不能删除业务消息；它只决定本次发送给模型的上下文。
- 每个阶段都要有确定性 fake 测试，不能把真实 LLM 输出作为唯一测试依据。

---

## Phase 1：Query Rewrite

### 目标

将口语化、指代不清、上下文依赖的问题转换为更适合检索的查询，同时保留原始问题。

### 推荐链路

```text
原始问题
  -> 判断是否需要改写
  -> 生成 1~3 个检索查询
  -> 原始 query + rewrite queries 并行检索
  -> RRF 去重融合
  -> rerank
```

### 需要支持的场景

- “这个流程多久完成？”结合上一轮对话改写为“差旅报销流程需要多久完成？”
- “采购复核是什么条件？”保留“采购复核”等精确术语。
- 数字、金额、制度编号、产品名不得在改写时丢失。

### 实现边界

- 原始 query 必须参与检索。
- 改写失败、超时或返回非法内容时，直接退回原始 query。
- 精确编号、金额和专有名词优先保留，不使用改写结果替换原问题。
- 第一版不做复杂多轮 query planning。

### 预计代码

- `backend/app/services/query_rewrite_service.py`
- `backend/app/graph/state.py`
- `backend/app/graph/nodes.py`
- `backend/app/services/retrieval_service.py`
- `backend/app/config.py`
- `backend/tests/test_query_rewrite.py`

### 验收

- 改写问题能够保留原始问题中的关键实体。
- 原始 query 和改写 query 都能进入检索融合。
- 改写服务故障时不影响原有检索。
- 对精确术语问题，Query Rewrite 不降低 Recall@5 和 MRR。

---

## Phase 2：Context Management

> 已完成第一版增强：结构化 ContextPack、节点级预算、原子证据选择、结构化会话摘要、上下文缺口检测和当前会话历史恢复已经接入。真实 tokenizer、语义级历史排序和长期记忆表仍是后续增强。

### 目标

控制发送给 LLM 的上下文大小和内容质量，避免历史消息无限增长、检索结果重复以及 Token 超限。

### 借鉴 Claude Code 的思想

这里只借鉴通用的上下文工程思想，不复制 Claude Code 的实现：

1. **上下文预算**：为系统指令、历史对话、检索证据和回答预留独立 Token 预算。
2. **滑动窗口**：优先保留最近几轮对话，淘汰过旧且与当前问题无关的消息。
3. **摘要压缩**：历史过长时生成会话摘要，摘要替代旧消息进入后续请求。
4. **工具输出裁剪**：工具返回结果超过预算时，只保留结构化摘要、关键字段和引用 ID。
5. **按需加载**：不把所有知识库内容预先放进上下文，只在检索或工具调用后加载相关内容。
6. **失败可恢复**：上下文压缩失败时退回最近消息 + 当前检索结果，不阻塞问答。

### 推荐上下文结构

```text
System Instructions
  + 当前用户问题
  + 会话摘要
  + 最近 N 轮消息
  + 当前检索证据
  + 工具调用结果
  + 引用约束
```

### 不同内容的优先级

```text
当前问题和直接证据       最高
工具调用结果和引用         高
最近几轮用户对话           中高
会话摘要                   中
更早的原始消息             低
重复或超长工具输出         最低
```

### 实现边界

- PostgreSQL 中的 `messages` 是完整业务历史，不能被物理删除。
- 新增上下文构建层，只生成本次 LLM 请求的 context pack。
- 第一版可以使用字符估算 Token，后续再接模型 tokenizer。
- 摘要内容要记录版本和生成时间，避免摘要不可追踪。
- citations 必须来自当前 context pack，不能引用已被裁剪的上下文。

### 预计代码

- `backend/app/services/context_manager.py`
- `backend/app/services/context_types.py`
- `backend/app/services/context_budget.py`
- `backend/app/services/context_gap_detector.py`
- `backend/app/graph/state.py`
- `backend/app/graph/nodes.py`
- `backend/app/api/chat.py`
- `backend/tests/test_context_manager.py`
- `backend/tests/test_context_gap_detector.py`
- `backend/tests/test_context_recovery_nodes.py`

### 验收

- 短对话保持原有回答行为。
- 长对话不会超过配置的上下文预算。
- 旧消息仍然可以通过历史接口查看。
- 上下文压缩后仍保留当前问题、关键证据和引用。
- 同一个会话重启后可以继续使用摘要和历史消息。
- 指代续问只在上下文缺口时查询当前会话历史。
- 历史查询工具不能跨组织、跨用户或跨会话读取消息。

---

## Phase 3：只读 Tool Calling

> 已完成：只读工具 registry、参数校验、组织/知识库边界、Qwen 原生 Tool Calling、工具结果 Context Pack 和不重复检索的 follow-up tool 路由已经接入。详细实现说明见 [Phase 3 学习文档](./phase-3-readonly-tool-calling.md)。

### 目标

让 Agent 在需要时调用知识库只读工具，而不是把所有能力硬编码在 Answer Node 中。

### 第一批工具

```text
search_knowledge_base
get_document
get_knowledge_item
get_chunk_neighbors
list_knowledge_base_documents
```

### 调用链路

```text
用户问题
  -> Router
  -> direct / rag / tool / complex
  -> Qwen 原生 tool_calls
  -> 后端校验参数和权限
  -> 执行工具
  -> 工具结果进入 Context Manager
  -> Answer
```

### 第一版不做

- 创建、修改、删除知识库数据。
- 任意 SQL 工具。
- 任意 HTTP 请求工具。
- Shell、文件系统和代码执行工具。
- 自动连续调用无限工具。

### 预计代码

- `backend/app/agent_tools/registry.py`
- `backend/app/agent_tools/knowledge_tools.py`
- `backend/app/agent_tools/conversation_tools.py`
- `backend/app/agent_tools/schemas.py`
- `backend/app/graph/nodes.py`
- `backend/app/graph/state.py`
- `backend/tests/test_readonly_tools.py`

### 验收

- 需要详细文档内容的问题可以调用 `get_document`。
- 需要相邻上下文的问题可以调用 `get_chunk_neighbors`。
- 工具参数非法时返回结构化错误，不执行数据库查询。
- 工具调用结果能进入最终 context 和 citations。
- 工具调用失败时可以退回普通 RAG 流程。

---

## Phase 4：工具调用评估和权限控制

> 第一版已完成：只读工具权限矩阵、单轮调用预算、工具调用审计、低基数运行指标和离线 planner 评估已经接入。真实 LLM 原生 tool call 选择评测、跨副本集中式指标和更细粒度资源策略留在后续增强。

### 目标

保证 Agent 选择工具的行为可评估，同时防止模型生成越权工具调用。

### 权限模型

工具权限由后端维护：

```python
TOOL_PERMISSIONS = {
    "search_knowledge_base": PERMISSION_SEARCH,
    "get_document": PERMISSION_CONTENT_READ,
    "get_knowledge_item": PERMISSION_CONTENT_READ,
    "get_chunk_neighbors": PERMISSION_CONTENT_READ,
    "list_knowledge_base_documents": PERMISSION_CONTENT_READ,
    "search_conversation_history": PERMISSION_CHAT,
}
```

每次工具调用都必须检查：

- 当前用户身份和角色；
- organization_id；
- knowledge_base_id；
- 资源是否属于当前组织；
- 工具参数是否越界；
- 当前会话是否有权访问该资源。

### 评估集

增加以下样本：

- 应该直接回答的问题；
- 应该检索的问题；
- 应该调用工具的问题；
- 不应该调用工具的问题；
- 工具参数缺失或非法的问题；
- 跨组织越权的问题；
- 工具失败后的降级问题。

### 评估指标

- tool selection accuracy：工具选择是否正确；
- argument validity：参数是否符合 schema；
- unauthorized call rejection：越权调用拒绝率；
- tool success rate：工具执行成功率；
- fallback success rate：工具失败后降级成功率；
- tool call latency：工具调用延迟；
- citation coverage：最终引用是否来自工具结果。

### 审计

记录：

- conversation_id；
- user_id；
- tool_name；
- sanitized arguments；
- allow / deny；
- failure reason；
- request_id / trace_id；
- duration。

禁止记录密码、Token、OSS 签名 URL 和完整敏感正文。

### 预计代码

- `backend/app/agent_tools/authorization.py`
- `backend/app/agent_tools/audit.py`
- `backend/app/agent_tools/evaluation.py`
- `backend/app/graph/nodes.py`
- `backend/tests/test_tool_authorization.py`
- `backend/tests/test_tool_evaluation.py`
- `backend/tests/fixtures/tool_evaluation/cases.json`
- `docs/operations/tool-calling-security.md`

### 验收

- viewer 可以搜索和读取授权资源，但不能调用写工具。
- 跨组织 document、knowledge item 和 chunk 访问全部拒绝。
- 模型生成的错误工具参数不会直接进入数据库查询。
- 工具失败不会导致整条会话状态损坏。
- 工具调用和拒绝行为可通过审计日志追踪。

---

## 实施顺序和停止线

推荐顺序：

```text
先完成 U8 检索基线
  -> Phase 1 Query Rewrite
  -> Phase 2 Context Management
  -> Phase 3 Read-only Tools
  -> Phase 4 Tool Evaluation + Authorization
```

每个阶段完成后都要执行：

```text
单元测试
  -> 检索回归测试
  -> 权限负向测试
  -> 延迟和 Token 观察
```

停止线：

- 不在本阶段引入多 Agent；
- 不增加写操作工具；
- 不接入任意代码执行、Shell 或网络访问工具；
- 不为了展示 Agent 数量而拆分现有 LangGraph；
- 如果 Query Rewrite 使 Recall@5 或 MRR 下降，则默认关闭并保留实验结果。

## 当前项目的最终能力定位

完成后可以准确描述为：

> 基于 LangGraph 构建的企业知识 Agent，具备意图路由、查询改写、混合检索、rerank、上下文预算管理、只读知识库工具调用、引用追踪、权限校验和人工审核能力。

这比直接声称“多 Agent 平台”更符合当前系统的真实边界，也更容易在面试中解释每个模块的职责和取舍。
