# Phase D：完整上传处理流水线

## 目标

让上传完成后的处理流程真正拆成阶段级 job：

```text
download
  -> validate
  -> parse
  -> split
  -> embed
  -> index
```

每个阶段都有自己的 PostgreSQL 记录和 Celery task。前一个阶段成功后，才创建并投递下一个阶段。

## 数据库中的 job 链

同一个 `upload_task_id` 下会有 6 条记录：

| stage | depends_on_job_id | 主要职责 |
| --- | --- | --- |
| download | null | 从 OSS 下载原件到本地处理目录 |
| validate | download job id | 校验文件头、Office 容器和 SHA256 |
| parse | validate job id | 提取文本并创建 `documents` |
| split | parse job id | 创建 KnowledgeItem 和 PostgreSQL chunks |
| embed | split job id | 生成 BGE-M3 embedding |
| index | embed job id | 写入 Elasticsearch，更新 vector_id |

`depends_on_job_id` 不是为了让数据库自动执行任务，而是记录阶段之间的因果关系，方便查询、重试和审计。

## 关键代码路径

### 阶段创建和投递

[upload_postprocess_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/upload_postprocess_service.py) 的 `advance_pipeline_after_stage()`：

- 根据当前 stage 找到下一个 stage
- 创建新的 `UploadProcessingJob`
- 设置 `depends_on_job_id`
- 调用 Celery 派发器

[upload_celery_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/upload_celery_service.py) 的 `dispatch_upload_stage_job()`：

- 根据 `job.stage` 选择对应 Celery task
- 投递到 RabbitMQ
- 保存 `celery_task_id`

### 阶段执行

[upload_tasks.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/tasks/upload_tasks.py) 注册了：

```text
uploads.download
uploads.validate
uploads.parse
uploads.split
uploads.embed
uploads.index
```

后五个 task 统一进入 `run_pipeline_stage_job()`，但每个 task 仍然是独立的消息和独立的数据库 job。

## Embedding 为什么需要暂存

Embedding 结果不能只放在 Python 进程内存里，因为：

- embed worker 和 index worker 可能是不同进程
- embed 完成后进程可能退出
- index 失败重试时不能重新依赖原来的内存变量

当前实现把向量暂存在 `chunks.embedding_json`：

```text
embed stage
  -> 生成向量
  -> 写入 embedding_json

index stage
  -> 读取 embedding_json
  -> 写 Elasticsearch
  -> 写 vector_id
  -> 清空 embedding_json
```

这是为了先把阶段边界跑通的过渡方案。生产环境更适合使用独立的 embedding artifact 存储或专用向量任务结果表，并设置保留和清理策略。

## 状态流转

每个阶段自己的状态都遵循：

```text
pending
  -> running
  -> completed
```

失败时：

```text
running
  -> retry_scheduled
  -> running
  -> completed
```

超过最大重试次数后变成 `failed`。当前失败逻辑复用已有的退避策略，并保留 `error_message / retry_count / attempt_count`。

## 最终验收

启动 RabbitMQ、Celery worker 和 FastAPI 后，完成一次 OSS 上传：

```bash
bash scripts/start_rabbitmq_local.sh
bash scripts/start_celery_worker.sh
uvicorn app.main:app --reload
```

数据库最终应能看到：

```text
6 个 stage job
每个 job status=completed
每个后续 job 的 depends_on_job_id 指向前一个 job
documents.status=indexed
chunks.vector_id 不为空
chunks.embedding_json 已清空
```

## 当前边界

已经完成：

- 阶段级 job 链
- Celery task 链式投递
- PostgreSQL 状态追踪
- 失败重试状态
- 最终文档索引

后续还可以继续增强：

- 每个阶段使用独立 Celery queue 和并发池
- task 幂等键和重复消息保护
- 死信队列和告警
- embedding artifact 的专用存储
- 任务进度百分比和前端可视化
