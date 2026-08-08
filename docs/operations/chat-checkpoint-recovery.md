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
