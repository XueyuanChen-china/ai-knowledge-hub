# 备份与恢复

## 备份范围

PostgreSQL 是业务事实来源，至少备份：

- organizations、users、memberships；
- knowledge_bases、documents、knowledge_items、chunks；
- upload_tasks、upload_parts、processing jobs；
- conversations、messages、review_tasks 和 LangGraph checkpoint 表。

OSS 保存原始文件，Elasticsearch 保存可重建的检索索引，RabbitMQ 保存短期消息，不把 RabbitMQ 当作长期备份。

## 开发环境备份

```bash
docker compose exec -T postgres pg_dump \
  -U postgres -d ai_knowledge_hub \
  --format=custom > backup-$(date +%Y%m%d-%H%M%S).dump
```

恢复到空数据库：

```bash
cat backup-YYYYMMDD-HHMMSS.dump | docker compose exec -T postgres \
  pg_restore -U postgres -d ai_knowledge_hub --clean --if-exists
```

生产环境需要由平台提供加密、定期、异地备份；本地 compose 命令不等同于生产备份策略。

## 索引恢复

Elasticsearch 索引是派生数据。恢复顺序是：

```text
恢复 PostgreSQL
  -> 确认 documents / chunks / vector_id
  -> 创建版本化 ES index
  -> 从 chunks 重新 embedding/index
  -> 验证检索后再切换 alias
```

OSS 原文件不可用时，不能仅凭 chunk 恢复原始文件；因此生产必须同时保留 OSS 生命周期策略和对象级备份。

## Chat checkpoint 恢复

checkpoint 和 Message 分开处理。恢复后先用原 `thread_id` 读取 checkpoint，确认 conversation 所属组织和用户，再执行 resume。重复 resume 必须幂等或被拒绝，不能重复写 assistant message。

## 常见故障

- migration 失败：停止 backend/worker，修复 migration 后再 `alembic upgrade head`。
- worker 中断：恢复 RabbitMQ/worker，检查 pending/running/lease 过期的 stage job。
- ES 丢失：不直接删除 PostgreSQL chunks，按版本索引重建。
- OSS multipart 残留：使用过期任务清理接口或对象存储生命周期规则清理。
