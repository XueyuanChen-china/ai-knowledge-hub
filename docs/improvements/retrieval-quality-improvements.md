# U8：混合检索、Rerank 与评测

## 目标

此前系统只有 Dense Vector 检索：它擅长理解“怎么报销差旅费”和“员工差旅申请流程”这类同义表达，但对错误码、制度编号、金额门槛、函数名等精确术语不稳定。

U8 将候选检索升级为：

```text
用户问题
  -> Dense top-k（语义相近）
  -> BM25 top-k（关键词/精确术语）
  -> 相同组织 + 相同知识库过滤
  -> RRF 融合与 chunk 去重
  -> BGE reranker 精排
  -> relevance gate
  -> 有证据才进入 Answer Node
```

## 代码职责

| 位置 | 职责 |
| --- | --- |
| `backend/app/services/vector_service.py` | 分别执行 Dense 与 BM25 查询，统一 ES 范围过滤并解析命中。 |
| `backend/app/services/retrieval_service.py` | 调度双路查询、RRF 融合、reranker 降级和检索指标。 |
| `backend/app/services/retrieval/reranker.py` | 定义最小 reranker 协议，支持本地 BGE CrossEncoder。 |
| `backend/app/services/rag_service.py` | 将混合检索结果补全为带标题的 `RetrievedDocument`。 |
| `backend/app/graph/nodes.py` | 使用 rerank score 优先、dense score 兜底的 relevance score。 |
| `backend/app/services/retrieval_evaluation.py` | 评测固定问题集并生成机器可比较的 JSON 报告。 |

## 为什么使用 RRF

Dense 的 cosine score 与 BM25 分数不在同一量纲，不能直接相加。RRF 只使用名次：

```text
RRF(chunk) = sum(1 / (k + rank))
```

同一个 chunk 同时被 Dense 和 BM25 命中时，会累积两次贡献，但只保留一条结果。每条命中会携带：

```json
{
  "retrieval_sources": ["dense", "bm25"],
  "dense_score": 0.82,
  "bm25_score": 12.4,
  "rrf_score": 0.0325,
  "rerank_score": 0.91
}
```

这让前端、日志和排障可以回答“这条证据为什么被召回”。

## 权限边界

Dense kNN 与 BM25 查询都复用：

```text
organization_id
knowledge_base_id
```

过滤发生在 ES 召回阶段，而不是融合或回答后。因此无权 chunk 不会进入 Dense 候选、BM25 候选、RRF 结果或模型上下文。

## Reranker 与降级

配置项：

```dotenv
RETRIEVAL_RERANKER_MODEL_NAME=BAAI/bge-reranker-v2-m3
RETRIEVAL_RERANKER_DEVICE=cpu
RETRIEVAL_RERANK_SCORE_THRESHOLD=0.78
```

U8 默认每次检索都执行 BGE reranker，因为 relevance gate 统一依赖 `rerank_score`。BGE 首次运行需要下载和加载模型，部署时应提前完成模型预热。它会重排 RRF 前 N 条候选；实现会显式读取 CrossEncoder 原始 logit，再归一化为 `0~1` 的 `rerank_score`，避免模型 SDK 默认 Sigmoid 与业务层重复归一化。

如果 reranker 加载或调用失败，系统记录 `retrieval.reranker_fallback` 日志并回退到 RRF，保证故障时仍能返回检索结果；但这类请求不应被视为正常的高置信回答，应通过告警和评测发现。

## Relevance Gate

门禁只保留三类判断：

```text
没有检索结果 -> no_answer / need_review
rerank_score 低于阈值 -> need_review
金额、数字、编号、引号内名称没有在证据中出现 -> need_review
```

普通中文关键词不再做硬性覆盖检查，避免“购复”这类字符滑窗噪声和同义表达被误拒。关键实体检查只保护问题中明确出现的金额、数字、编号、英文产品标识或引号内名称。

RRF 分数只保留在 metadata 中用于解释，不作为门禁分数；正常门禁统一使用 BGE `rerank_score`。

## 评测

固定样本在：

```text
backend/tests/fixtures/retrieval_evaluation/cases.json
```

覆盖：事实型、条件型、流程型、总结型、无答案和越权问题。

在隔离 demo 知识库完成索引后，将 fixture 的 `expected_chunk_ids` 更新为该 demo 数据的稳定 chunk id，再分别运行：

