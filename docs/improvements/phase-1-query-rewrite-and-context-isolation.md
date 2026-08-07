# Phase 1：查询改写与初步上下文隔离

## 1. 这次解决什么问题

用户在连续对话中经常会问：

```text
上一轮：差旅报销流程是什么？
这一轮：这个流程多久完成？
```

如果只把第二个问题直接送入检索，“这个流程”缺少明确实体，BM25 和向量检索都可能召回不稳定。第一阶段增加了按需 Query Rewrite，并把 Router、Rewrite、Answer 使用的上下文范围分开。

## 2. 实际链路

```text
用户问题
  -> Router 判断 direct / rag / complex
  -> RAG 路线进入 query_rewrite
  -> 规则判断是否需要改写
  -> 必要时调用 Qwen 生成 1~3 个补充查询
  -> 原始 query + 补充 query 分别做 Dense / BM25 检索
  -> RRF 去重、rerank、relevance gate
  -> Answer 只接收当前检索证据和受控会话上下文
```

原始问题永远保留，不会被改写结果替换。改写服务失败、超时或返回非法 JSON 时，检索自动退回原始问题。

## 3. 为什么先用规则判断

不是每个问题都值得调用一次 LLM。当前规则主要识别：

- 指代词：`这个`、`它`、`刚才`、`该流程`；
- 过短问题：例如“多久？”、“谁负责？”；
- 依赖上下文的表达：例如“继续说”“还有吗”“怎么处理”。

没有历史消息时，即使问题出现指代词，也不会调用 LLM，因为没有足够上下文可以安全补全。自包含问题，例如“采购复核的触发条件是什么？”，默认只使用原始 query。

规则只回答“要不要尝试改写”，不负责自己拼接事实。真正的补充查询由 Qwen 生成，并通过 JSON 解析、数量限制和长度限制做校验。

## 4. 改写查询如何进入检索

例如：

```text
原始 query：这个流程多久完成？
补充 query：差旅报销流程多久完成？
```

两者都会进入 Dense 和 BM25 检索。后续仍然统一经过 RRF、去重和 rerank。这样做有两个保护：

1. 改写正确时，可以补足指代信息；
2. 改写错误时，原始 query 仍然存在，不会完全依赖 LLM。

检索服务内部会按 query 去重，候选 chunk 在融合时按 chunk ID 去重。

## 5. 上下文隔离怎么做

当前对话历史仍然完整保存在 PostgreSQL 的 `messages` 表中；隔离只发生在“本次发送给模型的上下文”，不会删除业务历史。

| 上下文 | 当前范围 | 用途 |
| --- | --- | --- |
| `router_context` | 最近 2 条消息 | 帮 Router 判断当前问题路线 |
| `rewrite_context` | 最近 6 条消息 | 补全指代和生成检索查询 |
| `answer_context` | 最近 6 条消息 + 当前检索证据 | 生成最终答案 |

三者是不同的 state 字段。它们不是三个独立的 LangGraph 图，而是同一张图中不同节点读取不同字段，避免把完整历史和检索结果无差别地传给每个模型。

```text
PostgreSQL messages
       |
       +--> router_context  --> Router
       +--> rewrite_context --> Query Rewrite
       +--> answer_context  --> Answer
```

LangGraph checkpoint 保存的是这次工作流的执行状态；PostgreSQL `messages` 保存用户可见的业务消息。两者职责仍然分开。

## 6. 当前还没有做什么

这只是初步上下文隔离，不是完整 Context Manager。当前还没有：

- 基于 tokenizer 的真实 Token 预算；
- 长会话摘要表和摘要版本；
- 过长检索证据的智能压缩；
- 工具输出裁剪；
- 按消息重要性排序的上下文选择。

这些属于后续 Context Management 阶段。当前实现先建立字段边界和可测试的调用链，避免一次引入过多复杂度。

## 7. 主要代码入口

- `backend/app/services/query_rewrite_service.py`：规则门控、Qwen 改写和 JSON 校验。
- `backend/app/graph/nodes.py`：`query_rewrite_node`，以及 Router / Retrieve / Answer 节点之间的数据传递。
- `backend/app/graph/langgraph_workflow.py`：LangGraph 中 `router -> query_rewrite -> retrieve` 的连线。
- `backend/app/api/chat.py`：从历史消息构造三类上下文。
- `backend/app/services/retrieval_service.py`：执行原始 query 与补充 query 的混合检索。
- `backend/app/services/llm_router_service.py`、`llm_answer_service.py`：分别消费 Router 和 Answer 上下文。

## 8. 验证方式

```bash
cd backend
./.venv/bin/python -m unittest tests.test_query_rewrite tests.test_hybrid_retrieval
./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

全量测试还验证了旧的 Chat、LangGraph、检索、权限和迁移行为没有被这次改动破坏。
