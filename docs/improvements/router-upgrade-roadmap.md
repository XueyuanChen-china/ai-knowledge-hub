# Day 16 后续升级路线：Router / KB Selector / Structured Output

## 这份文档解决什么问题

当前 Day 16 已经完成的是：

```text
给定 question + knowledge_base_id
  -> Router 判断走 direct / rag / tool
```

也就是说，这一版默认：

- 调用方已经先选好了 `knowledge_base_id`
- Router 只负责判断“查不查当前这个库”

它还没有解决：

- 系统自己在多个知识库之间选库
- Router 返回更稳定的结构化结果
- 复杂问题暂时复用 RAG，多文档总结分支留作后续独立升级

所以后续升级要拆层做，而不是把所有能力一次塞进 Day 16。

---

## 当前边界

现在的路由职责是：

```text
question
  -> route = direct / rag / tool
```

它不是：

```text
question
  -> 先选 knowledge_base
  -> 再决定 route
```

这两个问题必须拆开。

因为：

1. 选库是“范围决策”
2. 路由是“处理方式决策”

如果把两个决策混在一个 prompt 里，后面调试会很难判断到底是：

- 选库错了
- 还是 route 错了

---

## 推荐升级顺序

### Phase A：增强当前 Router 输入

目标：

- 让 Router 不只看到 `question`
- 还看到当前知识库的基础信息

新增输入建议：

- `knowledge_base_id`
- `knowledge_base_name`
- `knowledge_base_description`
- 可选：`document_count`
- 可选：`knowledge_item_count`

升级后的输入形态：

```json
{
  "question": "公司制度怎么报销",
  "knowledge_base": {
    "id": 7,
    "name": "财务制度库",
    "description": "报销、预算、付款、发票、差旅制度"
  }
}
```

这一层的意义是：

- 即使还是单库调用
- 模型也会更清楚“当前这个库是干什么的”

这样 direct / rag 的判断会更稳。

---

### Phase B：把 Router 输出改成真正结构化

目标：

- 不再只靠 prompt 约束“请输出 JSON”
- 而是显式走结构化输出模式

当前状态更新：

- 这一项已经完成第一步
- Router 请求体已经显式带 `response_format={"type":"json_object"}`
- 但应用端 schema 校验还可以继续加强

推荐输出结构：

```json
{
  "route": "direct",
  "reason": "通用概念解释，不依赖知识库",
  "confidence": 0.92
}
```

应用端仍然要做校验：

- `route` 是否在 `direct / rag / tool` 里
- `reason` 是否为空
- `confidence` 是否在 `0~1`

注意：

- JSON Mode 主要解决“输出可解析”
- 不代表可以完全不做应用端校验

所以这一层应该是：

```text
LLM structured output
  -> parser
  -> schema validation
  -> fallback rule
```

---

### Phase C：新增 `kb_selector`

目标：

- 不再要求调用方先手工指定知识库
- 系统根据问题自动选最相关知识库

建议链路：

```text
question
  -> kb_selector
      选择最相关 knowledge_base
  -> router
      判断 direct / rag / tool
```

`kb_selector` 的输入不应该是完整文档，而应该是知识库目录信息：

```json
[
  {
    "id": 1,
    "name": "人事制度库",
    "description": "入职、考勤、请假、绩效"
  },
  {
    "id": 2,
    "name": "财务制度库",
    "description": "报销、预算、付款、发票"
  },
  {
    "id": 3,
    "name": "采购制度库",
    "description": "供应商准入、合同、采购审批"
  }
]
```

推荐输出：

```json
{
  "knowledge_base_id": 2,
  "reason": "问题与报销制度最相关",
  "confidence": 0.95
}
```

这里同样要做应用端兜底：

- 选中的知识库 ID 是否存在
- 置信度是否过低
- 若低于阈值，是否转人工确认或默认走全局搜索

---

### Phase D：复杂问题多步流（后续独立升级）

当前不实现独立的 `complex` 路由。复杂总结、归纳和对比问题统一进入 RAG，避免保留一个无法生成答案的占位分支。

后续如果重新启动这项升级，再单独设计：

```text
子问题拆解
  -> retrieve multiple docs
  -> rerank / group
  -> summarize
  -> answer with citations
```


---

### Phase E：Router 与 Tool Calling 解耦

目标：

- Router 专门负责分类
- Tool Calling 专门负责调用外部能力

建议边界：

- Router：
  - `direct / rag / tool`
- Tool Calling：
  - `search_knowledge_base`
  - `get_document_by_id`
  - `create_review_task`
  - `save_message`

这样后面图工作流会更清楚：

```text
router 先判断路线
具体节点再决定要不要调工具
```

而不是让 Router 自己一边分类一边发工具调用。

---

## 推荐优先级

我建议按这个顺序做：

1. Phase A：给 Router 补 knowledge base 摘要信息
2. Phase B：改成结构化输出 + 应用端校验
3. Phase C：实现 `kb_selector`
4. 后续再评估复杂问题多步总结流
5. Phase E：把工具调用体系接进来

原因：

- A/B 是低风险增强
- C 是真正改变调用方式的升级
- D/E 会扩大系统边界，应该放后面

---

## 下一次实现建议

如果下一步只做一小步，我建议优先落这三件事：

1. Router prompt 里加入当前知识库 `name/description`
2. Router 改成结构化输出模式
3. 应用端补一个严格的 route schema 校验函数

这样做的收益最大，而且不会一下把范围扩得太大。
