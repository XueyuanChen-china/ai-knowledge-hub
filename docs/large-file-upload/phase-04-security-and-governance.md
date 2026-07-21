# Phase 04 Security And Governance

这份文档对应当前仓库的大文件上传 Phase 4。

这一阶段的重点已经不再是“能不能上传”，而是：

> 让上传后处理真正脱离请求线程，并补上企业系统必须要有的安全、治理和运行控制。

## 1. Phase 4 实际落地了什么

当前仓库已经补上这些能力：

- `upload_processing_jobs` 改成真正异步执行
- `POST /uploads/{upload_id}/complete` 只负责完成 multipart + 入队 job
- 应用内 worker 轮询数据库并异步处理 job
- 下载/解析阶段和索引阶段拆成两个并发池
- 下载对象改成流式写本地，同时流式计算 SHA256
- 增加 job 重试退避字段：
  - `retry_count`
  - `max_retry_count`
  - `next_run_at`
  - `last_alert_at`
  - `alert_status`
- 文件类型探测和 magic number 校验
- docx/xlsx 的 zip 安全检查
- 基础配额和限流控制
- 上传审计日志

## 2. 为什么要把后处理迁到异步 worker

如果继续把 parse / split / embed / index 都放在：

```http
POST /uploads/{upload_id}/complete
```

这个请求里同步执行，会有几个问题：

- 请求耗时很长
- 上传 complete 和索引成功绑死在一个请求上
- 一旦 embedding 慢或者 ES 卡住，请求体验很差
- 无法做标准的重试退避和阶段并发控制

所以现在改成：

```text
complete multipart upload
  -> enqueue processing job
  -> worker 异步消费
```

这一步之后，上传控制面和后处理执行面就真正分开了。

## 3. 当前 worker 怎么实现

这版还没有接外部 MQ，而是先做了一个应用内 worker：

- 启动时创建线程池
- 轮询 `upload_processing_jobs`
- 挑出 `pending / retry_scheduled` 且到期可执行的任务
- 标记为 `queued`
- 提交到线程池运行

你可以把它理解成：

- 调度中心还是数据库
- 执行器先放在 FastAPI 进程内

这不是最终企业形态，但已经从“同步直跑”升级到“异步 job 模型”了。

## 3.1 阶段级 job 表 Phase A

当前已经先把 `upload_processing_jobs` 升级成能表达阶段级流水线的结构。

新增核心字段：

- `stage`：当前阶段，例如 `download / validate / parse / split / embed / index`
- `depends_on_job_id`：当前阶段依赖的上一个 job
- `attempt_count`：这个阶段实际开始执行过几次
- `max_attempts`：这个阶段最多允许执行几次
- `celery_task_id`：后续接 Celery 后记录外部任务 ID

Phase A 只做第一步：

```text
POST /uploads/{upload_id}/complete
  -> 创建 stage=download 的 upload_processing_jobs 记录
```

这一阶段还没有接 RabbitMQ / Celery，也还没有把 `download / validate / parse / split / embed / index` 拆成多个真实 worker 消费。

也就是说，目前表结构已经为阶段级流水线准备好，但执行逻辑仍然兼容旧的整体 `run_processing_job()`。

## 4. 为什么说现在已经拆成两个并发池

Phase 4 里最关键的一点，是把后处理链路里最重的两段拆开：

### 下载/解析池

控制：

- 从 OSS 流式下载
- hash 校验
- magic number 检测
- 文本抽取

这是 IO + CPU 混合阶段。

### 索引池

控制：

- chunk 生成
- embedding
- Elasticsearch 写入

这是更偏 CPU / 模型 / 向量索引资源的阶段。

现在代码里通过两个独立 semaphore 控制：

- `upload_download_stage_concurrency`
- `upload_index_stage_concurrency`

虽然底层还是一个应用内线程池，但关键资源阶段已经拆开，不会再完全混在一起。

## 5. 流式 hash 为什么比之前更合理

之前的做法是：

- 先整对象读到内存
- 再 `sha256(bytes)`

问题是：

- 文件大时内存占用不合理
- 不能算企业级大文件处理

