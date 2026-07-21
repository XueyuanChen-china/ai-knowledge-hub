# OSS 大文件上传完整流程复习

本文按照当前项目的真实实现，复习一次从用户选择文件，到 OSS 上传完成，再到文档解析、切片、Embedding 和 Elasticsearch 入库的完整链路。

重点使用这次测试的文件作为例子：

```text
backend/tests/fixtures/splitter_regression/samples/plain_text_policy.txt
```

这个文件比较小，所以实际只会被切成 1 个上传分片，但它可以完整演示 Multipart Upload 的所有步骤。

## 一、先区分三个 ID

上传流程里最容易混淆的是三个不同概念。

### 1. 业务 upload_id

例如：

```text
upl_721031c76a744bc4
```

这是后端生成并返回给前端的上传任务 ID。前端后续调用接口时使用它：

```text
/uploads/upl_721031c76a744bc4/parts/presign
/uploads/upl_721031c76a744bc4/parts/complete
/uploads/upl_721031c76a744bc4/complete
```

它对应 PostgreSQL 中 `upload_tasks.upload_id`。

### 2. OSS storage_upload_id

例如：

```text
5ED061630ADF41F1803D88E450406759
```

这是 OSS 创建 Multipart Upload 后返回的内部上传会话 ID。

它不会作为主要业务 ID 暴露给前端，但后端必须保存它，因为后续 OSS API 需要依靠它识别同一个分片上传任务。

它对应：

```text
upload_tasks.storage_upload_id
```

### 3. document_id

文件完整合并并进入后处理流程后，后端会创建 `documents` 记录，得到 `document_id`。

这个 ID 代表知识库中的文档，不代表上传过程本身。

可以这样理解：

```text
upload_id          = 运输单号
storage_upload_id  = OSS 仓库里的分批装箱单号
document_id        = 文件进入知识库后的档案编号
```

## 二、整体架构

```text
浏览器 / 前端
    |
    | 1. 请求上传任务
    v
FastAPI
    |
    | 写入任务状态
    v
PostgreSQL: upload_tasks / upload_parts / upload_processing_jobs
    |
    | 2. 创建 Multipart Upload、生成预签名 URL
    v
阿里云 OSS
    |
    | 3. 浏览器直接 PUT 文件分片
    v
OSS Multipart Upload
    |
    | 4. 后端确认所有 part，并调用 complete
    v
OSS 完整原始文件
    |
    | 5. 创建阶段级处理 job
    v
Celery + RabbitMQ
    |
    v
download -> validate -> parse -> split -> embed -> index
    |
    v
PostgreSQL documents/chunks + Elasticsearch 向量索引
```

这里最重要的设计是：

- 原始文件放 OSS，不放 PostgreSQL；
- PostgreSQL 保存任务、分片、状态和业务关联；
- Elasticsearch 保存向量检索数据；
- RabbitMQ 负责传递异步任务消息；
- Celery Worker 负责执行处理阶段。

## 三、准备测试文件

本次示例使用：

```text
tests/fixtures/splitter_regression/samples/plain_text_policy.txt
```

测试脚本先读取文件的几个基础信息：

```text
filename: plain_text_policy.txt
file_type: txt
client_mime_type: text/plain
file_size: 278 bytes
```

项目默认分片大小是：

```text
part_size = 5,242,880 bytes = 5 MiB
```

因此：

```text
total_parts = ceil(278 / 5,242,880) = 1
```

虽然只有 1 个 part，但仍然会经过完整的 Multipart Upload 流程。真正的大文件会得到多个 part，例如 12 MiB 文件可能得到 3 个 part。

## 四、阶段一：初始化上传任务

接口：

```http
POST /uploads/init
```

请求示例：

```json
{
  "knowledge_base_id": 8,
  "filename": "plain_text_policy.txt",
  "file_size": 278,
  "client_mime_type": "text/plain",
  "file_sha256": "客户端计算出的 SHA256",
  "created_by": "e2e-test"
}
```

