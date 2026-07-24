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
  -> 校验签名、算法、sub、exp、iss、aud、jti
  -> 查询用户和 membership 是否仍然有效
  -> 返回 user / organization / role
```

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
  - `create_access_token()` 写入 `sub / exp / iss / aud / jti`。
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
- `frontend/components/app-frame.tsx` 的退出登录只清理当前标签页 token；当前 JWT 是短期无状态 token，后续如需立即撤销要增加服务端 jti 黑名单或 token version。

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
