# Day 26：语义搜索页面

这一阶段把已有的后端语义搜索接口正式接到前端，形成一个可直接操作的搜索页。

目标很明确：

```text
输入 query
选择 knowledge base
设置 top_k
展示 doc_id / title / preview / score / metadata
```

## 这次改了什么

### 1. 前端 API client 接了 `/search/semantic`

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/types.ts`
- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/lib/api/client.ts`

新增类型：

- `SemanticSearchResult`

新增请求方法：

- `searchSemantic()`

这样前端就不需要自己手写 fetch 细节，后面别的页面如果也要做搜索复用，直接调这个方法即可。

### 2. 新增语义搜索页面 `/search`

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/app/search/page.tsx`

这一页做了几件事：

1. 首次进入时加载知识库列表
2. 默认选中第一条知识库
3. 用户输入 query
4. 调 `POST /search/semantic`
5. 展示返回结果

页面字段包括：

- 知识库选择
- 搜索问题输入框
- `Top K`
- 结果列表

## 结果页怎么展示

每条结果目前展示：

- `title`
- `doc_id`
- `chunk_id`
- `score`
- `content_preview`
- 一部分高频 metadata

这里 metadata 没有一股脑全量平铺，而是先挑比较有价值的字段显示，例如：

- `file_type`
- `filename`
- `heading_path`
- `page_start / page_end`
- `sheet_name`
- `block_type`
- `chunk_index`

这样第一版更适合人看，也更像搜索结果页，而不是调试接口原始 JSON。

## 为什么还要展示 metadata

因为语义搜索不是只看一段 preview 就够了。

很多时候你还想快速判断：

- 这条结果来自 PDF 还是 Word
- 来自哪一页
- 来自哪个标题
- 是正文、表格还是别的 block

这些上下文信息决定了结果是否真的可信。

## 导航也补上了

文件：

- `/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/frontend/components/app-frame.tsx`

左侧导航新增：

- `语义搜索`

这样现在前端主工作台的路径就是：

```text
总览
知识库
文档上传
语义搜索
对话工作台
```

## 当前验收方式

1. 启动后端
2. 启动前端
3. 打开：

```text
http://localhost:3000/search
```

4. 选择一个已经完成索引的知识库
5. 输入问题，例如：

```text
采购复核的触发条件是什么？
```

6. 点击“开始搜索”

验收点：

1. 页面能正常发请求
2. 能看到结果列表
3. 每条结果能看到 `doc_id / title / preview / score`
4. metadata 能看到基础来源信息

## 当前这一版的边界

这版先完成 Day 26 的最小闭环，还没做：

- 高亮命中词
- 搜索历史
- 按 metadata 过滤
- rerank 前后对比
- 结果点击跳文档原文/知识条目详情

这些更适合作为下一步产品化增强，而不是塞进 Day 26 的第一版里。
