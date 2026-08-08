# Phase 3：只读 Tool Calling

## 状态

已完成第一版，状态：`implemented`。

这一阶段不是给 Agent 开放任意代码执行能力，而是建立一个很小、可审计的知识库工具边界：
Agent 只能在当前组织和知识库范围内读取资料。

## 解决的问题

普通 RAG 只会把初次召回的 chunks 交给 Answer Node。当用户提出下面的问题时，单次召回不一定够用：

- “把这份文档的详细内容说一下。”
- “补充这个片段的上一段和下一段。”
- “这个知识库里有哪些文档？”
- “这个知识条目的完整内容是什么？”

如果把这些逻辑全部写进 Answer Node，Answer Node 会同时负责判断意图、查库、控制权限和生成回答，职责会越来越重。本阶段把读取能力拆成注册工具。

## 当前链路

```text
用户问题
  -> Router
  -> direct / rag / tool / complex
  -> rag: Query Rewrite -> Dense + BM25 + RRF + rerank
  -> tool_decision
  -> tool_call
  -> Context Manager
  -> relevance_check（初次 RAG）
  -> Answer 或 human review
```

当前有两种工具入口：

```text
初次 RAG 问题
  -> 先检索
  -> 只有出现工具意图时才请求 Qwen 原生 tool_calls

续问：“展开刚才那个文档”
  -> Router 选择 tool
  -> 从上一轮 assistant message 的 citations 取得资源 ID
  -> 直接调用工具，不重复 Dense/BM25 检索
```

Qwen 正常配置时，`tool_decision` 使用 OpenAI 兼容的原生协议：

```text
tools=[...]
tool_choice="auto"
  -> assistant.tool_calls
  -> ToolCallRequest
  -> registry 参数校验和权限校验
```

如果模型未配置、接口失败或没有返回工具调用，才使用确定性规则兜底：

```text
“完整原文 / 详细内容” -> get_document
“上一段 / 下一段 / 相邻上下文” -> get_chunk_neighbors
“有哪些文档 / 文档列表” -> list_knowledge_base_documents
“知识条目详情” -> get_knowledge_item
其它问题 -> 不调用工具，继续普通 RAG
```

这样既支持模型自主选择工具，又保留了离线测试和服务降级能力。模型只负责提出工具名和参数，不能绕过 registry；工具真正执行前仍必须经过参数、组织、知识库和角色权限校验。

`direct` 表示无需知识库和工具的直接回答；基于上一轮引用的“展开原文”“查看前后文”等问题属于 `tool`，不应该误归类成 `direct`。

## 工具边界

当前注册了 6 个工具：

| 工具                              | 用途                          | 关键限制                                 |
| --------------------------------- | ----------------------------- | ---------------------------------------- |
| `search_knowledge_base`         | 当前知识库内混合检索          | `top_k` 限制为 1~10                    |
| `get_document`                  | 读取文档提取文本              | 只按当前组织和知识库查询，并限制文本长度 |
| `get_knowledge_item`            | 读取知识条目                  | 只按当前组织和知识库查询                 |
| `get_chunk_neighbors`           | 读取同一知识条目的相邻 chunks | `radius` 限制为 1~3                    |
| `list_knowledge_base_documents` | 列出当前知识库的文档          | `limit` 限制为 1~50                    |
| `search_conversation_history`   | 当前会话内恢复历史上下文      | 只能查询当前用户的当前会话               |

明确不注册：写操作、删除操作、任意 SQL、任意 HTTP、Shell、文件系统和代码执行。

## 代码怎么分工

### `schemas.py`

定义工具调用协议和参数约束：

```text
ToolCallRequest
  name
  arguments
  reason

ToolExecutionResult
  tool_name
  ok
  data
  citations
  error_code
  error_message
```

模型输出或前端输入都不能直接进入数据库查询，必须先转换成这些受限模型。

### `registry.py`

这是工具的“白名单和总入口”：

1. 根据工具名查注册表。
2. 用对应的 Pydantic 参数模型校验参数。
3. 未知工具返回 `unknown_tool`。
4. 参数不合法返回 `invalid_arguments`，不会调用 `session.exec()`。
5. 执行异常只返回通用 `execution_error`，不把 SQL、路径或 SDK 错误泄露给模型。

