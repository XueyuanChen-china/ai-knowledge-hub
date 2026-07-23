# MQ 面试学习路线

本目录面向 `Agent / LLM 应用后端` 岗位。目标不是先把你培养成 RabbitMQ 运维专家，而是让你能讲清楚：为什么 Agent 系统需要消息队列、消息如何可靠投递、任务如何重试、如何避免重复处理，以及这些设计如何落到当前项目。

## 一、准备深度

建议采用三层深度。

### 第一层：必须熟练

- MQ 解决什么问题；
- 同步调用和异步任务的区别；
- Producer、Broker、Consumer、Queue、Exchange 的关系；
- RabbitMQ 的 direct / topic / fanout 基本区别；
- ACK、NACK、reject、requeue；
- 重试、死信队列、幂等；
- Celery 如何投递和消费任务；
- Agent 系统中哪些任务适合进入 MQ。

### 第二层：能够结合项目回答

- 上传完成后为什么只创建 `download` job；
- 为什么后续是 `download -> validate -> parse -> split -> embed -> index`；
- 为什么每个阶段使用独立 job 记录；
- RabbitMQ 断开、Worker 崩溃、任务重复执行时怎么办；
- `celery_task_id` 和 PostgreSQL 业务 job id 的区别；
- 任务状态为什么不能只依赖 Celery backend；
- 如何控制 Embedding 的并发和外部模型调用配额。

### 第三层：了解原理，暂不要求源码级

- RabbitMQ exchange binding 的路由过程；
- publisher confirm；
- consumer prefetch；
- RabbitMQ 持久化、复制和集群概念；
- Kafka 的 partition、offset、consumer group；
- Celery prefork、线程池、协程池的差异；
- MQ 至少一次、至多一次、恰好一次语义的现实边界。

暂时不把 RabbitMQ 集群运维、Erlang 内部实现和 Kafka 源码作为第一阶段重点。

## 二、推荐学习顺序

```text
异步任务基础
  -> MQ 核心模型
  -> RabbitMQ 路由和可靠性
  -> Celery 执行模型
  -> 当前项目代码链路
  -> Agent 场景设计
  -> 故障与面试题
```

## 三、当前项目对应关系

```text
FastAPI
  -> dispatch_upload_stage_job()
  -> Celery apply_async()
  -> RabbitMQ broker
  -> Celery Worker
  -> upload_*_stage_task()
  -> PostgreSQL 更新 upload_processing_jobs
```

代码入口：

- `backend/app/celery_app.py`
- `backend/app/services/upload_celery_service.py`
- `backend/app/tasks/upload_tasks.py`
- `backend/app/services/upload_postprocess_service.py`
- `backend/app/db/models.py` 中的 `UploadProcessingJob`
- `backend/scripts/start_celery_worker.sh`
- `backend/docker-compose.rabbitmq.yml`

## 四、学习目标

完成本目录后，你应该能用一分钟说明：

> 上传接口只负责完成 OSS Multipart Upload，并在 PostgreSQL 创建第一个 download job；Celery 将任务消息发送到 RabbitMQ，Worker 消费后执行下载。每个阶段完成后再创建并投递下一个阶段，数据库负责记录业务状态、依赖、重试和错误，RabbitMQ 负责传递待执行消息。由于消息可能重复投递，所以每个阶段必须设计成幂等。
