# 10 关键代码走读

面试通常不会要求逐行背代码，但会通过代码追问验证你是否真的理解项目。下面入口至少要能顺着讲出调用链。

## 1. FastAPI 入口与中间件

文件：[backend/app/main.py](../../backend/app/main.py)

重点：lifespan、Router、CORS、request context、异常日志，以及应用启动为什么不再用 `create_all` 偷偷改表。

```text
请求进入 -> request ID -> CORS -> route dependency -> service -> response
```

## 2. 当前用户与权限

文件：

- [dependencies.py](../../backend/app/security/dependencies.py)
- [policies.py](../../backend/app/security/policies.py)
- [resource_access.py](../../backend/app/security/resource_access.py)

```python
principal = decode_and_validate_token(...)
require_permission(principal, "document:read")
document = query_by_id_and_organization(document_id, principal.organization_id)
```

前两步不替代第三步。角色有读权限，不表示可以读其他组织资源。

## 3. OSS object key 和 multipart

文件：[upload_service.py](../../backend/app/services/upload_service.py)

```python
return (
    f"{prefix}/{organization_id}/{knowledge_base_id}/{upload_id}/"
    f"source.{extension}"
)
```

需要解释用户文件名为何不参与路径、part size 如何计算 total parts、presign 为什么有 TTL 和状态限制。

## 4. Celery 阶段任务

文件：

- [upload_tasks.py](../../backend/app/tasks/upload_tasks.py)
- [upload_postprocess_service.py](../../backend/app/services/upload_postprocess_service.py)

```text
task(job_id) -> claim/check -> running -> execute stage
-> completed -> create next-stage job -> publish next task
```

失败时区分可重试和永久错误，记录 attempt、错误和下次运行时间。

## 5. 文档切分 Pipeline

文件：

- [splitter.py](../../backend/app/services/document_splitter/splitter.py)
- [chunk_assembler.py](../../backend/app/services/document_splitter/chunk_assembler.py)

必须能解释 `DocumentElement != Section != Block != ChunkData`，以及 flush 条件、句子兜底、表头保留和 semantic overlap。

## 6. Elasticsearch 两路召回

文件：[vector_service.py](../../backend/app/services/vector_service.py)

重点函数：mapping/alias、bulk 写入、dense kNN、BM25、权限 filter、metadata 容错和 embedding dimension 校验。

## 7. RRF 与 Reranker

文件：

- [retrieval_service.py](../../backend/app/services/retrieval_service.py)
- [reranker.py](../../backend/app/services/retrieval/reranker.py)

```python
contribution = 1.0 / (rrf_k + rank)
rrf_score = existing_score + contribution
```

两个来源命中同一 vector ID 时更新同一个候选，保留原分，再取融合 top_n 给 reranker。

`ranked_candidates + hits[candidate_count:]` 中，前半段是已精排候选，后半段是未进入精排预算的剩余 RRF 候选，最终调用方再截 `top_k`。

## 8. LangGraph 图

文件：[langgraph_workflow.py](../../backend/app/graph/langgraph_workflow.py)

必须能指出 `StateGraph`、node、conditional edge、四条 Router 路线、relevance gate、`interrupt`、`Command(resume=...)` 和 `compile(checkpointer=...)`。

## 9. ContextPack 与工具执行

文件：

- [context_types.py](../../backend/app/services/context_types.py)
- [context_manager.py](../../backend/app/services/context_manager.py)
- [registry.py](../../backend/app/agent_tools/registry.py)

Tool Calling 的关键不只是把函数描述发给 Qwen，而是模型返回后仍执行：

```text
tool whitelist -> Pydantic schema -> authorization
-> organization scoped handler -> audit -> bounded context result
```

## 10. Chat SSE

文件：

- [chat.py](../../backend/app/api/chat.py)
- [sse.ts](../../frontend/lib/api/sse.ts)
- [chat/page.tsx](../../frontend/app/chat/page.tsx)

后端编码多行 SSE 时每行都带 `data:`，空行结束事件。前端保留未完成 buffer，只处理完整事件，并分别更新节点、回答、引用和审核状态。

## 11. 数据库迁移和启动顺序

文件：

- [start_api.sh](../../backend/scripts/start_api.sh)
- [start_worker.sh](../../backend/scripts/start_worker.sh)
- [database.py](../../backend/app/db/database.py)

```text
API: wait dependencies -> alembic upgrade -> checkpoint setup -> uvicorn
Worker: wait dependencies -> check revision -> celery
```

Worker 不做 migration，避免扩容多个副本时并发 DDL。

## 12. 面试前代码检查表

你应该能在不看文档的情况下定位：

- 新增一个 API 要改 Router、Schema、Service 和哪些测试；
- 一个上传从 init 到 indexed 的所有表和状态；
- 一个问题从 Router 到 citations 的节点顺序；
- 一个跨组织搜索请求在哪几层被拦截；
- 一个 chunk 如何追溯到文件页码或 Excel 行；
- 一个 Celery 任务失败如何重试和诊断；
- 一个审核会话在 FastAPI 重启后如何 resume。

## 13. 建议现场画的三张图

```text
上传：Browser -> Presign -> OSS -> Complete -> MQ -> Celery -> PG/ES

检索：Query -> Rewrite -> Dense + BM25 -> RRF -> Rerank -> Gate -> Answer

权限：JWT Principal -> RBAC -> PostgreSQL org scope -> ES filter -> OSS ownership
```

这三张图能覆盖项目大部分深挖问题。
