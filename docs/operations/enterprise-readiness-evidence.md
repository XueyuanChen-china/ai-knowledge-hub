# 企业化验收证据

本页是 U10 的证据目录，不把“代码存在”当作“能力已验收”。每项证据应包含命令、时间、环境、结果和失败说明。

## 当前证据

| 能力 | 证据位置 | 状态 |
| --- | --- | --- |
| 多格式 parser / block / chunk | `backend/tests/test_multiformat_e2e_fixtures.py`、fixture expected | 已自动化 |
| OSS -> Celery -> PostgreSQL -> Elasticsearch | `backend/scripts/test_multiformat_e2e.py` | 真实环境可复现 |
| Dense + BM25 + RRF + rerank | `backend/tests/test_hybrid_retrieval.py`、retrieval report | 已自动化 |
| JWT/RBAC/跨组织边界 | `backend/tests/test_resource_authorization.py`、U10 E2E | 单测已覆盖，真实负向需执行 |
| SSE / interrupt / resume | `backend/tests/test_chat_api.py`、`test_graph_checkpoint_persistence.py` | 已自动化 |
| 前端 lint/build/unit/browser | `frontend/tests/`、`frontend/playwright.config.ts` | 已自动化 |
| live/ready、JSON 日志、metrics | `backend/tests/test_health_api.py`、`test_observability.py` | 已自动化 |

## U10 运行记录模板

```text
执行日期：
代码版本：
数据库 migration revision：
测试环境：Compose / 本地进程 / CI
知识库 ID：
多格式报告：
检索报告：
后端测试：
前端 lint/build/unit/e2e：
权限负向结果：
重启恢复结果：
已知失败和解释：
```

## 结果判定

- happy path：五格式完成 indexed、chunks、vector_id、search 和 citations；
- security path：401、403、跨组织资源和 viewer 写操作均拒绝；
- recovery path：checkpoint 可跨 graph/API 实例恢复，消息不重复；
- engineering path：README 和 operations 文档中的命令能在干净环境复现。

## 已知非目标

本项目当前不宣称完成 Kubernetes、多地域容灾、企业 SSO、计费、RabbitMQ/Elasticsearch 高可用、全自动告警平台和大规模性能压测。它的企业化证据集中在数据链路、权限边界、可恢复工作流、检索质量和可重复交付。