后端执行顺序：

### 1. 校验请求

后端检查：

- 知识库是否存在；
- 文件名是否为空；
- 文件大小是否大于 0；
- 文件大小是否超过配置上限；
- 后缀是否属于允许类型；
- 当前用户或创建者的并发上传数和磁盘配额是否超限。

此时只相信文件名和客户端 MIME 的程度有限。真正的文件类型检测会在后续 `validate` 阶段再次进行。

### 2. 生成 object_key

后端不会直接使用用户文件名作为 OSS 路径，而是生成：

```text
raw/dev/8/upl_721031c76a744bc4/source.txt
```

格式是：

```text
{OSS_STORAGE_PREFIX}/{knowledge_base_id}/{upload_id}/source.{extension}
```

这样可以避免路径穿越、特殊字符、重名覆盖和用户控制目录等问题。

### 3. 调用 OSS 初始化 Multipart Upload

后端调用 OSS，得到：

```text
storage_upload_id = 5ED061630ADF41F1803D88E450406759
```

这一步只是在 OSS 创建一个“等待分片上传的会话”，文件本体此时还没有完整写入。

### 4. 写入 PostgreSQL

后端创建 `upload_tasks`：

```text
upload_id              = upl_721031c76a744bc4
knowledge_base_id      = 8
original_filename      = plain_text_policy.txt
object_key             = raw/dev/8/upl_721031c76a744bc4/source.txt
file_size              = 278
part_size              = 5242880
total_parts            = 1
storage_upload_id      = 5ED061630ADF41F1803D88E450406759
status                 = initiated
processing_status      = pending
auto_create_document   = true
auto_index_on_complete = true
```

接口返回给前端的主要是：

```json
{
  "upload_id": "upl_721031c76a744bc4",
  "storage_provider": "aliyun-oss",
  "bucket_name": "ai-knowledge-hub-xueyuan-dev",
  "object_key": "raw/dev/8/upl_721031c76a744bc4/source.txt",
  "part_size": 5242880,
  "total_parts": 1,
  "status": "initiated"
}
```

## 五、阶段二：申请分片预签名 URL

接口：

```http
POST /uploads/{upload_id}/parts/presign
```

请求：

```json
{
  "part_number": 1
}
```

后端会检查：

- upload_id 是否存在；
- 任务是否过期、取消或已经完成；
- part_number 是否在 `1..total_parts` 范围内；
- 当前分片重试次数是否超过上限。

然后生成 OSS 预签名 URL。URL 中包含：

```text
object_key
uploadId=5ED061630ADF41F1803D88E450406759
partNumber=1
过期时间
签名
```

当前实现还会把：

```text
Content-Type: text/plain
```

纳入签名。因此客户端真正 PUT 时必须使用同一个 Content-Type。

后端同时创建或更新 `upload_parts`：

```text
upload_task_id = upload_tasks.id
part_number    = 1
status         = pending
retry_count    = 1
```

此时数据库只表示“准备上传第 1 片”，不能表示 OSS 已经收到文件。

## 六、阶段三：客户端直接上传到 OSS

前端拿到预签名 URL 后，不把文件内容再次传给 FastAPI，而是直接：

```http
PUT https://ai-knowledge-hub-xueyuan-dev.oss-cn-shanghai.aliyuncs.com/...
Content-Type: text/plain

278 bytes of plain_text_policy.txt
```

为什么直接传 OSS：

```text
错误方式：浏览器 -> FastAPI -> OSS
正确方式：浏览器 -> OSS
```

错误方式会让 FastAPI 承担文件中转，浪费应用服务器的网络带宽、连接数和磁盘 IO。预签名 URL 允许前端在不拿到 OSS 密钥的情况下，临时获得一次受限 PUT 权限。

上传成功后，OSS 返回 HTTP ETag，例如：

```text
"6ABE1AD6BEFA0ECE49683532C7FFE8D2"
```

ETag 是 OSS 对这一个 part 的识别值。客户端必须把它保存下来，后续告诉后端。

