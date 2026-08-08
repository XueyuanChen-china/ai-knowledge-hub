# 只读 Tool Calling：权限、预算、审计与评估

## 1. 这一阶段解决什么问题

Phase 3 建立了知识库只读工具，但“工具已经注册”不等于“工具可以无条件执行”。Phase 4 为工具调用补上四个企业系统必须有的控制点：

```text
工具白名单
  -> 角色权限判断
  -> 参数校验
  -> 组织 / 知识库范围校验
  -> 执行
  -> 审计和指标
```

当前仍然只允许读取，不增加删除、修改、任意 SQL、Shell 或 HTTP 工具。

## 2. 权限矩阵怎么工作

文件：`backend/app/agent_tools/authorization.py`

工具权限复用现有 RBAC 权限：

| 工具 | 所需权限 |
| --- | --- |
| `search_knowledge_base` | `search:use` |
| `get_document` | `content:read` |
| `get_knowledge_item` | `content:read` |
| `get_chunk_neighbors` | `content:read` |
| `list_knowledge_base_documents` | `content:read` |

权限判断只回答“这个角色能不能使用该类能力”。真正查哪条文档，仍由工具 handler 在 SQL 中再次带上：

```text
organization_id = 当前用户组织
knowledge_base_id = 当前会话知识库
```

因此权限分两层：

1. **能力权限**：viewer 能搜索和读取，不能调用未注册的写工具。
2. **资源范围**：即使知道 document_id，也只能读取当前组织和知识库的数据。

两层不能互相替代。只有角色判断没有资源过滤，会发生越权读取；只有资源过滤没有角色判断，会让不该使用能力的角色获得能力。

## 3. 调用预算为什么需要存在

当前每轮对话最多执行一个工具调用，配置项是：

```env
AGENT_TOOL_MAX_CALLS_PER_TURN=1
```

这是后端硬限制，不相信模型自己“只调用一次”。它可以避免模型循环调用导致成本失控，也避免一次请求读取过多文档挤占回答上下文。

后续如果需要连续工具链，也应该改成有限状态机和总预算，而不是开放一个无限 `while tool_call`。

## 4. 参数校验和权限校验的顺序

统一入口在 `backend/app/agent_tools/registry.py`：

```text
工具名不在白名单 -> unknown_tool
角色没有工具权限 -> forbidden
参数不符合 Pydantic schema -> invalid_arguments
通过后才调用 handler
```

非法参数不会调用 `session.exec()`。工具异常会转成结构化 `execution_error` 或业务错误，不把 SQL、文件路径和第三方 SDK 异常直接交给模型。

## 5. 审计记录保存什么

文件：`backend/app/agent_tools/audit.py`

复用已有 `security_audit_logs` 表，不新增第二套审计表。每次工具调用都会记录工具名、是否允许、需要的权限、结果状态、组织、用户、会话、request_id、trace_id、耗时和错误码。

查询内容不会原样写入审计：

```json
{
  "arguments": {
    "query": "<omitted>",
    "top_k": 5
  }
}
```

这样既能回答“谁在什么时候调用了什么工具”，又不会把用户问题、密钥或工具正文变成日志泄露源。审计写入失败不会改变问答主链路，但应通过日志和告警发现。

## 6. 运行指标

工具调用复用现有低基数 metrics：

```text
ai_knowledge_hub_operations_total{operation="agent_tool",outcome="success"}
ai_knowledge_hub_operations_total{operation="agent_tool",outcome="forbidden"}
ai_knowledge_hub_operations_total{operation="agent_tool",outcome="invalid_arguments"}
```

原始 query 和具体内容不放进 metrics label。具体明细去查审计日志，统计趋势看 metrics。

## 7. 离线评估测什么

文件：`backend/app/agent_tools/evaluation.py`

当前评估的是确定性 planner 的工具选择准确率，而不是把真实 LLM 输出当作唯一标准：

```text
问题 -> planner -> 实际工具
实际工具 == expected_tool ?
```

样本在 `backend/tests/fixtures/tool_evaluation/cases.json` 中版本化，覆盖完整文档、相邻上下文、文档列表、知识条目详情和普通 RAG 问题。

后续接入模型原生 tool calling 后，可以复用同一套样本，再增加参数合法率、越权拒绝率、工具成功率、降级成功率、P50/P95 工具耗时和引用覆盖率。

## 8. 如何验证

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/python -m unittest \
  tests.test_tool_authorization \
  tests.test_tool_evaluation \
  tests.test_readonly_tools
```

重点观察：viewer 可以使用只读工具；未注册写工具返回 `unknown_tool`；未知角色返回 `forbidden` 且不查询数据库；工具调用写入 `security_audit_logs`；审计中不会出现原始 query；单轮超过预算返回 `tool_call_limit`。

## 9. 当前边界

- 指标仍是单进程内存聚合，多副本集中采集留给后续运维阶段；
- 当前 planner 是确定性规则，不是模型原生 tool selection；
- 当前只允许一个工具调用，不支持多步工具计划；
- 组织级资源过滤已经存在，但更细粒度的项目、部门和文档密级策略还没有实现；
- 审计失败不会阻塞回答，生产环境需要配套告警。
