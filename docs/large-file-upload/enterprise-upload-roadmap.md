# Enterprise Upload Roadmap

这份文档把当前项目的大文件上传方案直接提升到企业版目标，不再从“本地单接口上传”出发。

核心判断：

- 如果目标是面试亮点和企业项目表达，建议直接按“对象存储 + 分片上传 + 异步处理”设计。
- 对象存储是更合理的默认方案。
- 上传服务和知识索引服务应该解耦。

## 1. 目标方案

目标链路：

```text
前端
  -> 请求创建上传任务
  -> 获取对象存储分片上传凭证
  -> 直接上传分片到对象存储
  -> 通知后端完成上传

后端
  -> 记录 upload_task / upload_part
  -> 校验对象完整性
  -> 创建 document 记录
  -> 投递 parse/index job

后台 worker
  -> parse
  -> split
  -> embed
  -> write Elasticsearch
  -> update status
```

对象存储可选：

- 阿里云 OSS
- AWS S3
- MinIO

如果是本地开发，推荐：

```text
开发环境：MinIO
生产表达：OSS / S3
```

原因很直接：

- 企业里大文件通常不走业务服务本机磁盘
- 业务服务不该承受全部上传带宽
- 文件原件、分片、归档、更适合放对象存储
- 后续做 CDN、权限控制、生命周期管理也更自然

## 2. 为什么企业版默认用对象存储

如果文件先传到 FastAPI：

```text
Browser -> FastAPI -> local disk
```

问题是：

- 大文件流量会经过业务服务
- 网卡、磁盘 IO、连接数都会被上传拖住
- 后续扩容时，应用服务和文件存储耦合
- 多实例部署后，本地文件一致性麻烦

如果改成对象存储直传：

```text
Browser -> OSS/S3/MinIO
Backend -> 只负责签名、状态、元数据
```

好处是：

- 上传流量绕过业务服务
- 文件持久化和应用实例解耦
- 天然适合大文件、并发上传、多实例部署
- 后续接分片上传、断点续传更顺

## 3. 推荐系统边界

建议把系统拆成四层。

### 3.1 Upload API

负责：

- 初始化上传任务
- 生成分片上传凭证
- 查询上传状态
- 完成上传

不负责：

- PDF 解析
- 文本切片
- embedding
- Elasticsearch 写入

### 3.2 Object Storage

负责：

- 保存原始文件
- 保存上传分片
- 保存合并后的最终对象

### 3.3 Document Processing Worker

负责：

- 拉取对象存储文件
- 解析 PDF / DOCX / XLSX / TXT / MD
- 生成结构化文本
- 失败重试

### 3.4 Index Worker

负责：

- split
- embedding
- 写 Elasticsearch
- 更新 chunks / documents / jobs 状态

## 4. 推荐数据模型

### 4.1 `upload_tasks`

```text
id
upload_id
knowledge_base_id
original_filename
storage_provider
bucket_name
object_key
file_type
mime_type
file_size
chunk_size
total_parts
file_sha256
status
completed_parts
error_message
created_at
updated_at
```

`status` 建议：

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

### 4.2 `upload_parts`

```text
id
upload_id
part_number
etag
part_size
part_sha256
status
created_at
updated_at
```

说明：

- 如果是 OSS/S3 multipart upload，分片上传成功后通常会返回 `etag`
- 完成上传时要把所有 part 的 `part_number + etag` 回传给对象存储完成合并

### 4.3 `documents`

建议补字段：

```text
storage_provider
bucket_name
object_key
file_size
file_sha256
mime_type
upload_status
parse_status
index_status
error_message
```

不要只留一个笼统的 `status`。

### 4.4 `index_jobs`

```text
id
document_id
knowledge_base_id
job_type
status
current_step
progress
retry_count
error_message
created_at
updated_at
```

## 5. 推荐接口设计

### 5.1 初始化上传任务

```http
POST /uploads/init
```

请求：

```json
{
  "knowledge_base_id": 7,
  "filename": "supplier-policy.pdf",
  "file_size": 734003200,
  "file_sha256": "optional-full-file-hash",
  "mime_type": "application/pdf"
}
```

返回：

```json
{
  "upload_id": "upl_xxx",
  "storage_provider": "minio",
  "bucket_name": "knowledge-raw-files",
  "object_key": "raw/2026/07/upl_xxx/supplier-policy.pdf",
  "chunk_size": 5242880,
  "total_parts": 141
}
```

### 5.2 获取分片上传凭证

```http
POST /uploads/{upload_id}/parts/presign
```

请求：

```json
{
  "part_number": 1
}
```

返回：

```json
{
  "part_number": 1,
  "upload_url": "presigned url ...",
  "headers": {}
}
```

### 5.3 上报分片完成

```http
POST /uploads/{upload_id}/parts/complete
```

请求：

```json
{
  "part_number": 1,
  "etag": "\"abc123\"",
  "part_size": 5242880,
  "part_sha256": "optional"
}
```

### 5.4 查询上传状态

```http
GET /uploads/{upload_id}
```

返回：