注意：ETag 不等同于整文件 SHA256。ETag 用于 OSS Multipart 完成；整文件 SHA256 用于更强的完整性校验。

## 七、阶段四：确认单个 part

接口：

```http
POST /uploads/{upload_id}/parts/complete
```

请求：

```json
{
  "part_number": 1,
  "etag": "6ABE1AD6BEFA0ECE49683532C7FFE8D2",
  "part_size": 278
}
```

这里的 `complete` 不是完成整个文件，而是“确认这一个分片”。

后端执行：

1. 查询 OSS `list_parts`；
2. 确认 OSS 确实存在 `part_number=1`；
3. 比较请求 ETag 和 OSS ETag；
4. 比较请求 part_size 和 OSS 记录的大小；
5. 将结果写入 `upload_parts`；
6. 更新 `upload_tasks.completed_parts`；
7. 如果所有分片都完成，把任务状态推进为 `uploaded`。

对于本次 1 片文件，确认后大致是：

```text
upload_parts.part_number = 1
upload_parts.etag        = 6ABE1AD6BEFA0ECE49683532C7FFE8D2
upload_parts.part_size   = 278
upload_parts.status      = uploaded
upload_tasks.completed_parts = 1
upload_tasks.status      = uploaded
```

### 本次测试遇到的第一个问题

第一次测试在上一步之前失败：

```text
PUT OSS -> 403 SignatureDoesNotMatch
```

原因是预签名 URL 没有把 `Content-Type` 纳入签名，但脚本 PUT 时发送了 `Content-Type: text/plain`。OSS 计算出的签名和 URL 中的签名不一致，所以拒绝上传。

修复后，预签名和 PUT 使用同一个 MIME 类型，分片成功上传。

### 本次测试遇到的第二个问题

修复第一个问题后，分片已经成功进入 OSS，但确认接口返回：

```text
409 etag does not match object storage part record
```

当时状态是：

```text
HTTP PUT 返回的 ETag:  "6ABE..."
OSS list_parts 返回:    6ABE...
```

实际值相同，只是一个带双引号，一个不带。后端原来直接比较字符串，误判为不相同。

现在通过 `normalize_etag()` 统一处理：

```python
str(etag or "").strip().strip('"')
```

这属于“分片确认阶段的格式兼容问题”，不是 OSS 上传失败。

## 八、阶段五：完成整个 Multipart Upload

接口：

```http
POST /uploads/{upload_id}/complete
```

请求：

```json
{
  "expected_total_parts": 1
}
```

后端不会直接盲目调用 OSS complete，而是先做完整校验：

### 1. 校验本地分片是否齐全

本例要求：

```text
本地 uploaded part = {1}
期望 part           = {1}
```

如果缺少任何分片，返回 409，不允许合并。

### 2. 校验 OSS 分片是否齐全

后端再次调用 `list_parts`，确认 OSS 侧也有：

```text
part 1
```

这是为了防止 PostgreSQL 记录显示已完成，但 OSS 实际没有对应对象。

### 3. 校验每个 ETag

后端逐个比较：

```text
upload_parts.etag == OSS list_parts.etag
```

确认通过后，构造 OSS complete 请求：

```json
[
  {
    "part_number": 1,
    "etag": "6ABE1AD6BEFA0ECE49683532C7FFE8D2"
  }
]
```

### 4. OSS 合并分片

OSS 根据 `storage_upload_id` 和 part 列表，把：

```text
part 1
part 2
part 3
...
```

合并成一个完整对象：

```text
raw/dev/8/upl_721031c76a744bc4/source.txt
```

对于本次文件只有一个 part，因此是“单片 Multipart 合并”，但仍然走同一套接口。

### 5. 更新 PostgreSQL

OSS 合并成功后，后端更新：

```text
upload_tasks.status          = completed
upload_tasks.completed_parts = 1
upload_tasks.updated_at      = 当前时间
```

只有这一步成功后，才说明“原始文件已经完整落在 OSS 中”。