现在改成：

- 从 OSS 分块读取
- 每块直接写到本地文件
- 同时 `hasher.update(chunk)`

也就是：

```text
download chunk
  -> write file
  -> update sha256
```

这样不需要把整个对象一次性拉进内存。

## 6. 文件类型探测和 magic number 校验现在怎么做

这一步不是看文件后缀，而是看文件真实头部字节。

当前做法：

- PDF：检查 `%PDF-`
- DOCX/XLSX：检查 `PK\\x03\\x04`
- TXT/MD/CSV：检查是否包含明显二进制特征，比如 `NUL` 字节

这一步的意义是：

- 避免用户把可执行文件改名成 `.pdf`
- 避免后缀和内容不一致

## 7. 恶意文件控制现在做到哪一步

当前主要针对 `docx / xlsx` 这种 zip 容器做了基础检查：

- zip 成员数量不能过多
- 总解压大小不能过大
- 压缩比不能异常高
- zip entry 不能有路径穿越
- docx 必须包含 `word/document.xml`
- xlsx 必须包含 `xl/workbook.xml`

这套检查的目的不是“杀毒”，而是先挡掉最常见的：

- zip bomb
- 路径穿越型压缩包
- 伪造 Office 文件

## 8. 重试退避和告警状态怎么做

现在每个 processing job 都有：

- `retry_count`
- `max_retry_count`
- `next_run_at`
- `alert_status`
- `last_alert_at`

失败后：

1. 如果没超过最大重试次数
   - 标记为 `retry_scheduled`
   - 按指数退避计算下次执行时间

2. 如果超过上限
   - 标记为 `failed`
   - 记录一次“已告警”状态

当前的“告警”还只是数据库状态，不是发飞书/钉钉/邮件。
但这已经把真正的告警接入点准备好了。

## 9. 生命周期管理现在做了哪些

当前这版已经有：

- 上传任务过期时间 `expires_at`
- `POST /uploads/cleanup-expired`
- 过期时尝试 abort OSS multipart upload
- 本地任务标记为 `expired`

这属于生命周期管理的第一层：

- 先把“悬挂上传任务”清理掉

还没完全做完的，是：

- 已完成对象的 OSS 生命周期策略
- 本地回落缓存文件的定期清理
- 更细分的长期归档策略

## 10. 限流、配额、审计现在怎么做

### 10.1 限流 / 配额

当前在 `POST /uploads/init` 已经补了基础控制：

- 每个 `created_by` 的活跃上传任务数量上限
- 每个 `created_by` 的日上传字节配额上限

这是很基础但非常实用的一层治理。

### 10.2 审计

新增了：

- `upload_audit_logs`

当前会记录一些关键事件，例如：

- 初始化上传任务
- 上传完成
- processing job 创建
- processing job 开始
- processing job 成功 / 失败

这张表后面可以继续扩到：

- 用户侧审计
- 安全审计
- 成本审计

## 11. 现在 complete 接口的真实语义

现在：

```http
POST /uploads/{upload_id}/complete
```

不再表示：

> 文档已经索引完成

而是表示：

> multipart upload 已完成，且后处理 job 已入队

这两个要分清。

所以接口返回里现在更重要的是：

- `processing_job_id`
- `processing_status`

一般会先看到：

```text
processing_status = pending
```

之后 worker 跑完，`upload_task` 上的状态才会变成：

- `completed`
- 或 `retry_scheduled`
- 或 `failed`

## 12. 这版仍然不是最终形态

虽然 Phase 4 已经比前面成熟很多，但还不是最终企业版。

还值得继续做的包括：

- 把应用内 worker 换成独立 worker 进程
- 接 MQ / 调度系统
- 对告警接入真实通知渠道
- 对本地回落文件做定期清理任务
- 做更细的租户权限、配额和速率治理

## 13. 当前结论

Phase 4 完成后，这套上传系统已经从：

```text
能上传
```

进化成：

```text
可异步处理
可重试
可校验
可审计
有基础治理
```

这已经比较接近一个企业级上传控制面的样子了。
