# Day 28：前端迁移到 Vite

这一阶段不是重做前端功能，而是把前端运行架构从：

```text
Next.js
```

迁移到：

```text
React + Vite + TypeScript + Mantine
```

目标很明确：

1. 保留现有页面和交互
2. 保留现有 API client
3. 去掉当前阶段并不需要的 Next 运行时复杂度
4. 让本地开发更轻、更稳

## 为什么要迁

这个项目当前前端更像内部工作台，而不是面向公网的 SSR 产品。

当前阶段并不强依赖：

- SSR
- SEO
- 首屏渲染优化
- Next route handlers
- server actions

但前面实际开发里已经多次遇到：

- `.next` 缓存错乱
- `main-app.js` / `vendor-chunks` 404
- 动态路由 dev chunk 丢失

所以这次迁移的核心判断是：

> 功能不变，但把不必要的运行时复杂度拿掉。

## 这次改了什么

### 1. 构建链从 Next 换成 Vite

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/package.json`
- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/vite.config.ts`
- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/index.html`

现在脚本变成：

```json
"dev": "vite",
"build": "tsc --noEmit && vite build",
"preview": "vite preview"
```

这样前端就不再依赖 `next dev / next build`。

### 2. 增加新的前端入口

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/src/main.tsx`
- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/src/router.tsx`

现在入口逻辑变成：

```text
index.html
  -> src/main.tsx
      -> MantineProvider
      -> BrowserRouter
      -> AppFrame
      -> 各业务页面
```

### 3. 路由改成 React Router

原来是 Next App Router。

现在改成 `react-router-dom`，对应页面仍然保持这些路径：

```text
/                    首页
/knowledge-bases     知识库列表
/knowledge-bases/:id 知识库详情
/knowledge-items/:id 知识条目详情
/documents           文档上传与索引
/search              语义搜索
/chat                专家问答
```

也就是说：

- 用户访问路径不变
- 页面能力不变
- 只是底层路由机制换了

### 4. 做了一个 Next 兼容层

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/src/compat/next-link.tsx`
- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/src/compat/next-navigation.ts`

这是这次迁移里最关键的降风险手段。

原因是现有很多页面已经写了：

- `next/link`
- `next/navigation`

如果全部人工改成 React Router，改动面会比较大。

所以这次做法是：

- `next/link` -> 映射到 React Router 的 `Link`
- `usePathname` -> 映射到 `useLocation`
- `useParams` -> 映射到 React Router `useParams`
- `useRouter().push()` -> 映射到 `navigate()`
- `useRouter().refresh()` -> 退化成浏览器刷新

这样页面层代码大部分都不用推倒重写。

### 5. 环境变量从 `NEXT_PUBLIC_*` 改成 `VITE_*`

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/.env.example`
- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/.env.local`
- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/client.ts`

现在前端读取：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

这是 Vite 的标准暴露方式。

### 6. 清掉 Next 专用文件

这次删掉了这些只属于 Next 的文件：

- `app/layout.tsx`
- `next-env.d.ts`
- `next.config.ts`

原因很简单：

- 它们已经不再参与运行
- 留着只会制造误导

## 哪些东西没有变

这次迁移刻意保持不变的部分包括：

- 页面路径
- Mantine 组件体系
- 现有 API client
- 知识库 CRUD 页面
- 知识条目详情页
- 文档上传与索引页
- 语义搜索页
- 专家问答页和人工审核流

也就是说，这次不是产品重做，而是架构替换。

## 当前启动方式

前端：

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend
cp .env.example .env.local
npm install
npm run dev -- --host localhost --port 3000
```

访问：

```text
http://localhost:3000
```

## 验证结果

这次迁移后已经完成：

1. `npm run build`
2. `npm run lint`
3. 本地 Vite 启动验证
4. 关键页面路由验证

说明这次迁移至少在构建和基础运行层面已经闭环。

## 当前已知点

Vite 构建能过，但会有一个打包提示：

- 主 chunk 体积偏大

这不是错误，只是说明当前前端还没有做更细的代码分包。

后面如果页面继续增多，可以再做：

- route-level lazy loading
- 手动 chunk 拆分
- 重型页面按路由延迟加载

但这不影响当前项目继续开发。
