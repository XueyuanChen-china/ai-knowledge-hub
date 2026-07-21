# Enterprise Upload Roadmap

这份路线图以当前仓库的真实状态为前提：

- 后端已经是 `PostgreSQL + SQLModel`
- 当前有 FastAPI 主服务
- 当前还没有 Alembic
- 当前文档解析、切片、Embedding、Elasticsearch 已经存在，但上传链路仍然偏“单接口 + 本地文件”

所以这份方案不再讨论 SQLite，也不再以 MinIO 作为当前 Phase 1 的落地目标，而是直接对齐阿里云 OSS。

## 1. 目标架构

目标链路：

```text
前端
  -> POST /uploads/init
  -> 获取 upload_id / object_key / part_size / total_parts
  -> 后续按 multipart 协议把文件上传到阿里云 OSS
  -> POST /uploads/{upload_id}/complete

后端 Upload API
  -> 记录 upload_tasks / upload_parts
  -> 维护上传状态
  -> 校验对象存储元数据
  -> 创建 document / index job

后台处理链路（后续 Phase）
  -> parse
  -> split
  -> embed
  -> write Elasticsearch
  -> update status
```

## 2. 为什么当前直接用阿里云 OSS

当前目标不是做“最便宜的本地演示版”，而是做更像企业项目的表达。

如果还把大文件先传到 FastAPI，再落本地磁盘：

```text
Browser -> FastAPI -> local disk
```

问题很直接：

- 上传流量会穿过业务服务
- 应用实例要承担网络 IO 和磁盘 IO
- 多实例部署时本地文件不一致
- 后面做断点续传、生命周期管理、跨机房存储都不自然

如果改成对象存储：

```text
Browser -> Aliyun OSS
Backend -> 只负责签名、状态、元数据、后续处理调度
```

好处更符合企业常态：

- 文件原件和应用服务解耦
- 更容易做大文件上传和恢复
- 更容易做权限控制、审计、归档和过期清理
- 后面接 Worker 和异步索引链路更顺

## 3. 当前推荐边界

### 3.1 Upload API

负责：

- 初始化上传任务
- 生成对象路径
- 维护 `upload_tasks / upload_parts`
- 完成上传状态流转

不负责：

- 文档解析
- 文本切片
- embedding
- Elasticsearch 写入

### 3.2 Object Storage Adapter

负责：

- 屏蔽具体 OSS SDK
- 提供 multipart 初始化、complete、abort、presign 等能力

这样后续如果需要兼容 S3，只要补新 adapter。

### 3.3 Document Processing / Indexing

后续继续保留为独立阶段：

- 上传完成后再创建 document
- parse 和 index 继续异步化
- 和上传链路分开限流

## 4. 当前阶段状态

### Phase 1 已落地

### 已要求落地的内容

- 阿里云 OSS 配置接入
- 对象存储封装层
- `upload_tasks` / `upload_parts` 模型
- `POST /uploads/init`
- `GET /uploads/{upload_id}`
- `POST /uploads/{upload_id}/complete`
- 基础测试

### Phase 1 已完成后留空的内容

- 真实前端分片上传
- `POST /uploads/{upload_id}/parts/presign`
- 断点续传恢复逻辑
- Worker / MQ
- 上传完成后自动解析
- 上传完成后自动入 Elasticsearch

## 5. 当前数据模型建议

### 5.1 `upload_tasks`

```text
id
upload_id
knowledge_base_id
original_filename
storage_provider
bucket_name
object_key
file_type
client_mime_type
detected_mime_type
file_size
part_size
total_parts
file_sha256
storage_upload_id
status
completed_parts
error_message
created_by
created_at
updated_at
```

状态建议：

```text
initiated
uploading
uploaded
verifying
completed
failed
cancelled
expired
```

### 5.2 `upload_parts`

```text
id
upload_task_id
part_number
etag
part_size
part_sha256
status
created_at
updated_at
```

这里字段命名固定使用：

- `part_size`
- `total_parts`
- `part_number`
- `etag`

不使用 `upload chunk`，避免和 RAG 的文本 `chunk` 混淆。

## 6. 当前对象路径规则

对象路径由后端统一生成，禁止直接信任用户原始文件名：

```text
raw/dev/{knowledge_base_id}/{upload_id}/source.{extension}
```

例如：

```text
raw/dev/7/upl_a1b2c3d4e5f6a7b8/source.pdf
```

这样做的原因：

- 路径规则稳定
- 不泄漏用户原始文件名到对象路径
- 避免特殊字符和路径穿越风险
- 后续更方便按知识库和 upload_id 追溯

