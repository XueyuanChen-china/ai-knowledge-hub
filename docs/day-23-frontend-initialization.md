# Day 23：前端初始化

这一阶段先把 `frontend` 独立搭起来，目标不是一次做完所有页面，而是先建立一个后续能持续扩展的前端骨架。

## 选型

本次前端技术栈：

```text
框架：React + Vite
语言：TypeScript
UI 组件库：Mantine
图标：Tabler Icons
```

这里选 Mantine，主要是因为：

1. 公开成熟，文档完整
2. 表单、布局、表格、导航这些后台型页面常用组件比较全
3. 第一版可以快速搭出“像真实内部工具”的界面

## 这次做了什么

### 1. 创建 `frontend`

目录：

`frontend/`

核心文件包括：

- `package.json`
- `src/main.tsx`
- `src/router.tsx`
- `app/page.tsx`
- `app/knowledge-bases/page.tsx`
- `app/chat/page.tsx`
- `lib/api/client.ts`
- `lib/api/types.ts`
- `components/app-frame.tsx`

### 2. 配置路由

使用 React Router：

```text
/                  首页总览
/knowledge-bases   知识库页
/chat              对话工作台
```

这一版先把路由打通，后续可以继续加：

- `/documents`
- `/search`
- `/review-tasks`

### 3. 配置 API Client

文件：

`frontend/lib/api/client.ts`

当前先封装这些能力：

- `getKnowledgeBases()`
- `getKnowledgeItems()`
- `getDashboardSummary()`
- `runChat()`

并统一从：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

读取后端地址。

这样后面切测试环境、线上环境时，不需要到处改请求地址。

### 4. 配置 UI 组件库

Mantine Provider 放在：

`frontend/src/main.tsx`

这样全局页面都能直接复用：

- `AppShell`
- `Card`
- `Table`
- `Alert`
- `Button`
- `Select`
- `Textarea`

## 当前页面说明

### 首页 `/`

职责：

- 拉取知识库和知识条目
- 做基础统计展示
- 展示知识库列表

这是一个典型后台工作台首页，不是营销页。

### 知识库页 `/knowledge-bases`

职责：

- 独立查看知识库列表
- 后续继续补创建 / 编辑 / 删除入口

### 对话工作台 `/chat`

职责：

- 选择知识库
- 输入问题
- 调用 `/api/chat`
- 展示 route / answer / citations / docs_preview

这一页的价值在于，它不是静态页面，而是已经接了真实后端对话链路。

## 启动方式

先启动后端，再启动前端。

前端：

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev -- --host localhost --port 3000
```

默认访问：

```text
http://127.0.0.1:3000
```

## 当前边界

这一阶段只完成“前端初始化”和“基础工作台骨架”。

还没有做：

- 文档上传页面
- 文档切片管理页
- 文档索引按钮和进度状态
- 语义搜索独立页面
- 审核中断与恢复页
- 会话历史侧边栏

也就是说，这一版重点是：

```text
能启动
能访问
有路由
有统一布局
能打真实 API
```

这已经满足“前端初始化”的验收目标了。