因此 Agent 即使生成了：

```json
{"name":"run_sql","arguments":{"sql":"select * from users"}}
```

也只会得到结构化拒绝，不会执行。

`build_openai_tool_definitions()` 会把 Pydantic 参数模型转换为 Qwen 所需的：

```json
{
  "type": "function",
  "function": {
    "name": "get_chunk_neighbors",
    "description": "读取相邻文本切片",
    "parameters": {"type": "object", "properties": {}}
  }
}
```

### `knowledge_tools.py`

这里是真正的 SQLModel 查询，但每个查询都显式带上：

```text
organization_id = 当前登录用户组织
knowledge_base_id = 当前会话知识库
```

工具不相信模型传入的组织 ID，也不返回 `file_path`、presigned URL 或密钥。跨组织资源统一表现为 `not_found`，避免通过错误信息泄露资源存在性。

### `nodes.py`

新增两个图节点：

```text
tool_decision
  -> 选择最多一个只读工具，写入 tool_call

tool_call
  -> registry 校验并执行
  -> 写入 tool_results / tool_citations / tool_error
```

`tool_decision_node` 只在问题包含明显工具意图，或 Router 已选择 `tool` 路线时调用
Qwen，避免普通 RAG 问题平白增加一次模型请求。上一轮引用通过 assistant message 的
`metadata_json.citations` 恢复，只传资源 ID、标题和分数，不重复加载完整正文。

当前不支持工具递归调用，也没有 `while tool_call`，因此不会因为模型输出而无限执行。

### Context Manager

工具结果不会直接拼进 Answer prompt，而是先转成受控文本，再通过已有 `context_manager.build_answer_context()` 进入 `ContextPack`。因此仍然受到工具预算和原子结果选择限制，超长结果会被整体省略并记录到 `omitted_items`。

PostgreSQL 的 `messages` 仍保存完整业务对话；工具结果只是本次工作流状态和本次 LLM 请求的上下文，不替代历史消息。

## 引用处理

工具结果自带 `tool_citations`：

```text
get_document -> doc_id 级引用
get_knowledge_item -> knowledge_item_id 级引用
get_chunk_neighbors -> 每个相邻 chunk 的引用
```

Answer 生成后的普通检索 citations 与工具 citations 会按 `doc_id / chunk_id / knowledge_item_id` 去重合并。这样即使工具结果不是初始 top-k，也不会完全丢失来源信息。

## 失败降级

初次 RAG 中工具调用失败时：

```text
tool_error / ToolExecutionResult(ok=false)
  -> 写入工作流状态
  -> 不抛出破坏整个问答的异常
  -> 继续 relevance_check 和普通 Answer 流程
```

如果是 `tool` follow-up 路线且无法解析工具目标，则进入人工审核，不偷偷重新做一次
全量检索，避免用户明确要求的“上一轮资源”被错误替换成新的候选。

这意味着“详细文档工具暂时不可用”不会让所有普通 RAG 问题一起不可用，但 Answer 上下文会知道工具失败，模型不能假装已经拿到了完整文档。

## 测试重点

测试文件：[test_readonly_tools.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_readonly_tools.py)

覆盖：

- 正常读取文档并生成引用。
- 跨组织读取被拒绝。
- 非法参数在数据库查询前被拦截。
- 未注册的 `run_sql` 被结构化拒绝。
- 相邻 chunk 按 `chunk_index` 有序返回。
- planner 能选择文档详情和相邻上下文工具。
- 能解析 Qwen 原生 `tool_calls`，并转换成内部 `ToolCallRequest`。
- “展开刚才文档”路线不会再次调用检索服务。
- 工具节点把结果写入 `tool_results` 和 `tool_citations`。

## 当前边界

本阶段完成的是“受控只读工具执行框架 + Qwen 原生 Tool Calling 接入”，仍不是完整的 Agent 平台。后续 Phase 4 再做：

- 工具选择准确率评测。
- 角色到工具的权限矩阵。
- 工具调用次数、耗时和失败指标。
- 模型原生 tool call 的离线准确率和降级率评测。
- 多工具顺序调用，但仍需设置最大调用次数和超时。
