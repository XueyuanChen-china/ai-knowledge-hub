# U7：可观测性与运维闭环

## 这一阶段做了什么

U7 的目标是：当接口、Celery 任务或上传流水线出问题时，能够沿着一条关联线定位问题，而不是只看到一个 `500` 或长期 `pending`。

```text
HTTP 请求
  -> RequestContextMiddleware 创建 request_id / trace_id
  -> JSON 日志 + HTTP 响应头
  -> 上传完成接口投递带 headers 的 Celery 消息
  -> Worker 恢复同一个 trace_id
  -> 阶段 job + 审计记录 + metrics
```

这一阶段新增了：结构化 JSON 日志、敏感信息脱敏、低基数指标、按职责拆分的健康检查，以及上传/DLQ 的只读诊断能力。

## 核心代码路径

- `backend/app/middleware/request_context.py`

  - 每个 HTTP 请求创建或接收关联 ID。
  - 把 `X-Request-ID`、`X-Trace-ID` 写回响应头。
  - 记录请求开始、结束和耗时指标。
- `backend/app/observability/context.py`

  - 用 Python `ContextVar` 保存当前调用链的 `request_id`、`trace_id`、`upload_id`、`processing_job_id` 与 `celery_task_id`。
  - 这样业务函数不需要层层增加这些参数，也能读取当前上下文。
- `backend/app/observability/logging.py`

  - 将普通 Python 日志格式化为单行 JSON。
  - 对密码、JWT、API Key、OSS Secret 和预签名 URL 查询参数做脱敏。
  - 异常堆栈文本也会再次脱敏，避免上游 SDK 错误正文泄露敏感信息。
- `backend/app/services/upload_celery_service.py`

  - API 进程投递 Celery 消息时，将当前的 request/trace/job 信息放到 message headers 中。
- `backend/app/tasks/upload_tasks.py`

  - Celery Worker 消费消息时，从 headers 恢复关联上下文。
  - 因此 API 日志、Worker 日志和数据库审计记录可以通过同一个 `trace_id` 关联。
- `backend/app/api/health.py`

  - 提供存活检查、PostgreSQL readiness、搜索 readiness、上传 readiness 和指标导出接口。
- `backend/scripts/diagnose_upload_pipeline.py`

  - 只读查询上传任务、阶段 job 与审计事件，不会自动重试、修改或重放消息。

## 需要理解的概念

### Request ID 和 Trace ID

`request_id` 用于识别一次 HTTP 请求。例如浏览器发起一次 `POST /uploads/{id}/complete`，这一次调用有一个 request ID。

`trace_id` 用于识别一整条业务链路。上传完成后会异步经过 Celery 的 download、validate、parse、split、embed、index 阶段；虽然这些不再是同一个 HTTP 请求，但它们保留同一个 trace ID。

因此排障时可以通过一个 trace ID 串起：用户请求、Celery 任务、上传阶段状态和审计记录。

### 什么是低基数指标

指标标签的取值必须是有限且可预期的。

安全的标签：

```text
stage=download
status=failed
operation=semantic_search
```

不安全的标签：

```text
upload_id=upl_xxx
trace_id=xxx
user_id=12345
```

后者几乎每次请求都会产生新取值，称为高基数。高基数会让 Prometheus 一类监控系统占用大量内存，查询也会变慢。

所以：具体 ID 应写入日志与审计表；metrics 只保留有限分类。

### Liveness 和 Readiness 的区别

Liveness 回答的是：进程还活着吗？

Readiness 回答的是：这个进程现在适合接收某类流量吗？

本项目的区分如下：

| 接口                      | 含义                                                    |
| ------------------------- | ------------------------------------------------------- |
| `/health/live`          | FastAPI 进程能响应请求，不检查外部依赖。                |
| `/health/ready`         | PostgreSQL 可用，普通业务接口可以接流量。               |
| `/health/ready/search`  | PostgreSQL 与 Elasticsearch 都可用，搜索/索引路径可用。 |
| `/health/ready/uploads` | PostgreSQL 与 RabbitMQ 都可用，上传后处理可以投递。     |

这样 RabbitMQ 故障不会让纯只读知识库接口被误判为不可用。

### 为什么 JSON 日志还必须脱敏

事故发生时，最容易把错误日志直接发到群里或工单里。上游 SDK、HTTP 错误和请求头都有可能带上 token、密码、OSS 签名 URL 或模型 API Key。

因此本项目同时按两层处理：

1. 根据字段名脱敏，例如 `password`、`secret`、`token`、`api_key`。
2. 扫描普通日志文本和异常文本，脱敏 Bearer Token、JSON 中的密钥字段以及预签名 URL。

这只是防御性措施，业务代码仍然不应该主动记录请求正文或凭证。

## 常用排障命令

