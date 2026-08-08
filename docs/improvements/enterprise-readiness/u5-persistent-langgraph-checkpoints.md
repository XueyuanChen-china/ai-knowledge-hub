# U5：持久化 LangGraph Checkpoint

## 为什么 InMemorySaver 不够

内存 checkpointer 只活在当前 Python 进程里：

```text
用户问题
  -> retrieve
  -> human_review interrupt
  -> 进程重启
  -> 内存清空
  -> 原 thread_id 无法 resume
```

多 Uvicorn Worker 或多台 API 实例时，问题更明显：发起请求的 Worker A 保存了内存状态，审核请求恰好被负载均衡到 Worker B，B 看不到 A 的内存。

U5 改为 `langgraph-checkpoint-postgres`，checkpoint 写 PostgreSQL，因此图实例、API 进程和 Worker 不再是状态边界。

## 两类持久化数据

```text
messages / conversations
  用户看得见的业务记录

LangGraph checkpoints
  图执行状态、下一节点、interrupt 位置、恢复所需配置
```

它们不能互相替代：从 `messages` 无法准确还原图停在哪个节点；只保留 checkpoint 又无法作为用户聊天记录展示。

## 代码链路

```text
FastAPI lifespan
  -> initialize_graph_checkpointer()
  -> Psycopg ConnectionPool
  -> PostgresSaver
  -> build_checkpointed_workflow()
  -> builder.compile(checkpointer=...)

human_review interrupt
  -> checkpoint 写 PostgreSQL

新的 graph 实例 / API 进程
  -> graph.get_state(thread_id)
  -> Command(resume=...)
```

[checkpointer.py](../../../backend/app/graph/checkpointer.py) 是连接池生命周期的唯一入口。它使用独立 pool，不复用 SQLModel 请求 Session：图执行可以持续较久，不能把 HTTP 请求事务或业务连接一直占住。

## 为什么不把第三方表写进 Alembic

业务表仍由 Alembic 管理。`langgraph-checkpoint-postgres` 的表属于第三方库实现细节，由它的 `PostgresSaver.setup()` 创建。

因此采用显式运维命令：

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/python scripts/setup_langgraph_checkpoints.py
```

应用启动只连接 pool，**不会**自动执行 setup 或 DDL。这样多副本部署时不会出现所有 API 实例同时改表的风险。

## thread_id 为什么还要绑定业务会话

resume 时不能只相信客户端传来的 `thread_id`。代码同时确认：

- `Conversation.thread_id`；
- `Conversation.organization_id`；
- conversation 创建人或组织审核者权限；
- checkpoint state 中的 `thread_id`、`organization_id`、`conversation_id`。

任何一项不匹配都按 checkpoint 不存在处理，防止把另一组织或另一会话的暂停状态恢复到当前会话。

## SSE 的一个细节

当前 SSE 为了让回答逐步显示，会在 `answer` 节点前使用 `interrupt_before=["answer"]`，然后切换为答案事件重放。这个“技术暂停”和 `human_review_node` 的真实人工审核 interrupt 都会产生 LangGraph interrupt 信号。

区分规则是：

```text
snapshot.interrupts 且 need_human_review=true
  -> 人工审核中断

其他 answer 前暂停
  -> 内部 SSE 边界，继续生成 answer 事件
```

否则正常问题会被错误展示成“等待人工审核”。

## 推荐阅读顺序

1. [langgraph_workflow.py](../../../backend/app/graph/langgraph_workflow.py)：图如何挂 checkpointer。
2. [checkpointer.py](../../../backend/app/graph/checkpointer.py)：Psycopg pool、setup 和关闭逻辑。
3. [chat.py](../../../backend/app/api/chat.py)：resume 前如何校验 conversation 与 checkpoint。
4. [test_graph_checkpoint_persistence.py](../../../backend/tests/test_graph_checkpoint_persistence.py)：重建 graph 实例仍能恢复的最小证明。

## 当前边界

- 同步 `PostgresSaver` 与当前同步 FastAPI 图调用匹配。
- SSE 真正的模型 token 流、异步 `AsyncPostgresSaver` 和长时间任务隔离仍是后续独立决策。
- 多人同时点击 resume 的分布式互斥可继续加强为 ReviewTask 条件更新或数据库锁。
