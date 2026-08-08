# U4：跨存储授权边界

## 这阶段解决什么问题

U3 已经能识别“谁在请求”和“这个角色是否允许某个动作”，但只做 RBAC 还不够。
如果某个接口只按 `document_id` 或 `conversation_id` 查询，组织 A 的用户仍可能通过猜测 ID 读到组织 B 的资源。

U4 将授权边界贯穿到四个实际存储和业务位置：

```text
JWT Principal
  -> PostgreSQL 资源行
  -> Elasticsearch 检索过滤
  -> OSS 上传任务与对象路径
  -> Chat 会话和审核恢复
```

## 资源归属模型

以下资源增加了 `organization_id`。除 `chunks` 外，还增加了 `created_by_user_id`：

- `knowledge_bases`
- `documents`
- `knowledge_items`
- `chunks`
- `conversations`
- `upload_tasks`

这两个字段回答不同的问题：

- `organization_id`：这条资源属于哪个租户，决定跨组织数据隔离。
- `created_by_user_id`：谁创建了它，决定会话私有访问、审计和后续归属追踪。

迁移文件是 [20260727_f3a8d9e45c10_resource_ownership_backfill.py](../../../backend/alembic/versions/20260727_f3a8d9e45c10_resource_ownership_backfill.py)。它按“先可空、回填、再加外键和非空约束”的顺序执行，避免存量行在中间状态违反约束。

## PostgreSQL：先按组织查询，再谈角色

[resource_access.py](../../../backend/app/security/resource_access.py) 是资源读取的统一入口。例如读取知识库不再等价于：

```python
session.get(KnowledgeBase, knowledge_base_id)
```

而是等价于：

```python
select(KnowledgeBase).where(
    KnowledgeBase.id == knowledge_base_id,
    KnowledgeBase.organization_id == principal.organization_id,
)
```

找不到时返回 404，而不是“存在但你没有权限”的 403。这样不会泄露另一个组织是否拥有某个 ID，称为避免资源枚举。

角色权限仍由 U3 的 `require_permission(...)` 负责：

```text
先检查角色是否能做该动作
  -> 再确认资源属于当前组织
  -> 对会话等私有资源再确认创建人或管理员身份
```

## Elasticsearch：权限过滤必须在 ES 内执行

向量召回不能先取 top-k 再在 Python 里丢弃无权结果，否则：

- 高分无权 chunk 会挤掉真正可见的 chunk；
- 日志或调试数据可能已经泄露内容；
- 最终召回数量可能不足。

所以 [vector_service.py](../../../backend/app/services/vector_service.py) 写入 ES 时保存顶层字段：

```text
organization_id
knowledge_base_id
```

kNN 查询在 ES 内使用两个 `term` filter。只有同时匹配当前组织和知识库的向量才有资格进入 top-k。

### 为什么采用 version + alias

U4 的新 mapping 比旧索引多了组织字段。直接原地改旧索引并不可靠，因此实际索引名分为：

```text
具体写入索引：knowledge_chunks_v2_{knowledge_base_id}
查询 alias：   knowledge_chunks_{knowledge_base_id}_active
```

存量重建脚本 [reindex_resource_ownership.py](../../../backend/scripts/reindex_resource_ownership.py) 先把某个知识库的所有 chunk 写入 `v2`，全部成功后才原子切换 alias。旧索引不会删除，回滚时可以把 alias 指回旧索引。

本地升级顺序：

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/alembic upgrade head
./.venv/bin/python scripts/reindex_resource_ownership.py
```

重建会重新调用 embedding 模型，因此在生产环境要评估模型吞吐、成本和执行窗口。

## OSS：预签名 URL 不是授权绕过

上传对象路径由后端生成：

```text
raw/dev/{organization_id}/{knowledge_base_id}/{upload_id}/source.{extension}
```

原始文件名只保存展示用途，不参与 object key。生成分片 presign URL 前，接口会检查：

- 当前用户是否有 upload 权限；
- `upload_id` 是否属于当前组织；
- 上传任务是否仍处于允许上传的状态；
- `part_number` 是否在范围内；
- URL 的 method、content type 和有效期是否符合配置。

URL 让浏览器直传 OSS，减少 API 服务器带宽压力；但它不是文件安全校验。上传完成后的 validate 阶段仍须校验 content length、magic number 和格式结构。

## Chat：会话不是知识库的公共附属物

聊天接口先限定知识库组织，再将新会话写入：

```text
organization_id + created_by_user_id + knowledge_base_id
```

普通成员只能读取自己创建的 conversation；owner/admin 可以在本组织内审核和恢复会话。跨组织的 `thread_id`、conversation ID、review resume 都统一按 404 处理。

## 删除保护

知识库删除不是级联删除。若存在 `documents`、`knowledge_items` 或 `upload_tasks`，接口返回 409，并在响应中说明依赖类型。这样避免一次删除同时丢失对象存储、ES 向量和数据库记录而无法恢复。

## 推荐阅读顺序

1. [models.py](../../../backend/app/db/models.py)：先确认每个资源的组织字段。
2. [resource_access.py](../../../backend/app/security/resource_access.py)：理解“按组织取资源”的统一封装。
3. [knowledge_base.py](../../../backend/app/api/knowledge_base.py)：最简单的 CRUD 授权入口。
4. [vector_service.py](../../../backend/app/services/vector_service.py)：理解 ES filter 和 alias。
5. [upload.py](../../../backend/app/api/upload.py) 与 [upload_service.py](../../../backend/app/services/upload_service.py)：理解 OSS presign 前的二次校验。
6. [chat.py](../../../backend/app/api/chat.py)：理解会话创建、读取和 resume 的权限差异。
7. [test_resource_authorization.py](../../../backend/tests/test_resource_authorization.py)：查看跨组织访问如何被回归测试固定。

## 验收测试

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/python -m unittest \
  tests.test_resource_authorization \
  tests.test_search_permissions \
  tests.test_upload_permissions \
  tests.test_chat_permissions
```

这些用例覆盖“组织 A 访问组织 B”的知识库、文档、搜索、上传任务和会话场景。