## 7. API 方向

### 7.1 初始化上传任务

```http
POST /uploads/init
```

请求：

```json
{
  "knowledge_base_id": 7,
  "filename": "supplier-policy.pdf",
  "file_size": 734003200,
  "client_mime_type": "application/pdf",
  "file_sha256": "optional",
  "created_by": "alice"
}
```

响应：

```json
{
  "upload_id": "upl_xxx",
  "storage_provider": "aliyun-oss",
  "bucket_name": "ai-knowledge-hub-xueyuan-dev",
  "object_key": "raw/dev/7/upl_xxx/source.pdf",
  "part_size": 5242880,
  "total_parts": 141,
  "status": "initiated"
}
```

### 7.2 查询上传任务

```http
GET /uploads/{upload_id}
```

返回当前任务元数据和状态，用于前端查询。

### 7.3 完成上传

```http
POST /uploads/{upload_id}/complete
```

Phase 1 先保留协议和状态入口，真实 multipart complete 放到 Phase 2。

## 8. Phase 2 当前已落地

当前仓库已经补上：

- `POST /uploads/{upload_id}/parts/presign`
- `POST /uploads/{upload_id}/parts/complete`
- `POST /uploads/{upload_id}/abort`
- `GET /uploads/{upload_id}/parts`
- 服务端基于 OSS `list parts` 做 part 级校验
- 最终 `POST /uploads/{upload_id}/complete` 真正执行 multipart complete
- 断点续传查询所需的 `local_parts / remote_parts / missing_part_numbers`

这一阶段的重点不是“把文件内容传过来”，而是：

- 前端直传 OSS
- 后端只管上传控制面
- 本地记录和 OSS 远端记录能对齐

## 9. Phase 3 当前已落地

当前仓库已经继续补上：

- `POST /uploads/{upload_id}/parts/presign-batch`
- part 重试次数控制
- 上传任务过期时间与清理接口
- 上传完成后自动创建 `documents`
- 上传完成后自动触发 parse / split / embed / index
- 新增 `upload_processing_jobs` 表，把上传后处理单独记录成 job
- `UploadTask.processing_status / processing_error_message / document_id`

这一阶段的重点是：

- 上传控制面继续留在 `/uploads`
- 文档创建和索引流程开始自动化
- 代码上把“上传完成”和“上传后处理”拆开

虽然当时还是同步执行后处理，但结构上已经不再和上传接口糊成一团。

## 10. 后续怎么走

### Phase 2

这一层现在已经完成，后面不再作为规划项。

### Phase 3

这一层现在已经完成，后面不再作为规划项。

### Phase 4

当前仓库已经继续补上：

- `upload_processing_jobs` 真实迁到应用内异步 worker
- 上传 complete 只负责入队，不同步跑 parse/index
- 下载/解析阶段与索引阶段拆成两个并发池
- 对象下载改为流式写本地 + 流式 SHA256
- job 重试退避与告警状态字段
- 文件类型探测和 magic number 校验
- docx/xlsx 的基础 zip 恶意文件控制
- 上传任务过期清理
- 发起人活跃任务上限和日配额控制
- 上传审计日志表

这一层现在已经落地，后面更值得继续做的是：

- 把完整阶段流水线迁移到真正独立进程 / MQ
- 本地告警状态接入外部通知系统
- 生命周期管理再细化到 OSS 对象和本地缓存清理策略
- 更细粒度的租户级限流、配额和权限控制

## 11. 当前结论

当前项目如果要做成更像企业的版本，方向应该是：

- 上传走阿里云 OSS
- PostgreSQL 继续保存任务和元数据
- RabbitMQ + Celery 已接入 hello task 和 download 阶段真实消费，后续继续迁移完整上传流水线
- FastAPI 只承担控制面，不承担大文件数据面
- 解析和索引继续拆成后续阶段

这也是本项目后续大文件上传改造的主线。

## 12. Phase D 当前已落地

当前已经把完整处理流水线接到阶段级 job：

```text
download -> validate -> parse -> split -> embed -> index
```

每个阶段都有独立 `upload_processing_jobs` 记录，并通过 `depends_on_job_id` 记录依赖。最终成功时：

- `documents.status = indexed`
- `chunks.vector_id` 已回填
- 每个阶段 job 为 `completed`
- 失败阶段复用重试退避逻辑

详细说明见：

```text
docs/large-file-upload/phase-07-full-processing-pipeline.md
```
