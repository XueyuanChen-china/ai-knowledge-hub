# 09 高频面试问题与参考回答

使用方式：先遮住答案口述 1 分钟，再对照。不要逐字背诵，必须能结合当前项目继续展开。

## 一、项目与架构

### Q1：你的项目解决什么问题？

企业内部文档格式分散、搜索不准且存在权限边界。系统把文件上传、解析、切分、索引、检索和 Agent 问答串成可恢复流程，并为答案提供引用和人工审核。

### Q2：为什么用了这么多中间件，会不会过度设计？

每个组件对应明确职责：PostgreSQL 管业务事实，OSS 管大对象，ES 管搜索，RabbitMQ/Celery 管长任务，Redis 管短期 token 撤销。对个人 Demo 确实偏重，但项目目标是展示企业链路；同时用 Compose 降低本地运行成本。更小场景可以删除 MQ、OSS 或 ES。

### Q3：系统的单一事实来源是什么？

业务资源与状态以 PostgreSQL 为准；OSS 是原件，ES 是可重建的派生索引，RabbitMQ 是传递通道，checkpoint 是工作流执行状态。

### Q4：项目最核心的 trade-off？

可靠性和复杂度之间。异步流水线、权限贯穿和持久 checkpoint 提升可靠性，但增加状态协调、部署依赖和调试成本，因此通过明确状态机、migration、E2E 和可观测性控制复杂度。

## 二、数据库与权限

### Q5：为什么从 SQLite 换 PostgreSQL？

需要真实并发事务、行锁、连接池、多个 API/Worker 共享数据、Alembic migration 和生产接近度。SQLite 适合早期单机开发，不适合当前异步多进程链路。

### Q6：为什么 `create_all` 不够？

它不表达 schema 演进历史、数据回填、降级和团队环境一致性。Alembic migration 可审查、可追踪、可在 CI 验证。

### Q7：如何防止跨组织越权？

认证得到 principal，动作经过 RBAC，所有 PostgreSQL ID 查询带 organization 条件，ES 两路召回带组织过滤，OSS presign 重新校验任务归属，而不是只在前端隐藏按钮。

### Q8：JWT 退出登录为什么不能只删前端 token？

前端删除只影响当前浏览器，泄露的 JWT 在过期前仍有效。因此当前 `jti` 写 Redis 黑名单；禁用账号或退出全部设备通过 `token_version` 批量作废旧 token。

## 三、上传、MQ 与并发

### Q9：为什么使用预签名 URL？

让客户端在限定时间、方法、对象路径下直传 OSS，降低 API 带宽和连接压力，同时服务端保留授权控制，不暴露 AccessKey Secret。

### Q10：MQ 如何保证消息绝对不丢且只消费一次？

分布式系统通常难同时保证绝对 exactly-once。采用 durable queue、ACK、重投和数据库幂等实现 at-least-once；业务依靠唯一约束和状态检查抵抗重复。数据库与 MQ 双写可进一步用 Outbox。

### Q11：为什么 Embedding Worker 并发设为 1？

BGE-M3 模型较大，多 prefork 子进程可能各加载一份，造成内存交换和更慢。独立队列与低并发只限制 Embedding，不影响 download/parse 等普通任务吞吐。

### Q12：线程和进程怎么选？

I/O 等待多可用线程或 async；CPU 密集且受 GIL 影响用多进程；模型推理还要考虑模型内存副本和底层库自身线程。选择依据是测量资源瓶颈，不是固定规则。

### Q13：任务执行到一半 Worker 崩了怎么办？

消息未 ACK 可重投，数据库 job 保存 stage 和 attempt。重新执行前检查状态和稳定业务键，已完成阶段不重复产生数据；重试超限后失败或进入 DLQ，用 job ID 和 trace ID 定位。

## 四、切分与检索

### Q14：为什么要 Section、Block、Chunk 三层？

Section 表示章节上下文，Block 表示不可随意破坏的结构单元，Chunk 是受大小约束的检索单元。分层后 parser 和组装器解耦，能保留表格、代码和标题边界。

### Q15：chunk size 为什么是 850/1000？

这是当前字符预算的工程初值，不是通用最优。target 指组装目标，max 是上限；短完整章节不强行补满。最终应通过 Recall/MRR、引用质量和 token 成本共同调整。

