# 05 Elasticsearch、混合检索与 Rerank

## 一、完整检索链路

```text
原始问题 + 可选 rewrite queries
  -> Dense top-k
  -> BM25 top-k
  -> permission / metadata filters
  -> RRF 融合与 chunk 去重
  -> BGE reranker
  -> relevance gate
  -> context pack
```

## 二、Dense 检索

BGE-M3 把 query 和 chunk 编码为向量。语义相近文本在向量空间中距离更近，Elasticsearch kNN 返回相似候选。

优点：能处理同义表达，例如“差旅费用怎么申请”与“员工报销流程”。

局限：精确编号、专有名词和数字不一定稳定；Embedding 模型较大，加载慢且占内存。

## 三、BM25 与倒排索引

正向关系是：

```text
chunk 5 -> [采购, 复核, 金额, 委员会]
```

倒排索引把它反过来：

```text
采购 -> chunk 1, chunk 5, chunk 9
复核 -> chunk 2, chunk 5
R-001 -> chunk 8
```

查询文本先经过 analyzer 分词，再根据倒排表定位候选。BM25 大致考虑：

- 词在当前 chunk 出现次数；
- 词在全库是否稀有，越稀有通常区分度越高；
- chunk 长度，避免长文仅靠包含词多占便宜；
- 可调参数 `k1` 和 `b`。

BM25 分数与余弦相似度量纲不同，不能直接相加。

## 四、RRF 为什么适合融合

Reciprocal Rank Fusion 只使用每路排名：

```text
contribution = 1 / (rrf_k + rank)
```

同一个 chunk 同时在 Dense 和 BM25 排名前列时，会累加贡献。这样不需要校准两路原始分数。

项目按 `vector_id` 或 `chunk_id` 去重，并保留：

```text
dense_score
bm25_score
rrf_score
retrieval_sources
```

RRF 稳定、可解释，但它只看名次，不理解 query 和文本的细粒度交互，因此后面仍需要 rerank。

## 五、BGE Reranker 原理

Embedding 是双塔：query 和 document 分别编码，可提前存文档向量，召回快。

Reranker 是交叉编码：把 `(query, chunk)` 一起输入模型，让注意力直接比较两段文本，输出相关分。它更准确但无法对全库逐条计算，所以只精排融合后的少量候选。

```text
数万/百万 chunks
  -> Dense/BM25 召回几十条
  -> Reranker 精排 top_n
  -> 返回 top_k
```

当前实现若 reranker 异常会记录告警并降级为 RRF，避免整个检索不可用。业务逻辑按默认启用 rerank 设计，但基础召回仍保留降级能力。

## 六、`score` 到底表示什么

`SemanticSearchHit.score` 表示当前排序阶段的主分数：

- Dense 阶段是 dense score；
- BM25 阶段是 BM25 score；
- RRF 后是 RRF score；
- rerank 后是归一化 rerank score。

metadata 中保留各阶段原始分数用于解释和调试。不能把 `0.8` 的向量分数和 `12.4` 的 BM25 分数直接比较。

## 七、权限过滤为什么必须在召回内执行

错误方式：先取全局 top 5，再在应用层删掉无权结果。这样有权结果可能排在第 6 到 10 位，最终召回不足，而且无权内容已经进入服务内存和日志风险区。

正确方式：Dense kNN 和 BM25 都在 ES query 内加入 `organization_id`、`knowledge_base_id` 等 filter，让无权 chunk 根本不进入候选集。

## 八、Relevance Gate

门禁不是“只要有搜索结果就回答”。当前主要判断：

1. 是否有候选；
2. rerank score 是否达到评估阈值；
3. 证据是否覆盖关键数字、金额、制度编号等精确实体；
4. 证据不足时进入 no-answer 或 human review。

普通关键词是否原样出现只能作为辅助信号，因为同义表达可能正确但没有字面重合。BM25 和 reranker 已经承担大部分一般相关性判断。

## 九、检索评估

| 指标 | 含义 |
| --- | --- |
| Recall@K | 正确文档/Chunk 是否出现在前 K 条 |
| MRR | 第一个正确结果排名的倒数均值 |
| nDCG@K | 考虑多级相关性和排名位置 |
| No-answer rejection | 无答案问题被正确拒答的比例 |
| Citation correctness | 最终引用是否真正支持答案 |
| P50/P95 latency | 50%/95% 请求不超过的延迟 |

P95 远高于 P50 通常说明存在长尾：模型首次加载、网络波动、ES 慢查询或并发资源争用。评估报告要固定数据集、配置、模型版本和机器环境，否则数字不可比较。

## 十、常见追问

### 为什么不用 Elasticsearch 原生混合排序？

第一版应用层 RRF 更透明、容易写确定性单测，也便于保留每路证据。数据量和延迟压力更大时，可以评估 ES 原生能力，减少网络往返。

### Query Rewrite 会不会降低准确率？

会。改写可能偏离原意，因此原始 query 永远参与检索，rewrite 只扩召回，最终 reranker 仍以原问题精排，并限制变体数量。

### 如何优化检索效果？

先分类错误：解析错误、切分错误、召回漏失、融合排序错误、rerank 错误、门禁错误。对应调整 parser/chunk、candidate k、analyzer、RRF、reranker 和阈值，不能只盲目调 top_k。

## 十一、关键代码

- [ES 向量和 BM25](../../backend/app/services/vector_service.py)
- [RRF 主流程](../../backend/app/services/retrieval_service.py)
- [BGE Reranker](../../backend/app/services/retrieval/reranker.py)
- [检索评估](../../backend/app/services/retrieval_evaluation.py)
- [RAG 文档转换](../../backend/app/services/rag_service.py)
- [检索质量说明](../improvements/retrieval-quality-improvements.md)

