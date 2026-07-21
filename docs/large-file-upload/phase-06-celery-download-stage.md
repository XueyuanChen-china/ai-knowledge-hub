# Phase C：Celery Download Stage

## 目标

把上传完成后的第一阶段任务交给独立 Celery worker 执行，验证“上传控制面”和“文件处理面”已经分离。

## 实际链路

```text
upload complete
  -> PostgreSQL 创建 upload_processing_jobs(stage=download)
  -> FastAPI 投递 uploads.download
  -> RabbitMQ 保存消息
  -> Celery worker 消费
  -> 下载 OSS 对象
  -> 文件类型和哈希校验
  -> 更新 PostgreSQL job 状态
```

API 请求只负责创建 job 和投递消息，不会在请求线程里等待 OSS 下载，也不会在这个阶段执行文档解析和索引。

## 关键代码路径

### 1. 创建并投递 job

`app/services/upload_postprocess_service.py` 的 `enqueue_processing_job()`：

- 创建 `stage=download` 的 job
- 如果 `UPLOAD_PROCESSING_BACKEND=celery`，调用 download 派发器
- 把 Celery 返回的 `celery_task_id` 写回 PostgreSQL

### 2. Celery 派发器

`app/services/upload_celery_service.py` 的 `dispatch_upload_download_stage_job()`：

- 校验 job 存在且阶段为 download
- 调用 `upload_download_stage_task.apply_async()`
- 更新 `current_step=celery_download_dispatched`

### 3. Worker task

`app/tasks/upload_tasks.py` 的 `upload_download_stage_task()`：

- 从 Celery 请求上下文读取 task id
- 打开独立数据库 Session
- 调用阶段执行函数
- 返回本阶段结果

### 4. 阶段执行函数

`app/services/upload_postprocess_service.py` 的 `run_download_stage_job()`：

- 将 job 标记为 `running`
- 通过对象存储 adapter 流式下载到 `data/uploads`
- 检查 PDF、DOCX、XLSX、文本文件的基础文件头
- 对 Office ZIP 检查成员数量、解压大小、压缩比和危险路径
- 对声明了 `file_sha256` 的任务做 SHA256 比对
- 成功后标记 `completed`
- 异常时复用 retry/backoff 逻辑

## 状态如何理解

```text
pending
  -> Celery 消息已发送，但 worker 尚未开始

running
  -> worker 已经领取任务，正在下载或校验

completed
  -> download 阶段成功完成

retry_scheduled
  -> 本次失败，等待退避后重试

failed
  -> 已超过最大重试次数
```

`completed` 只表示当前阶段完成。它不表示 `parse / split / embed / index` 已经完成，这正是阶段级 job 的意义。

## 和应用内 worker 的关系

当配置为：

```env
UPLOAD_PROCESSING_BACKEND=celery
```

FastAPI 内置的线程 worker 不会启动，也不会 claim 这些 job，避免同一个 job 被应用内线程池和 Celery worker 重复消费。

如果本地暂时不启动 RabbitMQ，可以切回：

```env
UPLOAD_PROCESSING_BACKEND=in_app
```

这时会使用已有的应用内完整处理流程，便于兼容旧测试和没有 MQ 的开发环境。

## Phase C 的边界

本阶段已经完成：

- complete 自动创建 download job
- 自动投递 Celery
- worker 真实下载和校验
- PostgreSQL 状态追踪
- 重试失败状态复用

本阶段暂不完成：

- validate / parse / split / embed / index 的独立 task
- download 完成后自动创建下一个阶段 job
- 每个阶段独立队列和并发池
- Worker 监控、告警和死信队列