## 九、阶段六：创建后处理任务

整个文件上传完成后，后端调用 `enqueue_processing_job()`，创建第一条阶段 job：

```text
upload_processing_jobs:

id     stage      status   depends_on_job_id
101    download   pending  null
```

如果当前配置是 Celery：

```text
FastAPI
  -> RabbitMQ 投递 uploads.download
  -> Celery Worker 消费
```

数据库中的 `celery_task_id` 会记录 Celery 任务 ID，方便追踪。

注意：`POST /uploads/{upload_id}/complete` 不是同步完成解析和索引。它只负责：

```text
完成 OSS Multipart Upload
创建第一个处理 job
投递 download task
立即返回上传完成和处理已排队
```

## 十、阶段七：阶段级处理流水线

当前流水线是：

```text
download -> validate -> parse -> split -> embed -> index
```

每个阶段都是单独的 `upload_processing_jobs` 记录。

### 1. download

从 OSS 下载原始对象到后端临时目录，并进行流式 SHA256 计算。

主要检查：

- OSS 对象是否存在；
- 下载字节数是否符合预期；
- 如果提供了 SHA256，实际 SHA256 是否一致；
- 保存文件头字节，供后续 magic number 检查。

完成后创建：

```text
stage=validate
depends_on_job_id=download_job.id
```

### 2. validate

校验文件真实类型和基本结构，例如：

- TXT / MD 是否是可读文本；
- PDF 是否具有 PDF 文件头并能被解析器打开；
- DOCX 是否是合法 ZIP 容器并能被 `python-docx` 打开；
- XLSX 是否是合法 ZIP 容器并能被 `openpyxl` 打开。

这一步不会只相信扩展名。

### 3. parse

创建 `documents` 记录，并调用对应 parser：

```text
txt  -> plain text parser
md   -> markdown parser
pdf  -> PDF parser
docx -> DOCX parser
xlsx -> Excel parser
```

本例会创建类似记录：

```text
documents.id       = 13
documents.filename = plain_text_policy.txt
documents.file_type = txt
documents.status   = uploaded / processing
```

### 4. split

把解析结果转换为 sections、blocks、chunks，并写入 PostgreSQL 的 `chunks` 表。

每个 chunk 包含：

- `document_id`；
- `knowledge_item_id`；
- `chunk_index`；
- 文本内容；
- heading、来源和位置 metadata；
- 后续回填的 `vector_id`。

### 5. embed

读取 chunks 内容，调用 BGE-M3 生成向量。

当前实现把 embedding 暂存在：

```text
chunks.embedding_json
```

这是为了让 `embed` 和 `index` 成为两个可追踪、可重试的阶段。embedding 生成完后，状态推进到 `index`。

### 6. index

读取暂存向量，写入 Elasticsearch：

```text
knowledge_chunks_{knowledge_base_id}
```

写入成功后：

```text
chunks.vector_id       = Elasticsearch 文档 ID
chunks.embedding_json  = 清空
documents.status       = indexed
upload_processing_jobs.status = completed
```

最终可以通过语义搜索查询这个文件的 chunk。

## 十一、完整状态变化

### upload_tasks

本例的主要状态变化：

```text
initiated
  -> uploading
  -> uploaded
  -> completed
```

如果 OSS 合并失败：

```text
completed multipart 失败 -> failed
```

如果用户取消：

```text
initiated/uploading -> cancelled
```

如果超过过期时间：

```text
initiated/uploading/uploaded -> expired
```

### upload_parts

本例的第 1 片：

```text
不存在
  -> pending       申请 presigned URL
  -> uploaded      OSS PUT 成功并通过 parts/complete 校验
```

### upload_processing_jobs

正常链路：

```text
download: pending -> running -> completed
validate: pending -> running -> completed
parse:    pending -> running -> completed
split:    pending -> running -> completed
embed:    pending -> running -> completed
index:    pending -> running -> completed
```

失败时可能是：

```text
running -> retry_scheduled -> running
running -> failed
```

