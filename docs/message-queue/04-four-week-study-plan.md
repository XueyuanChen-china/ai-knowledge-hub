# MQ 四周学习计划

## 第 1 周：基础模型

目标：能讲清 MQ 的价值和基本组成。

学习：

- 同步、异步、线程池、进程；
- Producer、Broker、Queue、Consumer；
- Exchange、routing key、binding；
- ACK、NACK、requeue。

练习：

- 画出当前上传任务的消息链路；
- 解释为什么 upload complete 不直接执行 parse/index。

## 第 2 周：可靠性

目标：能回答“消息会不会丢、会不会重复”。

学习：

- 持久化；
- publisher confirm；
- 手动 ACK；
- 至少一次投递；
- 幂等；
- 重试退避；
- 死信队列。

练习：

- 设计一个“文档索引失败重试”流程；
- 列出当前项目中防止重复 index 的机制。

## 第 3 周：RabbitMQ + Celery

目标：能读懂并解释当前项目代码。

学习：

- `apply_async()`；
- task id；
- Celery Worker；
- prefork；
- concurrency；
- task retry；
- RabbitMQ broker URL。

练习：

- 启动 RabbitMQ；
- 启动 Celery Worker；
- 跑通一个 hello task；
- 跑通 upload download stage；
- 查询 PostgreSQL 中的 job 状态。

## 第 4 周：Agent 后端设计

目标：把 MQ 知识迁移到 Agent 系统。

学习：

- 异步工具调用；
- 文档处理 pipeline；
- 并发池；
- LLM API 限流；
- 超时、熔断、重试；
- 任务可观测性；
- 人工审核和恢复。

练习：

- 设计一个长耗时报告生成任务；
- 设计一个 Embedding API 限流方案；
- 解释任务失败后如何从中间阶段恢复。

## 每天的复习方式

每个概念按下面四步学习：

```text
先说定义
再说解决的问题
再说失败场景
最后映射到当前项目
```
例如“幂等”：

```text
定义：重复执行最终结果一致。
问题：MQ 至少一次投递可能导致重复消费。
失败场景：index job 重复写入向量。
项目映射：稳定 vector_id + job 状态 + 数据库唯一约束。
```
