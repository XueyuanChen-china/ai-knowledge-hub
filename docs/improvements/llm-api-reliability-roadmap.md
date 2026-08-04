# LLM API Reliability Roadmap

这份文档记录 Agent 运行时对外部 LLM / Embedding / Rerank API 的后续升级方向。

它不属于 OSS 大文件上传本身，也不替代 RabbitMQ 的基础任务投递。它解决的是：Worker 或 Agent 节点调用外部模型服务时，如何控制并发、处理限流、避免故障扩散并统计成本。

## 一、当前边界

当前项目已经有：

- RabbitMQ + Celery 上传后处理任务；
- `download -> validate -> parse -> split -> embed -> index` 阶段流水线；
- Router / Answer Node 调用 Qwen；
- Embedding 模型调用；
- PostgreSQL 任务状态记录。

当前还没有完整实现：

- LLM API 全局并发限制；
- 按租户或用户的 Token 配额；
- RPM / TPM 限流；
- 429 专用退避策略；
- 熔断和半开恢复；
- LLM 调用成本统计；
- 统一的模型调用 trace。

## 二、为什么需要单独治理

假设有 20 个 Celery Worker 同时处理文档或 Agent 请求：

```text
20 个 Worker
  -> 同时调用 Qwen / Embedding API
  -> 超过 RPM / TPM
  -> 大量 429
  -> Worker 重试
  -> 重试进一步增加请求量
  -> 外部服务和本地任务同时拥塞
```

所以 MQ 只能控制“任务什么时候被消费”，不能自动控制“外部模型 API 能承受多少请求”。两层需要分开：

```text
RabbitMQ / Celery
  -> 调度任务并控制 Worker 消费

LLM Gateway / Client Policy
  -> 控制模型 API 调用速率、并发和成本
```

## 三、推荐目标链路

```text
Agent Node / Celery Task
  -> LLM Client
  -> Tenant Quota Check
  -> Global Semaphore / Rate Limiter
  -> Provider API
  -> 成功 / 429 / 超时 / 5xx
  -> Retry or Circuit Breaker
  -> Usage Meter
```

## 四、分阶段升级

### Phase 1：单进程并发限制

目标：先避免单个 Worker 进程同时发出过多模型请求。

实现方向：

- 使用 `asyncio.Semaphore` 或线程安全 Semaphore；
- 对 Router、Answer、Embedding 分别设置并发上限；
- 请求完成后释放 Semaphore；
- 超过等待时间时返回明确错误，不无限等待。

示例：

```python
llm_semaphore = asyncio.Semaphore(4)

async with llm_semaphore:
    response = await call_llm_api(...)
```

注意：进程内 Semaphore 只能限制一个进程。多个 Celery Worker 或多台机器时，需要后续使用 Redis 或网关做全局限制。

### Phase 2：429 和临时错误退避

目标：让模型服务限流时不会立刻形成重试风暴。

可重试错误：

- HTTP 429；
- 连接超时；
- 网络断开；
- HTTP 502 / 503 / 504。

不应盲目重试：

- API Key 无效；
- 请求参数错误；
- 模型不存在；
- 内容安全策略拒绝。

退避策略：

```text
第 1 次：1 秒
第 2 次：2 秒
第 3 次：4 秒
```

实际实现应增加 jitter，避免多个 Worker 同时重新请求。

### Phase 3：熔断器

目标：外部模型服务持续失败时，快速失败，保护本地系统。

状态：

```text
Closed
  -> 连续失败达到阈值
Open
  -> 暂停调用一段时间
Half-Open
  -> 放行少量探测请求
Closed
```

触发熔断的典型情况：

- 连续大量 5xx；
- 连续超时；
- 服务商明确不可用。

熔断后可以：

- 返回“模型暂时不可用”；
- 降级到备用模型；
- 降级到抽取式答案；
- 进入人工审核；
- 将任务重新放回异步队列。

### Phase 4：租户配额和全局限流

目标：多用户或多租户环境下，避免一个租户耗尽全部模型额度。

需要记录：

- tenant_id；
- user_id；
- provider；
- model；
- request_count；
- input_tokens；
- output_tokens；
- estimated_cost。

限流维度：

```text
全局并发上限
租户并发上限
用户并发上限
模型 RPM
模型 TPM
每日 Token 配额
每日金额配额
```

这一步通常需要 Redis、统一 LLM Gateway 或专门的限流服务，暂时不与当前 OSS 上传 MQ 混合实现。

### Phase 5：成本、日志和 Trace

每次调用至少记录：

```text
trace_id
conversation_id
processing_job_id
tenant_id
provider
model
input_tokens
output_tokens
latency_ms
status_code
retry_count
estimated_cost
```

目标链路：

```text
用户问题
  -> graph run
  -> answer node
  -> LLM request
  -> provider response
  -> token usage / cost record
```

## 五、和当前 MQ 的关系

两者是协作关系，不是同一个模块：

```text
RabbitMQ + Celery
  负责：任务排队、Worker 调度、失败重新投递

LLM Reliability Layer
  负责：API 并发、RPM/TPM、429、熔断、成本和降级
```

例如 Embedding 阶段：

```text
Celery 消费 embed job
  -> LLM/Embedding Client 获取 Semaphore
  -> 检查租户额度
  -> 请求 BGE-M3 服务
  -> 429 则退避
  -> 持续失败则熔断或返回 job retry_scheduled
```

当前 PostgreSQL job 重试仍然可以保留，但需要明确边界：

- LLM Client 负责短时间内的 API 级重试；
- Celery / PostgreSQL job 负责较长时间的任务级重试；
- 熔断器负责阻止持续失败的请求；
- RabbitMQ DLX 只保存最终失败消息。

## 六、当前暂不做的内容

以下内容先学习，不立即落地：

- 多机 Redis 分布式限流；
- LLM Gateway 集群；
- 多区域模型故障切换；
- 成本结算和计费系统；
- Kubernetes 层面的自动扩缩容；
- 多供应商复杂路由策略。

## 七、面试表达

> MQ 负责把 Agent 的长任务异步化，但它不能替代 LLM API 治理。多个 Worker 同时调用模型时，我会在 LLM Client 层增加并发 Semaphore、429 指数退避和熔断，并按租户统计 RPM、TPM、Token 和成本。短暂 API 错误由 Client 重试，长时间任务失败由 Celery/PostgreSQL job 重试，最终失败消息进入 DLX，避免多个重试层互相放大。