## 十二、断点续传时发生什么

假设一个大文件有 3 个分片：

```text
part 1 上传成功
part 2 上传成功
part 3 上传失败
```

前端重新打开页面后调用：

```http
GET /uploads/{upload_id}/parts
```

后端同时查询：

- PostgreSQL `upload_parts`；
- OSS `list_parts`。

返回：

```json
{
  "completed_parts": 2,
  "missing_part_numbers": [3]
}
```

前端只需要重新申请并上传第 3 片，不需要重新上传前两片。

这就是 `upload_tasks` 和 `upload_parts` 不能只存在内存中的原因：页面刷新、浏览器崩溃或客户端重启后，仍然需要恢复进度。

## 十三、这次两个报错分别说明了什么

### SignatureDoesNotMatch

位置：

```text
presign 完成之后，客户端 PUT 到 OSS
```

含义：请求本身到达了 OSS，但签名校验不通过。

排查顺序：

1. 预签名 URL 是否过期；
2. endpoint、bucket、object_key 是否一致；
3. `uploadId`、`partNumber` 是否一致；
4. 签名时使用的 Header 是否和实际 PUT 一致；
5. AccessKey 是否属于正确的 OSS 账号和地域。

### etag does not match object storage part record

位置：

```text
OSS PUT 成功之后，调用 /parts/complete
```

含义：文件已经上传到 OSS，但后端确认时认为客户端 ETag 和 OSS ETag 不一致。

本次具体是双引号格式差异，不是内容真的不同。修复后统一去除 ETag 首尾空白和双引号。

## 十四、最终验收检查

### 接口层

```bash
curl http://127.0.0.1:8000/uploads/{upload_id}
curl http://127.0.0.1:8000/uploads/{upload_id}/parts
```

### PostgreSQL

```sql
SELECT upload_id, status, completed_parts, total_parts,
       document_id, processing_status, processing_error_message
FROM upload_tasks
ORDER BY id DESC;

SELECT upload_task_id, part_number, etag, part_size, status
FROM upload_parts
ORDER BY upload_task_id DESC, part_number;

SELECT id, upload_task_id, stage, status, depends_on_job_id,
       attempt_count, celery_task_id, error_message
FROM upload_processing_jobs
ORDER BY id;

SELECT id, filename, status
FROM documents
ORDER BY id DESC;

SELECT id, document_id, chunk_index, vector_id
FROM chunks
ORDER BY document_id, chunk_index;
```

### 预期结果

```text
upload_tasks.status = completed
upload_tasks.completed_parts = upload_tasks.total_parts
upload_parts.status = uploaded
upload_processing_jobs 包含 download/validate/parse/split/embed/index
所有阶段 status = completed
documents.status = indexed
chunks.vector_id 不为空
```

## 十五、代码阅读入口

按这条顺序阅读最容易建立整体认识：

1. `backend/scripts/test_large_upload_e2e.py`
   - 看客户端如何串起所有接口。
2. `backend/app/api/upload.py`
   - 看 HTTP 接口如何接收请求和返回结果。
3. `backend/app/services/upload_service.py`
   - 看上传状态、ETag、OSS complete 和 job 入队。
4. `backend/app/services/storage/base.py`
   - 看对象存储抽象接口。
5. `backend/app/services/storage/aliyun_oss.py`
   - 看抽象接口如何落到阿里云 OSS SDK。
6. `backend/app/db/models.py`
   - 看 `upload_tasks`、`upload_parts`、`upload_processing_jobs` 的数据库结构。
7. `backend/app/services/upload_postprocess_service.py`
   - 看六个后处理阶段如何串联。
8. `backend/app/tasks/upload_tasks.py`
   - 看 Celery 如何消费各个阶段 job。

一句话总结：

```text
PostgreSQL 记录“应该上传什么、上传到哪、完成了多少、下一步做什么”；
OSS 保存“真实文件和 Multipart 分片”；
Celery 负责“文件上传完成后如何异步处理”。
```
