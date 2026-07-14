# Day 25：文档上传和索引页

## 本次目标

把前端接到现有文档上传和索引 API，上线一个可直接验收的文档工作台：

```text
选择知识库
  -> 上传文档
  -> 查看文档列表
  -> 点击构建索引
  -> 观察 uploaded / indexed / failed
```

## 这次改了什么

### 1. 后端补了文档列表接口

文件：

[backend/app/api/document.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/document.py)

新增：

```text
GET /documents
```

支持：

- 返回全部文档
- 按 `knowledge_base_id` 过滤
- 按创建时间倒序展示

这样前端就不用靠上传成功后的单条响应自己拼列表了，而是可以稳定刷新当前知识库下的全部文档。

### 2. 索引失败时显式标记 failed

还是这个文件：

[backend/app/api/document.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/document.py)

之前 `POST /documents/{id}/index` 如果向量写入失败，状态不一定能明确落到 `failed`。

现在改成：

- 索引成功：`documents.status = indexed`
- 索引失败：`documents.status = failed`

这样前端页面就能直接显示失败状态，而不是只看到接口报错。

### 3. 前端 API client 接了 FormData 和文档接口

文件：

- [frontend/lib/api/client.ts](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/client.ts)
- [frontend/lib/api/types.ts](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/types.ts)

新增能力：

- `getDocuments()`
- `uploadDocument()`
- `indexDocument()`
- `DocumentRecord`
- `DocumentIndexResponse`

这里有个关键点：

上传文件必须走 `FormData`，不能继续按 JSON 请求去补 `Content-Type: application/json`。

所以 `request()` 里加了判断：

- `FormData`：让浏览器自己带 `multipart/form-data; boundary=...`
- 普通字符串 body：再补 JSON 请求头

### 4. 新增前端文档页

文件：

[frontend/app/documents/page.tsx](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/app/documents/page.tsx)

页面包含三块：

#### 上传区

- 选择知识库
- 选择文件
- 点击上传

#### 列表区

- 文档 ID
- 文件名
- 文件类型
- 状态
- 提取文本预览
- 上传时间

#### 行内操作

- 点击“构建索引”
- 调现有 `POST /documents/{id}/index`
- 成功后把当前行状态改成 `indexed`
- 失败后把当前行状态改成 `failed`

### 5. 左侧导航补了入口

文件：

[frontend/components/app-frame.tsx](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/components/app-frame.tsx)

新增导航项：

```text
文档上传
```

## 代码链路怎么走

### 上传链路

```text
前端点击上传
  -> uploadDocument()
  -> POST /documents
  -> 后端保存文件
  -> 提取 extracted_text
  -> 写 documents 表
  -> 返回 DocumentRead
  -> 前端把新文档插入当前列表顶部
```

### 索引链路

```text
前端点击构建索引
  -> indexDocument(documentId)
  -> POST /documents/{id}/index
  -> 后端切 chunk
  -> 写 PostgreSQL chunks
  -> 生成 embedding
  -> 写 Elasticsearch
  -> 回填 vector_id
  -> 更新 documents.status
  -> 前端刷新当前行状态
```

## 为什么这一步要补 GET /documents

因为只靠上传接口返回一条数据，不足以支撑真正的文档页。

文档页至少要支持：

- 刷新
- 按知识库过滤
- 重新打开页面后继续看已有数据
- 上传后和索引后的状态对齐

所以单独补一个列表接口是合理的，不然前端状态只存在浏览器内存里，不稳。

## 当前验收方式

### 1. 启动后端

```bash
cd backend
uvicorn app.main:app --reload
```

### 2. 启动前端

```bash
cd frontend
npm run dev
```

### 3. 打开页面

```text
http://localhost:3000/documents
```

### 4. 手动验收

按这个顺序：

```text
1. 选择一个知识库
2. 上传 txt / md / pdf / docx / xlsx 任意文件
3. 看到列表新增一条 uploaded
4. 点击构建索引
5. 成功时状态变 indexed
6. 故障时状态变 failed
```

## 当前范围和后续可升级点

这一步先完成 Day 25 最小闭环，暂时还没做：

- 文档删除
- 文档详情页
- 索引进度条
- 自动轮询索引状态
- 上传后自动触发索引
- 文档级失败原因单独持久化

如果后面继续做 Day 26/27，这一页可以直接扩成完整文档中心。
