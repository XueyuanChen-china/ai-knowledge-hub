 

# U3：用户、组织与 RBAC

U3 建立身份和权限的基础设施。它先解决“请求是谁发的、属于哪个组织、拥有哪个角色”，U4 再把知识库、文档、搜索和 OSS 对象逐个接入资源授权。

## 数据模型

```text
organizations
      1
      |
organization_memberships
      |
      N
users
```

- `organizations`：组织边界；U3 migration 创建一个 `default` 组织。
- `users`：用户账号和 Argon2 密码哈希。
- `organization_memberships`：用户加入组织的关系表，并保存 `owner/admin/editor/viewer` 角色。
- 一个用户未来可以加入多个组织，在不同组织中拥有不同角色。
- U3 不给现有知识库、文档回填 `organization_id`，这属于 U4 的资源授权 migration。

## 登录链路

```text
POST /api/auth/login
  -> 标准化 email
  -> 查询用户
  -> Argon2 verify
  -> 查询组织 membership
  -> 签发短期 JWT
  -> 前端写入当前标签页 sessionStorage

GET /api/auth/me
  -> 读取 Authorization: Bearer <token>
  -> 校验签名、算法、sub、exp、iss、aud、jti、ver
  -> 查询 Redis 是否存在 blacklist:{jti}
  -> 查询用户、membership 和 token_version 是否仍然有效
  -> 返回 user / organization / role
```

## JWT 主动撤销

当前项目使用 Docker 运行 Redis：

```bash
cd backend
bash scripts/start_redis_local.sh
```

配置项：

```env
AUTH_REDIS_URL=redis://localhost:6379/0
AUTH_TOKEN_BLACKLIST_PREFIX=ai-knowledge-hub:auth:blacklist:
AUTH_REDIS_SOCKET_TIMEOUT_SECONDS=2.0
```

退出链路：

```text
POST /api/auth/logout
  -> 校验当前 JWT
  -> 读取 jti 和 exp
  -> Redis 写入 blacklist:{jti}=1
  -> TTL 设置为 token 剩余有效时间
  -> 前端 finally 清理 sessionStorage
```

Redis 只保存 `jti`，不保存 token 正文。TTL 到期后，JWT 本身也已经失效，Redis
会自动删除无用的黑名单记录。Redis 不可用时，鉴权依赖返回 503，而不是放行请求，
避免撤销机制失效后继续接受旧 token。

这是一种“按单个 token 撤销”的方案。它的代价是每次受保护请求都要查询 Redis，
因此 JWT 鉴权不再是完全无状态。多副本部署时，所有 API 实例必须连接同一个 Redis。

### 为什么登录失败要统一文案

不存在的邮箱和错误密码都返回 `Invalid email or password`。如果分别返回“用户不存在”和“密码错误”，攻击者就能批量枚举系统中的有效账号。

### 为什么 token 里还要有 jti

`jti` 是每个 token 的唯一 ID，当前用于满足 token 身份完整性和审计关联。以后接 token 黑名单、主动撤销或安全事件追踪时，可以按 jti 定位具体 token，而不是只能按用户整体处理。

## 密码和 JWT 的代码路径

- `backend/app/security/passwords.py`
  - `hash_password()` 使用 Argon2id 生成哈希。
  - `verify_password()` 只比较明文输入和 hash，不反解密码。
  - 用户不存在时对 dummy hash 执行 verify，尽量减少响应时间差异。
- `backend/app/security/tokens.py`
  - `create_access_token()` 写入 `sub / exp / iss / aud / jti / ver`。
  - `decode_access_token()` 使用固定算法、issuer、audience 和 required claims 校验。
- `backend/app/security/dependencies.py`
  - `get_current_principal()` 是 FastAPI 可复用依赖。
  - `require_permission("content:write")` 返回角色权限检查依赖。
- `backend/app/security/policies.py`
  - 集中维护角色到权限的映射，避免把 `if role == ...` 散落到业务接口。
- `backend/app/security/rate_limit.py`
  - 用进程内锁和时间窗口限制连续失败登录；多副本时需要升级成 Redis 等共享限流存储。

## 前端会话

- `frontend/app/login/page.tsx` 提交登录并保存 access token。
- `frontend/components/auth-gate.tsx` 进入业务路由时调用 `/api/auth/me` 验证 token。
- `frontend/lib/api/client.ts` 自动补充 `Authorization` header。
- 收到 401 时清理 sessionStorage，并派发事件跳转登录页。
- `frontend/components/app-frame.tsx` 的退出登录会调用 `/api/auth/logout`，服务端把当前 `jti` 加入 Redis 黑名单，然后清理当前标签页 token。
- Redis 黑名单只保留到 token 的自然过期时间；如果 Redis 不可用，后端鉴权按 fail-closed 返回 503。

## 管理员创建方式

迁移只创建默认组织，不创建默认密码。开发环境显式执行：

```bash
cd backend
export AUTH_JWT_SECRET="本地随机长字符串"
export SEED_ADMIN_EMAIL="admin@example.com"
export SEED_ADMIN_PASSWORD="至少 8 位的本地开发密码"
./.venv/bin/python scripts/seed_admin.py
```

密码不会写入代码、migration、日志或 API 响应。生产环境也不能把 seed 脚本放进应用启动钩子。

## U3 的边界

当前 `require_permission()` 已经可以被业务路由复用，但知识库、文档、ES 和 OSS 的组织归属过滤留到 U4。原因是身份表和存量资源回填拆开，避免一次 migration 同时改变账号体系和大量业务数据。

账号生命周期、成员管理和安全审计已在后续 U3.5 落地，见 `u3.5-account-management.md`。
