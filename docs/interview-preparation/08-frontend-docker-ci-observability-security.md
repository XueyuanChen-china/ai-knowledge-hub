# 08 前端、Docker、CI、可观测性与安全

## 一、前端架构

当前前端是 React + Vite + TypeScript + Mantine。项目主要是登录后的管理工作台，不依赖 SSR、SEO 或 Next 全栈能力，Vite 更轻、更直接。

```text
BrowserRouter
  -> /login
  -> AuthGate
  -> AppFrame
  -> Outlet 子路由
```

`Outlet` 是父布局中渲染匹配子路由的位置。`AuthGate` 检查 `sessionStorage` token 并调用 `/api/auth/me`，未登录则跳转登录页。

token 使用 `sessionStorage`，请求时 API client 主动添加 `Authorization`。它不像 Cookie 自动发送，因此 CSRF 风险较低，但仍要防 XSS。更严格生产方案可评估 HttpOnly Secure SameSite Cookie。

## 二、OSS 前端上传与 SSE

```text
init -> batch presign -> slice File -> PUT parts to OSS
-> collect ETag -> complete parts -> complete upload -> poll status
```

浏览器直传 OSS 必须在 Bucket 配置允许前端 origin、PUT 和必要请求头。FastAPI CORS 不能替 OSS 配 CORS。

SSE 使用 `fetch` 读取 `ReadableStream`，`TextDecoder` 增量解码，buffer 按 `\n\n` 取完整事件。网络 chunk 与事件边界无关，不能每次 read 都直接 `JSON.parse`。

## 三、Docker 基础

- Dockerfile：镜像构建说明；
- Image：只读模板；
- Container：镜像运行实例，一个镜像可启动多个容器；
- Compose：定义一组服务如何共同启动、联网、挂载和配置，不是 Dockerfile。

Compose 服务之间用服务名访问：

```text
postgres:5432
redis:6379
rabbitmq:5672
elasticsearch:9200
```

容器内 `localhost` 只指当前容器。

## 四、Multi-stage build

前端镜像：

```text
Stage 1 Node: npm ci + vite build
Stage 2 Nginx: 只复制 dist 静态产物
```

最终镜像不带 Node 构建工具和完整 `node_modules`。backend 与 Celery Worker 使用同一个项目镜像，因为代码和依赖相同，只是启动命令不同。

## 五、环境变量与 Secret

Compose 中 `${NAME}` 从宿主环境或 `.env` 替换；`env_file: backend/.env` 把文件变量注入容器。公开 GitHub 仓库不能让单个已提交文件私有，所以 `.env` 必须 `.gitignore`，只提交 `.env.example`。

前端 `VITE_*` 会进入浏览器 bundle，不能放 OSS secret、JWT secret 或 LLM API key。

## 六、CI 与可观测性

CI 在干净环境重复验证后端测试、前端 lint/test/build、migration 和 integration。缓存依赖可提速，但不能缓存敏感信息。

可观测性三类信号：

- Logs：结构化事件、request ID、trace ID；
- Metrics：请求数、错误率、P50/P95、检索耗时、job 和重试数；
- Traces：跨 API、MQ、Worker 的调用链，当前先做关联 ID，完整 OTel 后续升级。

日志脱敏按字段名替换敏感值，并扫描普通文本中的 Bearer token 和签名 URL。不是不记录日志，而是保留排障信息、移除凭证。

## 七、Live 与 Ready

- `/health/live`：进程是否活着；
- `/health/ready`：是否能接流量，检查数据库 revision，并按职责报告 ES/RabbitMQ。

数据库断开时 live 可成功但 ready 失败。P50 是中位延迟，P95 表示 95% 请求不超过该值，后者更能暴露长尾。

## 八、安全清单

- 密码强哈希；
- JWT 校验 `exp/iss/aud/jti/ver`；
- Redis 撤销；
- RBAC + organization scope；
- ES 召回前权限过滤；
- OSS 后端生成 key、短 TTL presign；
- magic number 和 ZIP 安全限制；
- 明确 CORS origin；
- 安全审计；
- 日志和响应不泄露 secret。

## 九、常见追问

### CORS 是后端安全机制吗？

CORS 是浏览器同源策略的协作机制，不阻止 curl 或服务端请求。真正安全仍依赖认证、授权和资源校验。

### Compose 能直接用于大型生产吗？

适合本地、CI 和单机演示。生产高可用还需要编排、Secret 管理、滚动发布、资源配额、持久卷备份和集群监控。

### 如何定位上传 pending？

从 upload ID 查 upload task 和 stage jobs，沿 trace ID 查 API/Celery 日志，再看 RabbitMQ/DLQ、Worker、attempt、OSS 对象和 ES 索引。

## 十、关键代码

- [前端路由](../../frontend/src/router.tsx)
- [API Client](../../frontend/lib/api/client.ts)
- [SSE Parser](../../frontend/lib/api/sse.ts)
- [Chat 页面](../../frontend/app/chat/page.tsx)
- [Compose](../../compose.yml)
- [后端 Dockerfile](../../backend/Dockerfile)
- [前端 Dockerfile](../../frontend/Dockerfile)
- [CI](../../.github/workflows/ci.yml)
- [可观测性](../../backend/app/observability)
- [Request Middleware](../../backend/app/middleware/request_context.py)

