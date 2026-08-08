# U10：最终 E2E、运行手册与项目证据

## 这阶段解决什么

前面的 U 单元验证的是局部能力：parser、上传、权限、检索、LangGraph、前端。U10 把这些能力组合成一份可重复的验收合同，回答“从用户上传一个文件到得到带引用的回答，系统是否真的闭环”。

## 三类测试

1. **Happy path**：登录 -> OSS multipart -> Celery 阶段 -> PostgreSQL documents/chunks -> Elasticsearch -> search -> Chat/citations。
2. **Security path**：未登录、viewer 写操作、跨组织知识库、越权 presign 和 ES 过滤都必须拒绝。
3. **Recovery path**：人工审核 interrupt 后重建 graph/API，再用同一个 thread_id resume；不能重复消息。

## 为什么 E2E 脚本和 unittest 同时存在

- `scripts/test_multiformat_e2e.py` 是操作员直接执行的真实 OSS 脚本，适合看 upload_id、stage、document_id、chunk_count 和检索报告。
- `tests/e2e/test_*.py` 是测试门禁，默认 skip 外部依赖；设置 `RUN_ENTERPRISE_E2E=1` 后才调用真实环境。
- parser 快照测试不替代 E2E；它验证结构边界，E2E 验证跨服务链路。

## Demo seed 的原则

`seed_demo_environment.py` 是显式命令，不在应用 startup 自动执行。它只创建演示组织、四种角色和知识库，不创建 OSS 对象，也不调用模型。这样可以反复准备权限测试，又不会把 demo 密码混入生产启动。

## 完成标准

只有以下内容同时满足，U10 才算完成：

- 五种文件的 manifest、解析断言和真实上传索引报告存在；
- PostgreSQL 与 Elasticsearch 的可追溯关系可核对；
- 搜索和 Chat 的引用来自正确文档；
- no-answer 不基于弱证据硬答；
- 401/403/跨组织负向测试通过；
- interrupt/resume 可跨重启恢复；
- README、架构、备份恢复和验收手册与代码一致。

## 学习重点

- E2E 的价值是验证系统边界之间的契约，不是简单把多个单元测试串起来。
- 业务事实保存在 PostgreSQL，OSS、ES、RabbitMQ 和 checkpoint 各自承担不同职责。
- CI 应使用 fake/mock 隔离外部凭据，真实 OSS/Qwen 测试作为受控演示环境。
- 失败定位要沿 `request_id -> upload_id -> stage job -> document_id -> chunk_id -> vector_id` 链路追踪。
