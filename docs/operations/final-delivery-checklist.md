# 最终交付清单

本文用于项目收尾，不再扩展新的 Agent 能力。目标是把当前代码整理成可启动、可演示、可测试、可解释的实习企业项目。

## 收尾顺序

1. 冻结功能范围，只修真实验收发现的缺陷。
2. 从干净 Compose 环境启动 PostgreSQL、Elasticsearch、RabbitMQ、Redis、API、Worker 和前端。
3. 执行 demo seed，记录组织、知识库和测试账号，不把密码提交到 Git。
4. 执行五格式 OSS E2E，记录每个文件的 upload、stage job、document、chunk 和 vector 结果。
5. 用前端完成登录、上传、搜索、Chat、Tool Calling 和人工审核流程。
6. 执行自动化测试、lint 和 production build。
7. 更新验收证据，检查 README、架构图和运行手册没有过时表述。
8. 检查敏感信息和未跟踪文件后，再提交和推送最终版本。

## 验收主线

```text
登录
  -> 选择知识库
  -> OSS 上传 TXT / MD / PDF / DOCX / XLSX
  -> Celery download -> validate -> parse -> split -> embed -> index
  -> PostgreSQL documents / chunks
  -> Elasticsearch vector_id 和混合检索
  -> Chat answer + citations
  -> 基于上一轮 citations 的 native Tool Calling
  -> interrupt / restart / resume
```

## 自动化门禁

```bash
cd backend
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"

cd ../frontend
npm test -- --run
npm run lint
npm run build
```

当前基线（2026-08-06）：后端 `230 passed，5 skipped`，前端 Vitest `9 passed`，lint 和 production build 通过。

## 收尾检查

```text
[ ] README 说明当前真实架构，不再把已完成能力写成未来计划
[ ] system-overview.md 包含上传、检索、Chat、Tool Calling 四条链路
[ ] demo-and-acceptance.md 命令可从头执行
[ ] enterprise-readiness-evidence.md 区分自动化证据和真实环境证据
[ ] backend/.env、OSS key、Qwen key、JWT secret 未进入 Git
[ ] 前端不保存长期敏感凭据
[ ] migration revision 与代码一致
[ ] 真实 E2E 报告不包含 token、secret 或完整 presigned URL
[ ] 已知非目标已写清楚
```

## 不在收尾阶段继续做的事情

```text
Kubernetes 和多地域高可用
完整企业 SSO / OIDC
复杂多 Agent 规划
自动调参和在线 A/B
大规模性能压测平台
完整告警和可视化运维平台
```

这些内容可以作为后续路线，但不应阻塞当前项目交付。