```json
{
  "upload_id": "upl_xxx",
  "status": "uploading",
  "total_parts": 141,
  "completed_parts": [1, 2, 3, 7],
  "missing_parts": [4, 5, 6]
}
```

### 5.5 完成上传

```http
POST /uploads/{upload_id}/complete
```

后端职责：

- 检查所有分片是否齐全
- 调对象存储完成 multipart upload
- 校验最终对象大小
- 可选校验最终 sha256
- 创建 `documents`
- 把 `upload_task.status` 改成 `completed`

返回：

```json
{
  "upload_id": "upl_xxx",
  "document_id": 23,
  "status": "completed"
}
```

### 5.6 创建索引任务

```http
POST /documents/{id}/index
```

这里不直接同步做完，而是创建 `index_job`。

## 6. 安全要求

企业版这里不能弱化。

### 6.1 文件类型校验

至少做四层：

- 扩展名白名单
- MIME 校验
- magic number 校验
- 对 zip 容器类文件检查内部结构

例子：

```text
pdf -> %PDF
docx -> ZIP + word/document.xml
xlsx -> ZIP + xl/workbook.xml
```

### 6.2 文件名安全

不要信任用户原始文件名作为真实存储路径。

建议：

```text
展示名：original_filename
存储键：系统生成 object_key
```

### 6.3 大小和复杂度限制

建议限制：

- 单文件最大大小
- PDF 最大页数
- Excel 最大 sheet 数
- 解压后最大体积
- 解析后最大文本长度

### 6.4 异步解析隔离

PDF / DOCX / XLSX 解析不要放在上传请求里。

要放到异步 worker，原因：

- 防止请求超时
- 防止解析异常把上传接口拖死
- 可以单独做超时、重试、限流

## 7. 资源和并发控制

企业版必须分池。

### 7.1 上传并发池

负责：

- 初始化上传
- 分片状态写库
- 生成签名

主要资源：

- 轻量 CPU
- DB
- 少量网络

### 7.2 处理并发池

负责：

- 拉对象
- parse

主要资源：

- CPU
- 内存
- 磁盘临时空间

### 7.3 索引并发池

负责：

- split
- embedding
- Elasticsearch 写入

主要资源：

- 模型服务 QPS / GPU
- ES 写入吞吐

建议分开限制：

```text
upload API 并发：高
parse worker 并发：中低
embedding worker 并发：低
```

不要用一个统一线程池。

## 8. 推荐阶段执行

虽然目标直接按企业版，但实现仍然应该分阶段。

### Phase 1：对象存储接入 + 上传任务模型

目标：

- 接 MinIO
- 落 `upload_tasks` / `upload_parts`
- 跑通 init / status / complete 接口骨架

学习点：

- 对象存储概念
- presigned URL
- upload task 状态机

交付物：

- `docs/large-file-upload/phase-01-object-storage-and-upload-contract.md`
- 后端 upload API 骨架

### Phase 2：分片上传 + 断点续传

目标：

- 前端按 part 上传
- 后端记录 part 状态
- 支持 missing parts 查询

学习点：

- multipart upload
- 幂等
- resume

交付物：

- `docs/large-file-upload/phase-02-multipart-upload-and-resume.md`

### Phase 3：文档落库 + 状态拆分

目标：

- `documents` 增加对象存储字段
- 拆 `upload_status / parse_status / index_status`

学习点：

- 状态机设计
- 上传链路与业务链路解耦

交付物：

- `docs/large-file-upload/phase-03-document-status-model.md`

### Phase 4：异步解析和索引任务

目标：

- 上传完成只创建 `document`
- 索引走 `index_jobs`
- 解析和 embedding 改成后台任务

学习点：

- job queue
- worker
- retry
- progress

交付物：

- `docs/large-file-upload/phase-04-async-processing-and-index-jobs.md`

### Phase 5：安全和治理

目标：

- MIME / magic number / zip 内部结构校验
- 上传限制
- 过期任务清理
- 错误治理

学习点：

- 文件安全
- 生命周期管理
- 可观测性

交付物：

- `docs/large-file-upload/phase-05-security-and-governance.md`

### Phase 6：生产级增强

目标：

- 限流
- 生命周期策略
- bucket 分层
- 观测指标
- 成本治理

学习点：

- S3/OSS 生命周期
- 冷热分层
- 企业运维视角

交付物：

- `docs/large-file-upload/phase-06-production-hardening.md`

## 9. 当前项目的具体建议

对于这个 RAG 项目，我建议直接这样定：

```text
原始文件：对象存储
上传方式：multipart upload
上传完成后：只创建 document
索引方式：异步 job
向量写入：继续走 Elasticsearch
```

这是最像企业项目的组合。

如果你后面面试表达，可以直接说：

```text
我把大文件上传设计成对象存储直传体系。前端先初始化 upload task，
后端返回对象存储 multipart upload 所需的信息，前端直接按分片上传到对象存储。
后端只负责状态、分片记录、完整性校验和文档元数据。
上传完成后不会同步做解析和索引，而是创建后台 index job，由 worker 完成 parse、
split、embedding 和 Elasticsearch 写入。这样上传流量不会压垮业务服务，
同时支持断点续传、失败恢复、状态跟踪和资源隔离。
```
