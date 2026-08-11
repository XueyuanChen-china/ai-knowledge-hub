# Chat Checkpoint Recovery Runbook

## 初始化

LangGraph checkpoint 使用 `langgraph-checkpoint-postgres` 的第三方表，不由 Alembic 创建。

在部署新版本、且数据库尚未初始化 checkpoint 表时，执行一次：

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/python scripts/setup_langgraph_checkpoints.py
```

该命令可重复执行。应用启动不会自动执行它。

## 配置

默认 checkpoint 和业务 PostgreSQL 使用同一数据库，但连接池独立：

```env
# 留空时回退到 DATABASE_URL
GRAPH_CHECKPOINT_DATABASE_URL=
GRAPH_CHECKPOINT_POOL_MIN_SIZE=1
GRAPH_CHECKPOINT_POOL_MAX_SIZE=4
GRAPH_CHECKPOINT_POOL_TIMEOUT_SECONDS=10
```

若需要隔离 checkpoint 的数据库，可单独填写 `GRAPH_CHECKPOINT_DATABASE_URL`，格式为标准 Psycopg/PostgreSQL URL，例如：

```text
postgresql://user:password@host:5432/ai_knowledge_hub
```

## PostgresSaver 负责什么

`PostgresSaver` 是 LangGraph 的 PostgreSQL checkpoint 适配器，不是一个新的数据库服务。
默认情况下，它和业务表使用同一个 PostgreSQL 数据库，只是使用独立的 Psycopg 连接池。

它主要负责：

1. 把 LangGraph 每个线程的工作流状态保存到 PostgreSQL。
2. 记录当前状态对应的 `thread_id`、checkpoint 版本和父 checkpoint。
3. 保存节点执行过程中产生的 channel 数据和写入记录。
4. 通过同一个 `thread_id` 读取最近的状态快照。
5. 在 `interrupt` 后支持使用 `Command(resume=...)` 从暂停位置继续。

执行一次 `setup_graph_checkpoint_schema()` 后，第三方包会创建这些表：

```text
checkpoint_migrations  checkpoint 表结构的版本记录
checkpoints            thread 的状态快照和下一步执行信息
checkpoint_blobs       较大的 channel 状态数据
checkpoint_writes      节点写入记录
```

这些表不是业务表，不由 Alembic 管理；业务表仍然由 Alembic 管理。应用启动只建立连接池，
不会在每个 API 或 Worker 启动时自动执行 `setup()`。

`PostgresSaver` 不负责：

- 创建 PostgreSQL 数据库或 Docker 容器；
- 保存用户可见的聊天消息；
- 保存 `Conversation`、`Message`、`ReviewTask` 等业务记录；
- 判断用户是否有权访问某个会话；
- 代替业务 API 执行人工审核权限校验。

因此，checkpoint 和聊天业务记录必须分开理解：checkpoint 用于恢复工作流，业务表用于展示、审计和权限控制。

## 恢复流程

1. 用户发起 `/api/chat`，图在人工审核节点中断。
2. PostgreSQL checkpoint 保存当前 state 和 next node。
3. API 重启或请求切到另一 API Worker 后，`POST /api/review/resume` 使用同一 `thread_id` 读取 checkpoint。
4. 后端先验证 conversation 组织、所有者/审核权限，再验证 checkpoint 中的 thread、组织和 conversation ID。
5. 通过后执行 `Command(resume=...)`。

## 排查

`Graph checkpoint not found`：检查 `thread_id`、会话组织，以及 checkpoint 表是否已执行 setup。

`No pending interrupt for this thread`：该线程已恢复完成，或不是处于人工审核暂停状态。重复 resume 不会再次写 assistant message。

不要手工删除 checkpoint 表中的单行记录来“取消审核”。应通过业务 API 处理 review task；checkpoint 记录和 conversation/message 记录含义不同。
