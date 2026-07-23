# 当前项目 RabbitMQ + Celery 代码链路

## 1. 当前架构

```text
FastAPI
  -> upload complete
  -> PostgreSQL 创建 upload_processing_jobs(stage=download)
  -> Celery apply_async()
  -> RabbitMQ
  -> Celery Worker
  -> upload_download_stage_task(job_id)
  -> PostgreSQL 更新 job
  -> 创建 validate job 并继续投递
```

后续阶段：

```text
download -> validate -> parse -> split -> embed -> index
```

一份文件的阶段必须按顺序执行，但多份文件可以流水线并发：

```text
文件 A: embed
文件 B: parse
文件 C: download
```

## 2. 发送任务的代码入口

### `upload_celery_service.py`

核心职责：

- 根据 `job.stage` 选择 Celery task；
- 调用 `apply_async()` 投递任务；
- 保存返回的 `celery_task_id`；
- 记录投递审计事件。

业务 job id 和 Celery task id 不一样：

```text
processing_job_id = PostgreSQL 业务任务编号
celery_task_id    = Celery 消息执行编号
```

业务状态必须以 PostgreSQL 为准，因为数据库保存了知识库、文档、阶段依赖和错误信息。

## 3. Celery task 的代码入口

### `tasks/upload_tasks.py`

任务大致形式：

```python
@celery_app.task(name="uploads.download", bind=True)
def upload_download_stage_task(self, job_id: int):
    return run_download_stage_job(
        job_id=job_id,
        celery_task_id=str(self.request.id),
    )
```

这里的 `job_id` 是消息体中的业务参数。

Celery 负责：

- 把 Python 函数包装成可投递任务；
- 将任务消息发送到 RabbitMQ；
- Worker 取出消息并调用函数；
- 提供 task id、重试等执行能力。

## 4. Worker 启动脚本

```text
backend/scripts/start_celery_worker.sh
```

本质上会启动类似：

```bash
celery -A app.celery_app.celery_app worker \
  --loglevel=INFO \
  --pool=prefork
```

`prefork` 通常表示 Celery 预先创建多个子进程来消费任务。它不是 RabbitMQ 创建线程，也不是 FastAPI 创建线程。

## 5. RabbitMQ 启动脚本

```text
backend/scripts/start_rabbitmq_local.sh
```

它启动本地 Docker 容器。配置：

```env
CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
```

这个 URL 的含义：

```text
amqp://用户名:密码@主机:端口/虚拟主机
```

最后的 `//` 表示默认 virtual host `/`。

## 6. 阶段推进

`upload_postprocess_service.py` 中维护阶段映射：

```text
download -> validate
validate  -> parse
parse     -> split
split     -> embed
embed     -> index
index     -> 结束
```

当前阶段成功后：

1. 当前 job 标记 `completed`；
2. 创建下一阶段 job；
3. 下一阶段的 `depends_on_job_id` 指向当前 job；
4. 调用 Celery 投递下一阶段；
5. 更新上传任务的汇总处理状态。

## 7. 失败时怎么处理

### 临时失败

例如 OSS 下载超时：

```text
job running
  -> retry_scheduled
  -> next_run_at = 当前时间 + backoff
  -> 再次投递
```

### 永久失败

例如文件内容不是合法 PDF：

```text
job running -> failed
documents.status = failed
processing_error_message = 具体原因
```

## 8. Publisher confirm 和死信队列

当前项目在 Celery 配置中打开了 RabbitMQ publisher confirm：

```python
broker_transport_options = {
    "confirm_publish": True,
}
```

它解决的是 Producer 侧问题：

```text
FastAPI / Celery Producer
  -> RabbitMQ
  -> Broker 返回确认
```

如果 Broker 没有确认消息，`apply_async()` 会失败，调用方不会把这次投递当作成功。注意：publisher confirm 只能说明 Broker 接收了消息，不能说明 Worker 已经执行成功。

主任务队列还配置了死信交换机：

```text
ai_knowledge_hub
  -> x-dead-letter-exchange = ai_knowledge_hub.dlx
  -> x-dead-letter-routing-key = dead
  -> ai_knowledge_hub.dead
```

当前业务重试耗尽后，任务代码会：

```python
raise Reject(error_message, requeue=False)
```

消息随后由 RabbitMQ 路由到死信队列，方便人工排查。这里的死信队列不是第二套业务重试系统，而是“最终失败消息的隔离区”。

当前项目的重试边界是：

```text
临时错误 -> PostgreSQL job retry_scheduled -> 再投递
重试耗尽 -> job failed -> reject(requeue=False) -> DLX
```

## 9. ACK 配置的含义

当前打开了：

```python
task_acks_late = True
task_reject_on_worker_lost = True
task_acks_on_failure_or_timeout = False
```

含义是任务执行完成后再确认，而不是刚从队列取出就确认。如果 Worker 在执行过程中崩溃，RabbitMQ 还有机会重新投递未确认消息。

这会带来一个要求：业务必须幂等。当前项目使用 PostgreSQL job 状态、阶段依赖、稳定 vector id 和数据库约束共同处理重复执行。

## 10. 为什么不能只看 RabbitMQ

RabbitMQ 只知道“消息是否在队列中、是否被确认”，不适合承担全部业务状态。

例如一个任务可能已经：

- 在 PostgreSQL 创建；
- 投递 RabbitMQ；
- Worker 已经开始执行；
- 文件已经解析；
- Elasticsearch 写入失败。

这些业务状态需要保存在 `upload_processing_jobs`，否则前端无法准确展示“失败在哪个阶段”。

## 11. 这个项目的面试表达

> 我把上传后的处理拆成阶段级 job。上传完成接口只完成 OSS Multipart Upload，并创建 download job。FastAPI 通过 Celery 将任务投递到 RabbitMQ，Worker 消费后执行对应阶段。每个阶段完成后创建下一个有依赖关系的 job。PostgreSQL 保存业务状态和重试信息，RabbitMQ 只负责消息传递。因为 Celery/RabbitMQ 通常是至少一次投递，所以每个阶段使用 job 状态、唯一约束和稳定 vector id 保证幂等。
