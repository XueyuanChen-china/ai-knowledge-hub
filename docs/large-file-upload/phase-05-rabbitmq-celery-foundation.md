# Phase 05 RabbitMQ Celery Foundation

这份文档对应当前仓库的大文件上传 Phase B，并补充 Phase C 的真实 download 消费入口。

目标不是直接改完整上传流程，而是先跑通最小链路：

```text
FastAPI
  -> Celery
  -> RabbitMQ
  -> Celery Worker
  -> PostgreSQL
```

## 1. 这一阶段落地了什么

新增能力：

- Celery 应用实例：[backend/app/celery_app.py](../../backend/app/celery_app.py)
- Celery hello task：[backend/app/tasks/upload_tasks.py](../../backend/app/tasks/upload_tasks.py)
- 投递服务层：[backend/app/services/upload_celery_service.py](../../backend/app/services/upload_celery_service.py)
- 测试接口：

```http
POST /uploads/processing-jobs/{processing_job_id}/celery/hello
```

- RabbitMQ 本地 Docker 文件：

```text
backend/docker-compose.rabbitmq.yml
```

- 本地脚本：

```bash
bash scripts/start_rabbitmq_local.sh
bash scripts/start_celery_worker.sh
bash scripts/stop_rabbitmq_local.sh
```

## 2. 当前 hello task 做什么

它只做验证，不执行真实业务。

流程是：

```text
调用 FastAPI 测试接口
  -> upload_hello_task.apply_async(...)
  -> PostgreSQL 写入 celery_task_id
  -> RabbitMQ 收到消息
  -> Celery worker 消费消息
  -> worker 把 current_step 更新为 celery_hello_received
```

所以你可以通过数据库看两个关键状态：

```text
celery_task_id 不为空
current_step 从 celery_hello_dispatched 变成 celery_hello_received
```

## 3. 为什么要先做 hello task

完整流水线会涉及：

```text
download
validate
parse
split
embed
index
```

如果一开始就全部迁移，问题会混在一起：

- RabbitMQ 是否连通
- Celery worker 是否启动
- task 是否被消费
- 数据库状态是否正确
- 上传业务是否幂等
- 后续阶段是否重复创建

Phase B 先只验证消息链路本身。

## 4. 本地启动方式

在 `backend` 目录执行：

```bash
bash scripts/start_rabbitmq_local.sh
```

RabbitMQ 管理后台：

```text
http://localhost:15672
```

默认账号：

```text
guest / guest
```

启动 Celery worker：

```bash
bash scripts/start_celery_worker.sh
```

启动 FastAPI：

```bash
uvicorn app.main:app --reload
```

## 5. 测试接口

先通过上传完成流程拿到一个 `processing_job_id`。

然后调用：

```http
POST /uploads/processing-jobs/{processing_job_id}/celery/hello
```

请求体：

```json
{
  "message": "hello from FastAPI"
}
```

返回里应该看到：

```json
{
  "processing_job_id": 1,
  "celery_task_id": "...",
  "queue": "ai_knowledge_hub",
  "status": "pending",
  "current_step": "celery_hello_dispatched",
  "detail": "Celery hello task dispatched"
}
```

worker 消费后，数据库里的 `current_step` 会变成：

```text
celery_hello_received
```

## 6. Phase C：download 阶段真实消费

Phase C 把最小消息链路接到上传 complete：

```text
POST /uploads/{upload_id}/complete
  -> 创建 stage=download 的 upload_processing_job
  -> upload_download_stage_task.apply_async(...)
  -> RabbitMQ
  -> Celery worker
  -> 下载 OSS 原件到本地处理目录
  -> magic number / Office 容器 / SHA256 校验
  -> job.status = completed
```

阶段执行函数位于：

```text
backend/app/services/upload_postprocess_service.py
  run_download_stage_job()
```

本阶段成功后：

- `upload_processing_jobs.status = completed`
- `upload_processing_jobs.current_step = download_completed`
- `upload_processing_jobs.attempt_count += 1`
- `upload_tasks.processing_status = completed`
- `upload_tasks.detected_mime_type` 写入探测结果
- 不创建 `documents`
- 不执行 parse / split / embedding / Elasticsearch index

这里的 `completed` 表示 download 阶段完成，不代表整条文档处理流水线完成。后续阶段会创建新的 stage job，并通过 `depends_on_job_id` 表达依赖。

如果 download 或校验失败，统一进入现有的重试退避逻辑，状态会变成 `retry_scheduled` 或 `failed`。

当前还补充了 RabbitMQ 可靠性基础：

- Celery/Kombu 打开 publisher confirm；
- 主队列配置 `ai_knowledge_hub.dlx`；
- 业务重试耗尽后使用 `reject(requeue=False)` 进入 `ai_knowledge_hub.dead`；
- `task_acks_late` 保证任务执行完成后再确认；
- Worker 丢失时保留重新投递机会。

这里仍然只有一套业务重试：PostgreSQL `upload_processing_jobs` 的退避重试。DLX 只负责保存最终失败消息，不再次自动重试。

## 7. Phase C 本地验证

启动 RabbitMQ：

```bash
bash scripts/start_rabbitmq_local.sh
```

启动 Celery worker：

```bash
bash scripts/start_celery_worker.sh
```

启动 FastAPI：

```bash
uvicorn app.main:app --reload
```

完成一个上传后，立即查询：

```http
GET /uploads/{upload_id}
```

可以看到 job 先处于 `pending`，worker 开始执行后进入 `running`，下载和校验成功后变成 `completed`。数据库中还可以通过 `current_step` 区分：

```text
celery_download_dispatched
download_object
download_completed
```

## 8. 这一阶段没有做什么

还没有做：

- validate / parse / split / embed / index 阶段 task
- task 失败重试策略迁移
- Celery 队列按阶段拆分
- dead letter queue
- worker 多进程部署

这些放到后续 Phase D。