查看一个上传任务及其阶段状态：

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/python scripts/diagnose_upload_pipeline.py --upload-id upl_xxx
```

查看运行中但租约已过期的 job：

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/python scripts/diagnose_upload_pipeline.py --stuck-leases
```

查看 RabbitMQ 队列和死信队列中的消息数量：

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub
docker compose exec rabbitmq rabbitmqctl list_queues \
  name messages messages_ready messages_unacknowledged
```

完整恢复步骤见：

- [事故排障手册](../../operations/incident-runbook.md)
- [上传重试与死信处理](../../operations/dead-letter-and-retry.md)

## 当前边界

现在的 metrics 是每个 API/Worker 进程内存中的指标。它解决了本地观察和接口契约问题，但不会自动汇总多副本的数据。

以下内容留在后续阶段：

- Prometheus 拉取与长期存储；
- Grafana Dashboard 和告警规则；
- OpenTelemetry Collector 与分布式 tracing；
- 自动修复过期 lease；
- 带权限、带幂等保护的 DLQ 重放工具。

## 入门版：以一次上传完成请求为例

如果上面的术语比较陌生，可以先只记住一句话：

> `RequestContextMiddleware` 是所有 HTTP 请求进入 FastAPI 后最先经过的一层统一处理代码；它负责记录“谁来了、何时结束、是否出错、花了多久”。

假设前端请求：

```text
POST /uploads/upl_abc/complete
```

完整过程如下：

```text
1. 浏览器发出 HTTP 请求
   -> POST /uploads/upl_abc/complete

2. RequestContextMiddleware 先接到请求
   -> 若前端没有提供 X-Request-ID，就生成一个随机 ID
   -> trace_id 默认使用同一个 ID
   -> 写一条“请求开始”日志

3. Middleware 把 ID 放到当前请求上下文中
   -> 后面运行的 upload API、服务层和审计服务都能读取它

4. 真正的 upload complete API 执行
   -> 更新 upload_task
   -> 创建 download 阶段 job
   -> 投递 Celery 消息时，把 trace_id 放进 RabbitMQ message headers

5. HTTP API 返回响应
   -> Middleware 记录状态码和耗时
   -> 在响应头写入 X-Request-ID / X-Trace-ID

6. Celery Worker 之后收到消息
   -> 从 message headers 取回 trace_id
   -> 运行 download / validate 等阶段
   -> Worker 日志和 upload_audit_logs 都继续写同一个 trace_id
```

### Middleware 是什么

Middleware 可以理解为 API 函数外面的一层“通用包装”。

不用 middleware 时，每个接口都要自己写：

```python
start = time.time()
logger.info("request started")
try:
    result = do_business()
    logger.info("request completed")
    return result
except Exception:
    logger.exception("request failed")
    raise
```

这样会重复，而且很容易有接口漏写。

使用 `RequestContextMiddleware` 后，所有接口都自动经过同一套逻辑：

```text
请求 -> middleware -> 任意 API 路由 -> middleware -> 响应
```

因此它很适合放日志、请求 ID、统一异常记录、耗时统计、CORS、鉴权这类和具体业务无关的通用能力。

### ContextVar 是什么

`ContextVar` 可以理解为“当前请求专用的小抽屉”。

当 middleware 处理某个请求时，它会放入：

```text
request_id = req_123
trace_id = trace_123
```

后面的代码不需要把这两个值一层层传进去：

```python
upload_complete(trace_id)
    -> create_job(trace_id)
        -> log_upload_event(trace_id)
```

而是由 `log_upload_event()` 在当前上下文中读取。请求结束后，这个抽屉会恢复，不会影响下一个用户请求。

### 日志、审计表和 metrics 分别解决什么问题

它们都和“观察系统”有关，但用途不同。

| 位置 | 解决的问题 | 例子 |
| --- | --- | --- |
| JSON 日志 | 某一次请求为什么失败？ | 按 `trace_id` 找到异常和调用顺序。 |
| PostgreSQL 审计表 | 某个上传任务当前走到哪一步？ | 查看 job 的 stage、重试次数、错误信息。 |
| Metrics | 最近系统整体是否变慢或失败变多？ | `download failed` 是否突然上涨。 |

不要把它们混为一谈：

- 日志适合查具体事件。
- 审计表适合查具体业务对象。
- 指标适合看整体趋势和做告警。

### 健康检查为什么拆成多个接口

并不是所有接口都依赖同一个组件。

例如 RabbitMQ 宕机时：

- 上传完成后不能投递异步处理任务；
- 但知识库列表、用户信息等只依赖 PostgreSQL 的接口仍可能正常工作。

所以不能简单把“RabbitMQ 挂了”理解成“整个后端挂了”。

这就是 `/health/ready`、`/health/ready/search`、`/health/ready/uploads` 分开的原因：不同调用方根据自己需要的依赖判断是否可用。