### Q16：BM25 和向量检索分别解决什么？

BM25 擅长词面精确匹配、编号和专有词；Dense 擅长语义和同义表达。企业资料两类问题都存在，所以用 RRF 融合。

### Q17：为什么 RRF 不直接加两个 score？

BM25 和向量分数量纲、范围不同，直接相加需要校准。RRF 只依赖排名，简单稳定，并让双路都靠前的结果得到更高分。

### Q18：召回和精排有什么区别？

召回面对全库，要求快，返回几十个候选；reranker 对少量候选做 query-document 交叉编码，计算更贵但判断更细。不能对百万 chunk 逐条 rerank。

### Q19：检索“准确率”如何衡量？

不能只报一个 accuracy。召回用 Recall@K、MRR、nDCG；无答案看拒答率；最终问答看 citation correctness/faithfulness；系统还看 P50/P95 延迟。评估必须说明样本规模和标注方式。

## 五、RAG 与 Agent

### Q20：什么问题走 direct，什么走 RAG？

寒暄和通用概念走 direct；依赖企业文档事实的问题走 RAG，复杂总结也暂时复用 RAG；明确要求展开上一轮资源走 tool。Router 用 LLM 结构化分类并有规则兜底。

### Q21：为什么要 relevance gate？

检索总能返回相对最相似结果，但相对最相似不等于足以回答。门禁根据候选、rerank 和关键实体覆盖决定回答、拒答或转人工，防止弱相关文档被模型组织成看似可信的答案。

### Q22：Checkpoint 和聊天记录有什么区别？

聊天记录是产品业务历史；checkpoint 是节点执行快照。消息不能让图从任意节点恢复，checkpoint 也不是完整可展示对话。

### Q23：为什么需要 human-in-the-loop？

高风险企业问题在证据不足时需要可控降级。系统暂停并提交候选证据给审核人，批准后继续 Answer，拒绝则结束，且可跨进程恢复。

### Q24：Tool Calling 如何保证安全？

只开放白名单只读工具；参数由 Pydantic 校验；调用前做 permission 与资源归属校验；限制次数；记录审计；模型不能直接执行 SQL、HTTP、Shell 或任意文件操作。

### Q25：上下文太长怎么处理？

完整历史保存在数据库，本次 prompt 使用预算化 ContextPack：系统约束、持久记忆、结构化摘要、最近消息、相关历史、证据和工具结果分别分配预算；按优先级整体移除单元，并记录被省略内容。

## 六、前端与运维

### Q26：为什么换成 Vite 而不是 Next.js？

系统是登录后的工作台，不需要 SSR 和 SEO；FastAPI 已承担后端。Vite 架构更简单、构建快，保留 React、TypeScript 和 Mantine 能力。

### Q27：为什么 SSE 解析要 buffer？

TCP/HTTP 网络 chunk 与 SSE 事件边界无关，一次 read 可能只有半条事件或包含多条。前端必须累计到 `\n\n` 再解析，剩余半条继续保留。

### Q28：live 和 ready 为什么分开？

live 判断进程是否活着；ready 判断是否可以接流量。数据库断开不代表进程死了，重启可能无效，但应该把实例从负载均衡摘掉。

### Q29：怎么定位一个上传卡在 pending？

从 upload ID 查 upload task 和各 stage job，沿 trace ID 查 API/Celery JSON 日志，再看 RabbitMQ queue/DLQ、Worker、attempt/next_run_at、OSS 对象和 ES 索引。

### Q30：如果并发量增长十倍，优先优化哪里？

先用 metrics 和压测定位。可能措施包括 API/Worker 横向扩容、阶段队列隔离、Embedding batching/GPU 服务化、ES 查询调优、连接池和限流。不能未测量就盲目扩容。

## 七、反向检查

面试前必须能回答：

- 你亲自解决过哪三个真实 Bug？
- 哪个设计最初不合理，后来为什么改？
- 哪些数据可重建，哪些绝不能丢？
- 哪个性能数字来自真实测试？
- 当前最可能的生产事故是什么？
- 如果只能删掉一个中间件，会删哪个，代价是什么？
