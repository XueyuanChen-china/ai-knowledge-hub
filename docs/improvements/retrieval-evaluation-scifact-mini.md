# U8 公开检索基准评测记录

## 评测目的

原有评测集用于验证项目业务规则，但相关 chunk id 来自本地数据库，规模较小，不能作为检索算法的独立基线。
本次增加 BEIR SciFact 的固定小样本，用来比较 Dense-only 与 BM25 + Dense + RRF 的排序效果。

这不是企业知识库业务质量的最终结论。SciFact 是英文科学事实检索集，不能替代中文制度、金额、流程、无答案和权限黄金集。

## 数据版本

```text
dataset: BEIR/scifact
split: test
queries: 100
corpus passages: 1,730
negative passages per query: 20
random seed: 20260804
archive sha256: 536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165
knowledge_base_id: 9
top_k: 5
```

数据由 `backend/scripts/prepare_retrieval_benchmark.py` 下载并裁剪，原始语料放在本地 `backend/data/retrieval_benchmarks/`，不提交到 Git。

## 结果

| 模式 | Recall@5 | MRR | nDCG@5 | top-k precision approximation |
| --- | ---: | ---: | ---: | ---: |
| Dense-only | 0.8200 | 0.7093 | 0.7251 | 0.1840 |
| BM25 + Dense + RRF | 0.9100 | 0.7898 | 0.7985 | 0.1960 |

相对 Dense-only，RRF 在这批样本上的变化为：

```text
Recall@5: +0.0900
MRR:      +0.0805
nDCG@5:   +0.0734
```

这说明 BM25 对精确术语和 lexical match 的补强在这批数据上有效，但不能据此宣称所有企业场景都会提升。

## 如何复现

```bash
cd backend

./.venv/bin/python scripts/prepare_retrieval_benchmark.py \
  --dataset scifact \
  --split test \
  --query-limit 100 \
  --negative-per-query 20 \
  --output-dir data/retrieval_benchmarks/scifact-mini

./.venv/bin/python scripts/import_retrieval_benchmark.py \
  --organization-id 1 \
  --knowledge-base-id 9 \
  --created-by-user-id 1 \
  --dataset BEIR/scifact \
  --source-dir data/retrieval_benchmarks/scifact-mini

./.venv/bin/python scripts/evaluate_retrieval.py \
  --organization-id 1 \
  --knowledge-base-id 9 \
  --mode dense \
  --cases data/retrieval_benchmarks/scifact-mini/cases.json \
  --top-k 5 \
  --output data/retrieval_benchmarks/scifact-mini/report-dense.json

./.venv/bin/python scripts/evaluate_retrieval.py \
  --organization-id 1 \
  --knowledge-base-id 9 \
  --mode rrf \
  --cases data/retrieval_benchmarks/scifact-mini/cases.json \
  --top-k 5 \
  --output data/retrieval_benchmarks/scifact-mini/report-rrf.json
```

`rrf` 是消融基线，只执行 Dense + BM25 + RRF，不加载 reranker。生产默认仍然执行 BGE reranker。
当前机器尚未完整下载 `BAAI/bge-reranker-v2-m3`，因此本次没有伪造 rerank 结果；模型下载完成后，
再用 `--mode hybrid` 生成真正的 rerank 报告。

## 指标解释

- `Recall@5`：前 5 条中是否至少包含一个 qrels 标注的相关 passage。
- `MRR`：第一个相关 passage 越靠前，得分越高。
- `nDCG@5`：同时考虑多个相关 passage 和它们的相关性等级，衡量整体排序质量。
- `top-k precision approximation`：前 K 条中属于 qrels 相关集合的比例，只是检索候选精度近似，不等于最终 LLM 引用正确率。

## 业务黄金集仍然保留

SciFact 没有覆盖以下项目约束，因此仍需运行原有 `cases.json`：

- 中文制度和流程问题；
- 金额、制度编号、产品名等关键实体；
- 无答案时的拒答和人工审核；
- 组织、知识库和文档权限过滤；
- 最终回答引用与真实证据的一致性。

U8 的正式结论应同时引用公开基准和业务黄金集，而不是只报告公开数据集分数。
