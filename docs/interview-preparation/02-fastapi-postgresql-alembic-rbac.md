# 02 FastAPI、PostgreSQL、Alembic 与 RBAC

## 一、请求从哪里进入

FastAPI 在 [main.py](../../backend/app/main.py) 注册 Router、中间件、CORS 和 lifespan。业务接口通过 `Depends` 注入数据库 Session 和当前登录主体。

```text
HTTP Request
  -> RequestContextMiddleware
  -> CORS
  -> Router
  -> get_current_principal
  -> require_permission
  -> organization scoped query
  -> Service / Database
```

依赖注入的价值不是少写参数，而是把身份校验、权限和数据库生命周期放到统一入口，避免每个 API 各写一套。

## 二、PostgreSQL 与 SQLModel

SQLModel 同时结合 Pydantic 数据校验和 SQLAlchemy ORM。项目主要模型包括：

- 身份：`Organization`、`User`、`OrganizationMembership`；
- 知识：`KnowledgeBase`、`Document`、`KnowledgeItem`、`Chunk`；
- 对话：`Conversation`、`Message`、`ConversationMemory`、`ReviewTask`；
- 上传：`UploadTask`、`UploadPart`、`UploadProcessingJob`；
- 审计：`SecurityAuditLog`、`UploadAuditLog`。

典型事务：

```python
session.add(entity)
session.commit()
session.refresh(entity)
```

- `add`：把对象加入当前 Session 的工作集合；
- `commit`：提交数据库事务；
- `refresh`：从数据库重新读取生成的 ID、默认值等。

## 三、为什么需要 Alembic

`SQLModel.metadata.create_all()` 只能创建不存在的表，不能可靠表达列修改、数据回填、索引调整和回滚。Alembic 把 schema 演进记录成有序 migration：

```text
代码期望 revision
        |
alembic upgrade head
        |
PostgreSQL schema 达到当前版本
```

项目启动策略：API 容器先等待依赖，再执行 migration；Celery Worker 只检查 revision，不执行 DDL。这样多个 Worker 启动时不会同时争抢数据库结构变更。

面试概念：

- **DDL**：`CREATE TABLE`、`ALTER TABLE`、`CREATE INDEX` 等结构操作；
- **DML**：`INSERT`、`UPDATE`、`DELETE` 等数据操作；
- **baseline migration**：把已有完整 schema 固定为迁移起点；
- **stamp**：只登记 revision，不执行对应 DDL，适合已存在且人工核对一致的库。

## 四、认证与授权不是一回事

- Authentication：你是谁？
- Authorization：你能做什么？

登录后 JWT 包含：

| Claim      | 含义               |
| ---------- | ------------------ |
| `sub`    | 用户 ID            |
| `exp`    | 过期时间           |
| `iss`    | 签发方             |
| `aud`    | 目标受众           |
| `jti`    | token 唯一 ID      |
| `org_id` | 当前组织           |
| `role`   | 当前角色           |
| `ver`    | 用户 token version |

`ver` 用于使一批旧 token 失效。例如管理员禁用用户或用户“退出所有设备”时增加 `token_version`，旧 JWT 即使签名和 `exp` 有效，也会因为版本不一致被拒绝。

## 五、为什么还需要 Redis 黑名单

普通 JWT 是无状态的，后端签发后无法主动收回。退出登录时把当前 `jti` 写入 Redis：

```text
blacklist:{jti} = 1
TTL = token 剩余寿命
```

设置 TTL 是因为 token 自然过期后黑名单没有意义，Redis 可以自动清理。`jti` 撤销单个 token，`token_version` 撤销某个用户的全部旧 token。

## 六、RBAC 与资源边界

RBAC 通过角色映射权限：

```text
owner / admin / editor / viewer
             -> permission set
```

当前项目的角色概览如下：

| 角色 | 主要权限 |
| --- | --- |
| `owner` | 全部权限 |
| `admin` | 知识库、内容、用户管理全部权限 |
| `editor` | 查看和编辑内容、上传、搜索、问答，不能删除知识库，不能管理用户 |
| `viewer` | 只能查看、搜索和问答，不能新增、编辑、上传或删除 |

这里需要特别注意：**当前代码中 `owner` 和 `admin` 的普通接口权限集合实际上相同**，两者都拥有知识库删除、内容删除和用户管理权限。它们的区别主要体现在组织治理规则：

- `owner` 是组织的最高管理角色；
- `admin` 可以处理日常成员管理，但不能创建、降级或修改 `owner`；
- 只有 `owner` 可以管理 `owner` 角色；
- 系统不允许修改或移除组织中最后一个有效 `owner`。

因此可以这样理解：

```text
admin = 日常管理员
owner = 组织最终负责人
```

如果未来要让两者的业务权限真正不同，只需要在 `ROLE_PERMISSIONS` 中拆开权限集合，并补充对应的 API 测试；当前实现已经把 owner 角色的治理约束单独写在成员管理接口中。

但只有 RBAC 不够。某个用户即使有 `document:read`，也只能读取自己组织的文档。因此按 ID 查询必须同时带资源边界：

```python
select(Document).where(
    Document.id == document_id,
    Document.organization_id == principal.organization_id,
)
```

这防止 IDOR：攻击者通过猜测递增的 `document_id` 读取其他组织资源。

权限需要贯穿四层：

1. FastAPI dependency 判断动作权限；
2. PostgreSQL 查询限定 `organization_id`；
3. Elasticsearch kNN/BM25 查询带组织过滤；
4. OSS presign 前校验 upload task 所属组织和状态。

## 七、常见八股

### 事务的 ACID 是什么？

- Atomicity：事务内操作全部成功或全部回滚；
- Consistency：约束在事务前后成立；
- Isolation：并发事务互不看到不该看到的中间状态；
- Durability：提交后的结果不会因普通故障丢失。

### 乐观锁和悲观锁有什么区别？

- 悲观锁先锁行，例如 `SELECT ... FOR UPDATE`，适合冲突概率高、必须独占的 claim；
- 乐观锁通过 version 或条件更新检测冲突，适合冲突较少场景；
- `SKIP LOCKED` 允许多个 Worker 领取不同任务，不等待其他 Worker 已锁住的行。

### 数据库索引为什么能加速？

索引以额外空间和写入成本换取查询定位速度。联合索引需要关注最左前缀和选择性；低基数字段单独建索引未必有效。外键列和高频过滤的 `organization_id`、`knowledge_base_id` 是本项目的重点。

### 连接池解决什么？

建立 PostgreSQL TCP 连接有握手和认证成本。连接池维护少量可复用连接，请求借出后归还，减少频繁建连，同时通过池大小限制数据库并发压力。

## 八、关键代码

- [数据库连接与 revision 检查](../../backend/app/db/database.py)
- [数据模型](../../backend/app/db/models.py)
- [认证依赖](../../backend/app/security/dependencies.py)
- [角色策略](../../backend/app/security/policies.py)
- [JWT](../../backend/app/security/tokens.py)
- [Redis 撤销](../../backend/app/security/revocation.py)
- [资源归属查询](../../backend/app/security/resource_access.py)
- [Alembic 配置](../../backend/alembic/env.py)
