# U9：前端质量、鉴权与自动化测试

## 目标

U9 不做大规模视觉重做，重点是让当前 React + Vite + Mantine 前端具备可重复验证的工程基础：

- 受保护路由不会在未登录时展示业务页面；
- 401 会清理当前标签页 token，并跳回登录页；
- SSE 的答案增量、引用事件和多行文本不会被错误解析；
- human review 的 `interrupted -> resume -> completed` 状态可以单独测试；
- 页面路由按需加载，聊天页不阻塞首屏 bundle；
- 登录流程可以用 Playwright 真实跑通。

## 已有能力与本阶段补充

项目原来已经有 `AuthGate`、`sessionStorage`、统一 API client 和 SSE 流式聊天。本阶段没有重复造一套鉴权，而是补齐它们的测试边界：

| 能力 | 当前实现 |
| --- | --- |
| 登录状态 | `sessionStorage` 保存短期 access token |
| 路由保护 | `AuthGate` 调用 `/api/auth/me` 后决定渲染或跳转 |
| 401 处理 | API client 清理 token，并派发 `auth-expired` 事件 |
| SSE 解析 | `lib/api/sse.ts` 区分纯文本 `answer` 与 JSON 事件 |
| 审核状态 | `app/chat/chat-stream-state.ts` 统一终态判断 |
| 路由性能 | `src/router.tsx` 对业务页面使用 `React.lazy` |

## SSE 为什么单独拆文件

原来 SSE 解析函数嵌在 API client 中，页面和测试都无法直接验证它。现在解析逻辑位于：

```text
frontend/lib/api/sse.ts
```

规则是：

```text
event: answer
data: 纯文本增量

event: references
data: [1, 2]

event: node
data: {"node":"retrieve"}
```

`answer` 不执行 `JSON.parse()`，保留模型文本和换行；`references`、`node` 等结构化事件才解析 JSON。每个 `data:` 行只移除 SSE 协议要求的一个前导空格，不会误删正文缩进。

## interrupt / resume 的测试边界

`frontend/app/chat/chat-stream-state.ts` 只承载与 UI 状态有关的纯函数：

- `appendAnswerText()`：按到达顺序累积答案增量；
- `appendReferenceText()`：回答结束后追加引用编号；
- `resolveChatTerminalState()`：`interrupted` 保留审核对象，`completed` 清空待审核状态。

真正的 LangGraph、数据库和 Celery 行为仍由后端测试负责。前端单测只验证前端收到 SSE 后不会把审核状态误显示成完成，也不会把上一段答案覆盖掉。

## 测试命令

```bash
cd frontend
npm run lint
npm run test
npm run build
```

Playwright 浏览器流程需要后端运行，并通过环境变量提供测试账号：

```bash
npx playwright install chromium
E2E_EMAIL="owner.test@example.com" \
E2E_PASSWORD="本地测试密码" \
npm run test:e2e
```

Playwright 会启动 Vite preview，并使用 `localhost:3000`，与后端默认 CORS 配置保持一致。测试报告和截图目录不会提交到 Git。

## 当前边界

- 当前 Playwright 只覆盖登录和受保护首页，完整上传、搜索、Chat、审核恢复的 Compose E2E 留给 U10；
- 没有做浏览器矩阵和视觉回归；
- `sessionStorage` token 仍然是当前设计，服务端 jti 撤销由后端 Redis 负责；
- Vite 的 CJS API deprecation warning 来自现有工具链，不影响构建结果，后续可随 Vite 配置升级处理。
