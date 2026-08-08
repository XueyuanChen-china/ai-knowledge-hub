# Demo 与最终验收

本文是从干净环境验证项目的操作合同。命令默认在仓库根目录执行。

## 1. 启动

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/health/live
```

确认 PostgreSQL、Elasticsearch、RabbitMQ、Redis、backend、worker 和 frontend 已运行。首次启动由 backend 执行 Alembic；worker 只检查 revision，不执行 migration。

## 2. 创建演示数据

```bash
cd backend
./.venv/bin/python scripts/seed_demo_environment.py --password 'U10-Demo-Only-Change-Me!'
```

脚本输出 `organization_id`、`knowledge_base_id` 和四个角色账号：owner、admin、editor、viewer。账号只用于本地演示，密码不能写入 Git、日志或 CI。

## 3. 真实多格式 E2E

先用 demo owner 登录获取短期 access token，然后执行：

```bash
export E2E_ACCESS_TOKEN='短期 access token'
export E2E_BASE_URL='http://127.0.0.1:8000'

./.venv/bin/python scripts/test_multiformat_e2e.py \
  --base-url "$E2E_BASE_URL" \
  --knowledge-base-id <knowledge_base_id> \
  --access-token "$E2E_ACCESS_TOKEN" \
  --report data/retrieval_benchmarks/multiformat-e2e-u10.json
```

也可以让自动化 happy path 自己调用登录接口：

```bash
export E2E_EMAIL='owner@u10-demo.invalid'
export E2E_PASSWORD='U10-Demo-Only-Change-Me!'
unset E2E_ACCESS_TOKEN
```

它会上传 TXT、MD、PDF、DOCX、XLSX，等待 Celery 阶段完成，检查 document、knowledge item、PostgreSQL chunks、vector_id 和搜索命中。真实运行需要 OSS、RabbitMQ、Elasticsearch、BGE 模型和有效的应用 token。

如果只想验证前端而不重新执行五格式上传，可以使用已经 indexed 的知识库进入：

```text
登录 -> 知识库 -> 语义搜索 -> 专家问答
```

推荐 Chat 问题：

```text
采购复核的触发条件是什么？
展开刚才命中的供应商制度原文看看。
这个 chunk 前后还有什么内容？
```

第二个问题应读取上一轮 citations 并进入 `tool` 路由；第三个问题应调用 `get_chunk_neighbors`，而不是重新执行完整 Dense/BM25 检索。

## 4. 自动化门禁

```bash
export RUN_ENTERPRISE_E2E=1
export E2E_ACCESS_TOKEN='短期 access token'
export E2E_KNOWLEDGE_BASE_ID='<knowledge_base_id>'
cd backend
./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

未设置 `RUN_ENTERPRISE_E2E=1` 时，外部依赖测试会明确 skip，不会让普通单测隐式访问个人 OSS 或 Qwen。

## 5. 闭环检查清单

```text
[ ] 登录成功并能访问受保护 API
[ ] 五种文件全部 indexed
[ ] documents.status = indexed
[ ] PostgreSQL chunks 数量大于 0，vector_id 不为空
[ ] Elasticsearch 能按知识库检索
[ ] 搜索结果包含预期文档
[ ] Chat 返回 answer 或 human review
[ ] answer 有 citations
[ ] viewer 写操作返回 403
[ ] 未登录请求返回 401
[ ] 跨组织资源按 404/403 拒绝
[ ] interrupt 后重启 API/worker，resume 仍能继续
```

## 6. 测试失败定位

```bash
docker compose logs --tail=200 backend
docker compose logs --tail=200 worker
docker compose exec rabbitmq rabbitmqctl list_queues name messages arguments
curl http://127.0.0.1:8000/health/ready
```

先根据 `upload_id` 找 PostgreSQL 的 UploadTask 和 stage job，再根据 `document_id` 检查 documents/chunks，最后用 `vector_id` 在 Elasticsearch 查询。不要只根据前端的 500 判断失败位置。

## 7. 验收记录要求

每次真实验收至少记录：

```text
代码版本或 commit
migration revision
knowledge_base_id
五种文件的 document_id、chunk_count 和最终状态
检索报告路径
Chat route、tool_planner_mode 和 citations
权限负向结果
interrupt/restart/resume 结果
失败项及原因
```

不要把 access token、OSS 预签名 URL、Qwen API Key 或完整请求正文写入报告。
