# MQ 核心概念

## 1. MQ 是什么

MQ，Message Queue，消息队列，本质是一个暂存消息的中间件。

生产者不必直接等待消费者执行完成，而是把消息交给 Broker：

```text
Producer -> Broker / Queue -> Consumer
```

例如上传完成后，FastAPI 不需要同步执行 PDF 解析和 Embedding，而是发送：

```json
{
  "job_id": 101,
  "stage": "download"
}
```

Worker 稍后消费这条消息。

## 2. MQ 解决什么问题

### 解耦

FastAPI 不需要知道 Worker 是哪个进程、哪台机器。

### 削峰

短时间来了 1000 个上传任务，MQ 可以先保存消息，Worker 按处理能力消费。

### 异步化

接口可以快速返回“任务已提交”，而不是等待耗时任务完成。

### 重试和故障恢复

Worker 失败后可以重新投递；Worker 崩溃时，未确认的消息可以重新交给其他 Worker。

### 资源隔离

解析、Embedding、索引可以使用不同队列和不同 Worker 数量。

## 3. MQ 不等于线程池

线程池是一个进程内部的并发工具：

```text
同一个进程 -> 多个线程
```

MQ 是跨进程、跨机器的任务传递工具：

```text
FastAPI 进程 -> RabbitMQ -> Worker 进程
```

线程池解决“当前进程怎么并发执行”，MQ 解决“任务怎么可靠传给另一个执行者”。

## 4. 四个基础角色

### Producer

发送消息的一方。当前项目是 FastAPI / `upload_celery_service.py`。

### Broker

接收、保存、路由消息的中间件。当前项目是 RabbitMQ。

### Queue

等待消费的消息队列。Worker 从 Queue 获取消息。

### Consumer

消费消息并执行任务的一方。当前项目是 Celery Worker。

## 5. RabbitMQ 的 Exchange

RabbitMQ 通常不是 Producer 直接把消息发送到 Queue，而是：

```text
Producer -> Exchange -> Binding -> Queue -> Consumer
```

### direct

按精确 routing key 路由：

```text
routing_key = upload.download
```

### topic

按通配符路由：

```text
upload.*
upload.#
```

### fanout

广播给所有绑定队列，忽略 routing key。

Agent 场景中可以用 fanout 把一条“文档已完成”消息同时发给审计、通知和索引服务。

## 6. ACK 是什么

ACK 表示 Consumer 告诉 Broker：

> 这条消息我已经成功处理，可以从队列中移除。

如果 Worker 在 ACK 前崩溃，Broker 可以把消息重新投递。因此常见语义是“至少一次投递”。

这也意味着任务可能重复执行，所以业务代码必须幂等。

## 7. 重试和死信

不要无限重试。

```text
消费失败
  -> 等待退避时间
  -> 重试第 1 次
  -> 重试第 2 次
  -> 超过最大次数
  -> dead-letter queue
```

适合重试的错误：

- 临时网络失败；
- Elasticsearch 暂时不可用；
- 外部 Embedding API 超时。

不适合重试的错误：

- 文件格式永远不合法；
- 参数校验失败；
- 权限不足。

## 8. 幂等

幂等表示同一个任务执行一次和执行多次，最终业务结果一致。

当前项目可以使用：

- `upload_processing_jobs.id` 作为业务任务 ID；
- `stage` + `upload_task_id` 保证阶段唯一；
- 执行前检查 job 是否已经 `completed`；
- index 前删除旧 vector 或使用稳定 `vector_id`；
- PostgreSQL 唯一约束防止重复分片记录。

一句话面试回答：

> MQ 通常只能保证至少一次投递，不能天然保证业务只执行一次，因此消费者必须通过业务唯一键、状态机、唯一约束或幂等写入实现重复消费安全。
