# Retrieval Quality Improvements

## 这份文档解决什么问题

当前 RAG 主链路已经能跑通：

```text
query
  -> retrieve
  -> relevance_check
  -> answer
```

但从实际返回结果看，检索质量还有继续优化空间。

典型现象包括：

- top1 / top2 不是最相关 chunk
- 回答能答对，但依赖模型从 top-k 里自己挑对证据
- `docs_preview` 里前几条看起来不够“贴题”

所以后续重点已经不是“能不能检索”，而是“检索结果排得够不够准、够不够稳”。

---

## 当前待优化项

### 0. 企业级检索链路目标形态

当前第一版主链路仍然偏简单：

```text
query
  -> vector retrieve
  -> relevance_check
  -> answer
```

后续更完整的企业知识库问答链路建议升级成：

```text
1. retrieve
   - vector top_k
   - BM25 top_k
   - metadata filter
   - permission filter

2. fusion
   - 合并向量结果和关键词结果
   - 按 doc_id / chunk_id 去重
   - 做初步融合排序

3. rerank
   - cross-encoder / bge-reranker / LLM rerank
   - 判断 query 和 chunk 是否真的相关

4. relevance gate
   - top rerank score 是否足够高
   - 是否有足够证据覆盖问题
   - 是否命中关键业务实体
   - 是否允许直接进入 answer

5. answer
   - 只允许基于通过 gate 的 chunk 生成
   - 如果证据不足，进入 no_answer / need_review
```

这个目标形态的核心不是“多召回几个 chunk”，而是把召回、过滤、融合、重排、证据判定拆开。

- `vector retrieve` 负责语义相似，能处理同义表达和口语问题。
- `BM25 retrieve` 负责字面关键词命中，能减少“语义像但事实不相关”的误召回。
- `metadata filter` 控制文件类型、文档范围、知识库范围、来源类型。
- `permission filter` 控制企业权限边界，避免用户看到无权访问的 chunk。
- `fusion` 把不同召回来源合并，避免只依赖单一路径。
- `rerank` 判断“这个 chunk 是否真的回答这个 query”。
- `relevance gate` 决定是否允许生成答案，避免模型基于弱证据硬答。

当前项目刚补的“关键词覆盖检查”属于 `relevance gate` 的轻量版。
它是临时保护，不是最终形态。后续接入 BM25 / rerank 后，关键词覆盖应该降级为 gate 的一个特征，而不是唯一硬规则。

---

### 1. Rerank

目标：

- 先召回一批候选 chunk
- 再做二次重排

建议链路：

```text
query
  -> dense retrieve top_k=10~20
  -> rerank
  -> top_n for answer
```

价值：

- 把真正和问题最相关的 chunk 排到前面
- 降低“模型自己从 top-k 挑证据”的压力

适用场景：

- 多个 chunk 都和主题相关，但只有少数真正回答了问题
- 文档里有大量背景段、说明段、复盘段混在一起

---

### 2. Query Rewrite

目标：

- 在检索前先把用户问题改写成更适合召回的查询语句

例子：

原问题：

```text
采购复核的触发条件是什么？
```

可改写为：

```text
采购委员会复核 触发条件 单次采购金额 超过二十万元
```

价值：

- 降低自然语言问法和文档表述不完全一致带来的损失
- 提高短问题、口语问题、歧义问题的召回稳定性

---

### 3. Metadata Filter

目标：

- 在检索时结合 metadata 做过滤，而不是所有 chunk 一起混搜

建议后续考虑的过滤维度：

- `document_id`
- `file_type`
- `heading_path`
- `source_type`
- 权限组 / 可见范围

价值：

- 减少跨文档误召回
- 为企业 RAG 的权限控制做准备
- 让检索范围更可控

典型例子：

```text
只在采购制度相关文档中查
只查 PDF 制度原文
只查某份文档的 chunk
```

---

### 4. Chunk 粒度再调

目标：

- 继续优化切片大小和边界

当前要继续观察的问题：

- chunk 是否过大，导致一个 chunk 里混了多个子主题
- chunk 是否过碎，导致真正答案被拆散
- overlap 是否足够覆盖上下文
- 标题前缀是否一直保留得合理

后续可调方向：

- `target_chunk_size`
- `max_chunk_size`
- overlap 策略
- table / code / list 的特殊切法

---

## 推荐执行顺序

我建议按这个顺序做：

1. `rerank`
2. `query rewrite`
3. `metadata filter`
4. `chunk 粒度微调`

原因：

- `rerank` 对现有召回质量的直接提升最大
- `query rewrite` 能补用户问法和文档表述之间的落差
- `metadata filter` 更偏控制能力和企业场景
- `chunk` 微调应该建立在前面几项已有反馈之后再做

---

## 验收建议

后面做这些优化时，不建议只靠“肉眼觉得更好”。

建议至少固定几类典型问题样本：

- 事实型问题
- 条件触发型问题
- 流程型问题
- 总结型问题

然后对比：

- top1 是否更准
- top3 是否更贴题
- 最终 answer 是否更稳定
- 引用来源是否更合理