```bash
cd backend

./.venv/bin/python scripts/evaluate_retrieval.py \
  --organization-id <组织ID> \
  --knowledge-base-id <知识库ID> \
  --mode dense \
  --output reports/retrieval/dense-baseline.json

./.venv/bin/python scripts/evaluate_retrieval.py \
  --organization-id <组织ID> \
  --knowledge-base-id <知识库ID> \
  --mode hybrid \
  --output reports/retrieval/hybrid.json
```

报告指标：

- `Recall@K`：有答案问题中，正确 chunk 是否至少出现一次。
- `MRR`：正确 chunk 排得越靠前，分数越高。
- `citation_correctness`：候选结果中命中标注证据的比例；当前 Answer Node 的真实引用还需在 U10 E2E 中进一步核验。
- `no_answer_rejection_rate`：无答案或越权样本没有返回可用候选的比例。

## 验证

```bash
cd backend
./.venv/bin/python -m unittest \
  tests.test_hybrid_retrieval \
  tests.test_reranker \
  tests.test_retrieval_evaluation \
  tests.test_vector_service \
  tests.test_rag_service \
  tests.test_graph_workflow \
  tests.test_search_permissions
```

## 本轮边界

本轮不实现多 reranker provider、在线 A/B、query rewrite、自动调参或通用 plugin registry。query rewrite 只有在固定评测集证明能稳定提升后才考虑开启。

## 公开基准评测

原来的 `cases.json` 主要验证项目业务规则，答案依据绑定到本地数据库的 chunk id，适合回归测试，
不适合作为公开可比较的检索能力基线。U8 收尾增加一个不提交原始语料的公开基准流程：

```text
公开 corpus + queries + qrels
        -> 固定裁剪的小样本
        -> 导入独立测试知识库
        -> 复用现有 Dense / BM25 / RRF / rerank 链路
        -> Recall@K / MRR / nDCG@K / top-k precision
```

第一版默认使用 BEIR SciFact 的 test split。它是英文科学事实检索集，规模适中，带标准 qrels，
适合验证检索排序工程；它不能代表中文企业制度场景，因此不能替代项目自己的中文企业黄金集。

准备 100 条 query 和每条 query 的 20 个随机负例：

```bash
cd backend
./.venv/bin/python scripts/prepare_retrieval_benchmark.py \
  --dataset scifact \
  --split test \
  --query-limit 100 \
  --negative-per-query 20 \
  --output-dir data/retrieval_benchmarks/scifact-mini
```

导入前先在当前组织下创建一个专用测试知识库，并使用有权限的用户执行：

```bash
./.venv/bin/python scripts/import_retrieval_benchmark.py \
  --organization-id <ORG_ID> \
  --knowledge-base-id <BENCHMARK_KB_ID> \
  --created-by-user-id <USER_ID> \
  --dataset BEIR/scifact \
  --source-dir data/retrieval_benchmarks/scifact-mini
```

公开 corpus 已经是 passage 级检索单元，导入脚本不会再次切片。它把稳定的公开 corpus id
写入 chunk metadata 的 `benchmark_doc_id`，评测时按这个 id 对齐 qrels，不依赖 PostgreSQL
自增的 `chunk_id`。

分别执行 Dense baseline 和 U8 hybrid：

```bash
./.venv/bin/python scripts/evaluate_retrieval.py \
  --organization-id <ORG_ID> \
  --knowledge-base-id <BENCHMARK_KB_ID> \
  --mode dense \
  --cases data/retrieval_benchmarks/scifact-mini/cases.json \
  --top-k 5 \
  --output reports/retrieval/scifact-dense.json

./.venv/bin/python scripts/evaluate_retrieval.py \
  --organization-id <ORG_ID> \
  --knowledge-base-id <BENCHMARK_KB_ID> \
  --mode hybrid \
  --cases data/retrieval_benchmarks/scifact-mini/cases.json \
  --top-k 5 \
  --output reports/retrieval/scifact-hybrid.json
```

这里的 `ndcg@K` 使用公开 qrels 的相关性等级；项目原有的 `no_answer` 和 `unauthorized`
样本仍然单独评测。公开基准通常没有“这个组织无权访问”的语义，因此不能用 SciFact
证明权限隔离，也不能用它证明企业问答的拒答质量。

公开数据只作为本地下载产物，已经加入 `.gitignore`。提交到仓库的应是下载脚本、数据集名称、
来源 URL、裁剪参数和压缩包 SHA-256，不应直接提交完整公开语料或未经确认的再分发副本。
