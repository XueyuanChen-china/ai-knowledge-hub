# Agent 岗位 MQ 面试题

## 1. 为什么 Agent 系统需要 MQ

Agent 任务经常包含：

- 文档解析；
- 批量切片；
- Embedding；
- 搜索和 rerank；
- 工具调用；
- 长耗时报告生成；
- 失败重试和人工审核。

这些任务不适合全部阻塞 HTTP 请求，因此可以使用 MQ 做异步任务调度。

## 2. Agent 场景哪些任务适合异步

适合：

- 上传后的解析、切片、Embedding、索引；
- 批量文档处理；
- 长时间运行的工具调用；
- 报告生成；
- 审计日志和通知；
- 失败重试。

不一定适合：

- 非常快的健康检查；
- 必须立即返回的简单查询；
- 本地内存中的短函数调用。

## 3. 如何保证消息不丢

需要同时考虑：

1. Producer 发布确认；
2. Broker/Queue 持久化；
3. Consumer 手动 ACK；
4. Worker 处理成功后再 ACK；
5. 失败消息进入重试或死信队列；
6. 业务数据库记录任务状态。

只配置一个 RabbitMQ URL 不等于消息可靠。可靠性是 Producer、Broker、Consumer 和业务幂等共同完成的。

当前项目已经补了 publisher confirm：Producer 发布后等待 Broker 确认。这个确认只覆盖“Broker 是否接收”，不覆盖“Worker 是否执行成功”，所以仍然需要 ACK、业务状态和幂等。

## 4. ACK 应该什么时候发生

正确原则：

```text
收到消息
  -> 执行业务
  -> 数据库提交成功
  -> 外部系统写入成功
  -> ACK
```

如果刚收到消息就 ACK，之后 Worker 崩溃，消息已经从队列删除，任务可能丢失。

## 5. 如何处理重复消息

用业务唯一键：

```text
upload_processing_job.id
```

执行前判断：

```text
status == completed -> 直接返回
status == running   -> 根据租约判断是否可恢复
```

写数据库时使用唯一约束；写 Elasticsearch 时使用稳定文档 ID，而不是每次随机生成 ID。

## 6. 重试为什么需要退避

如果外部服务故障，立即无限重试会形成请求风暴。

常见策略：

```text
第 1 次：5 秒
第 2 次：10 秒
第 3 次：20 秒
```

实际还应增加随机抖动，避免很多 Worker 同时重试。

当前项目把 PostgreSQL job 重试作为主重试机制。超过最大次数后，任务使用 `reject(requeue=False)` 进入 RabbitMQ dead-letter exchange，而不是继续在主队列无限循环。

## 7. RabbitMQ 和 Redis 的关系

Redis 可以实现简单队列、缓存、分布式锁和限流；RabbitMQ 是更专门的消息代理，路由、ACK、确认、死信等消息能力更完整。

它们不是上下级关系，而是不同工具：

```text
Redis    -> 缓存、短状态、简单队列、计数器
RabbitMQ -> 可靠任务消息、路由、消费确认
```

Agent 系统可能同时使用：

- Redis 保存短期会话状态和限流计数；
- RabbitMQ 调度文档处理任务；
- PostgreSQL 保存最终业务事实。

## 8. RabbitMQ 和 Kafka 的区别

### RabbitMQ

- 传统消息队列；
- routing 灵活；
- ACK 和任务分发直观；
- 适合工作队列、异步任务、事件通知。

### Kafka

- 分布式日志和事件流平台；
- 通过 partition 和 offset 管理消费进度；
- 吞吐量高，适合日志、埋点、事件流和大规模数据管道；
- 消费者可以回放历史消息。

当前项目的文档处理任务用 RabbitMQ + Celery 更容易表达；如果以后要做全链路事件流、审计事件回放或海量日志，再考虑 Kafka。

## 9. K8s 和 MQ 的关系

Kubernetes 不是 MQ。

```text
RabbitMQ   -> 负责消息传递
Kubernetes -> 负责部署、扩缩容、故障重启和服务编排
```

生产环境可能是：

```text
Kubernetes 部署 FastAPI
Kubernetes 部署 Celery Worker
RabbitMQ 作为独立中间件
PostgreSQL 作为数据库
```

## 10. 面试时的边界回答

被问到“有没有做过 MQ”时，不要只说“配置过 RabbitMQ”。应该说明：

- 消息从哪里产生；
- 由谁消费；
- 消息体是什么；
- 业务状态存在哪里；
- 如何重试；
- 如何处理重复消费；
- Worker 崩溃如何恢复；
- 为什么按阶段拆 job。

## 11. 需要继续补强的生产问题

当前项目已经有基础任务投递和阶段消费，但面试中还应继续学习：

- publisher confirm；
- 手动 ACK 和 prefetch；
- dead-letter exchange；
- 延迟重试队列；
- RabbitMQ 高可用；
- Celery 任务超时和 revoke；
- 任务指标、日志和 trace id；
- 租户级并发和配额；
- 外部 LLM API 限流与熔断。
