# Day 13：RAG Service

## 今日目标

把“语义检索”继续往前推进成一个可复用的 RAG 服务层。

这一阶段不先暴露新的 API，而是先把后端内部链路打通：

```text
question
  -> retrieve
  -> docs
  -> format_context
  -> answer
```

这样后面无论接：

- `chat` 接口
- LangGraph 节点
- 人工审核流程

都能直接复用这一层。

---

## 本次实现内容

文件：

[backend/app/services/rag_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/rag_service.py:1)

这次实现了三个核心函数：

- `retrieve()`
- `format_context()`
- `generate_answer()`

同时补了两个内部数据结构：

- `RetrievedDocument`
- `RagAnswerResult`

---

## 1. `retrieve()` 在做什么

`retrieve()` 负责把“问题”变成“可用于回答的文档列表”。

它的链路是：

```text
question
  -> search_similar_chunks()
  -> Elasticsearch 返回命中的 chunk
  -> 根据 knowledge_item_id 去 PostgreSQL 补 title
  -> 返回 RetrievedDocument[]
```

这里特意没有直接复用 HTTP 层的 `/search/semantic` 接口，因为：

- service 层不应该依赖 API 层
- 后面 LangGraph 节点会直接调 service
- 这样职责更清晰

---

## 2. `format_context()` 在做什么

`format_context()` 负责把检索结果拼成统一上下文字符串。

输出大概像这样：

```text
[1] 标题：差旅报销流程
doc_id: 1
chunk_id: 2
score: 0.9100
内容：
员工差旅报销需要先提交发票。
```

这样做的目的不是给前端直接展示，而是为了后面喂给：

- LLM prompt
- LangGraph state
- debug 日志

后面接真正大模型时，这个 context 格式可以继续沿用。

---

## 3. `generate_answer()` 为什么先做成抽取式

当前项目里还没有正式接入问答模型 API。

所以 Day 13 先做成：

```text
基于检索结果的抽取式答案生成
```

也就是：

- 先从命中的 chunk 里挑更相关的句子
- 再把这些句子拼成一个可读答案
- 同时返回 citations

这样做有两个好处：

### 第一，当前阶段可以真实跑通

你现在就能完成：

```text
question -> docs -> context -> answer
```

不用等到后面 Day 18 才第一次看到答案链路。

### 第二，接口可以稳定下来

后面接真正的千问 API 或其他 LLM 时：

- `retrieve()` 不用动
- `format_context()` 不用动
- `generate_answer()` 的函数签名也不用动

只要把内部实现从“抽取式”换成“LLM 生成式”即可。

---

## 4. 句子排序现在怎么做

当前没有上 reranker，所以先用了一个轻量规则：

```text
按句子切分
看问题里的关键词是否出现在句子里
匹配越多，优先级越高
同分时文档前面的句子略优先
```

这里不追求“最强效果”，重点是：

- 行为稳定
- 容易理解
- 方便后面替换成更强的 rerank / LLM

---

## 5. 当前返回结果长什么样

`generate_answer()` 返回：

- `answer`
- `context`
- `citations`
- `used_fallback`

其中：

- `answer`：当前生成的答案文本
- `context`：格式化后的上下文
- `citations`：引用列表
- `used_fallback`：当前是否走了第一版抽取式兜底

这个字段后面很有用，因为以后接上真正 LLM 后，你还能知道：

- 是正常走了模型回答
- 还是退回了本地抽取式答案

---

## 本次测试

文件：

[backend/tests/test_rag_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_rag_service.py:1)

覆盖了这些点：

- `retrieve()` 能补标题
- `format_context()` 能正确拼上下文
- `generate_answer()` 能输出答案和 citations
- 无检索结果时能给出兜底回答

---

## 当前阶段说明

Day 13 完成后，RAG 的最小闭环已经具备：

```text
索引文档
  -> 语义检索
  -> 组装上下文
  -> 生成第一版答案
```

后面再接 LangGraph 时，这一层可以直接作为底层能力复用。
