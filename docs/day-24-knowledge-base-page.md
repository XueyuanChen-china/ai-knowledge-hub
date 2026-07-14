# Day 24：知识库列表页与详情页

这一阶段把知识库前端从“只读列表”推进到“可操作的后台页面”。

## 目标

完成知识库前端 CRUD 闭环：

1. 展示知识库列表
2. 弹窗创建知识库
3. 删除知识库
4. 进入详情页
5. 在详情页编辑知识库

## 代码结构

这次新增或重点修改的文件：

- `frontend/app/knowledge-bases/page.tsx`
- `frontend/app/knowledge-bases/[id]/page.tsx`
- `frontend/components/knowledge-base-form.tsx`
- `frontend/components/knowledge-base-delete-modal.tsx`
- `frontend/components/knowledge-base-table.tsx`
- `frontend/lib/api/client.ts`

## 这次做了什么

### 1. 列表页接入操作列

列表页不再只是展示数据，而是增加了：

- `新建知识库` 按钮
- `进入详情` 按钮
- `删除知识库` 按钮

这样列表页就成了真正的入口页。

### 2. 新建知识库弹窗

点击新建按钮后，打开 Mantine `Modal`。

弹窗内部复用：

`KnowledgeBaseForm`

表单只负责收集：

- `name`
- `description`

提交后调用：

`createKnowledgeBase()`

成功后把新结果直接插入当前列表，不需要整页刷新。

### 3. 删除确认弹窗

删除不是直接点一下就发请求，而是先打开：

`KnowledgeBaseDeleteModal`

这一步的作用是避免误删。

确认后调用：

`deleteKnowledgeBase(id)`

删除成功后，把当前列表里的对应项移除。

### 4. 详情页

新增动态路由：

`/knowledge-bases/[id]`

详情页会加载：

- 当前知识库详情
- 当前知识库下的知识条目列表数量

这一页主要承担两件事：

1. 编辑知识库
2. 从详情页删除知识库

所以现在前端知识库 CRUD 才算真正闭环。

## API Client 增强

`frontend/lib/api/client.ts`

这次补了：

- `getKnowledgeBase(id)`
- `createKnowledgeBase(payload)`
- `updateKnowledgeBase(id, payload)`
- `deleteKnowledgeBase(id)`

另外还顺手优化了一点：

只有带请求体时，才自动加：

```http
Content-Type: application/json
```

这样 GET 请求不会无意义地触发更多预检逻辑。

## 组件拆分思路

### `KnowledgeBaseForm`

负责：

- 输入框
- 文本域
- 提交/取消按钮

它被两个场景复用：

1. 创建弹窗
2. 详情编辑页

这样就避免了两份相似表单。

### `KnowledgeBaseDeleteModal`

负责：

- 展示删除确认文案
- 承接确认删除动作

它也被两个场景复用：

1. 列表页删除
2. 详情页删除

## 当前验收结果

已经能完成：

```text
列表展示
创建知识库
删除知识库
进入详情页
编辑知识库
```

对应的前端页面路由：

- `/knowledge-bases`
- `/knowledge-bases/{id}`

## 下一步自然演进

下一步最适合继续补的是：

1. 知识条目列表页
2. 文档上传页
3. 索引按钮和状态展示
4. 搜索页

也就是逐步把“知识库管理”从主记录推进到文档和条目层。
