# Large File Upload Roadmap

这份文档记录后续“大文件上传”能力的改造计划。

目标不是一次性把企业级上传全部做完，而是按阶段推进：每一阶段都能学到一个明确的后端知识点，同时能落到当前 AI Knowledge Hub 项目里。

## 1. 为什么要改造上传链路

当前项目已经把“上传文件”和“构建索引”拆开了：

```text
上传文件
  -> 保存原文件
  -> 写 documents 表
  -> status = uploaded

点击构建索引
  -> 解析文件
  -> 切 chunks
  -> embedding
  -> 写 Elasticsearch
  -> status = indexed / failed
```

这个方向是对的。

但如果文件变大，当前上传链路还需要补这些能力：

- 避免一次性把大文件读进内存
- 限制单文件大小和上传类型
- 上传失败时不留下半成品文件
- 记录文件 hash，支持完整性校验和去重
- 控制多人同时上传时的磁盘、网络、CPU 占用
- 后续支持分片上传和断点续传
- 解析、切片、embedding 后台异步化

## 2. 企业级目标架构

最终可以演进成这条链路：

```text
前端
  -> 初始化上传任务
  -> 分片上传
  -> 查询上传进度
  -> 完成上传

后端
  -> 校验文件类型 / 大小 / hash
  -> 合并分片
  -> 写 documents
  -> 创建 index job

后台 worker
  -> parse
  -> split
  -> embed
  -> write Elasticsearch
  -> update status
```

对应接口形态：

```text
POST /uploads/init
PUT /uploads/{upload_id}/parts/{part_number}
GET /uploads/{upload_id}
POST /uploads/{upload_id}/complete

POST /documents/{id}/index
GET /index-jobs/{job_id}
```

第一版不必全部实现。下面按阶段推进。

## Phase 1：普通上传先变稳

目标：不做分片，先把当前 `POST /documents/upload` 改成更可靠的大文件友好版本。

学习点：

- 流式读取
- 临时文件
- 文件大小限制
- 安全文件名
- sha256 hash
- 上传失败清理

要做的事：

1. 后端配置上传限制

```text
MAX_UPLOAD_FILE_SIZE_MB=200
UPLOAD_STREAM_CHUNK_SIZE_MB=1
```

2. 上传时不要一次性读取完整文件

不要这样：

```python
content = await file.read()
```

改成：

```python
while chunk := await file.read(1024 * 1024):
    ...
```

3. 先写 `.part` 临时文件

```text
data/uploads/tmp/<uuid>.part
```

全部写入成功后，再原子 rename 到最终路径：

```text
data/uploads/<uuid>.pdf
```

这样中途失败不会出现“看起来像完整文件但其实坏了”的文件。

4. 上传过程中累计大小

```text
received_bytes += len(chunk)
if received_bytes > max_upload_size:
    删除 .part
    返回 413 Payload Too Large
```

5. 同步计算 sha256

边写文件边计算：

```text
hash.update(chunk)
```

最后写入 `documents.file_sha256`。

6. 文件名只用于展示

用户原始文件名只存数据库，不直接作为保存路径。

保存路径使用：

```text
uuid + 安全扩展名
```

验收标准：

- 上传 100MB 文件不会明显占用过高内存
- 上传超过限制的文件返回 413
- 上传失败不会留下最终文件
- `documents` 表能看到 `file_size` / `file_sha256`
- 原有 txt / md / pdf / docx / xlsx 上传功能不退化

## Phase 2：补文件安全校验

目标：不要只靠文件后缀判断文件类型。

学习点：

- MIME
- magic number
- zip 容器结构
- 路径穿越
- 解析安全

要做的事：

1. 扩展名白名单

```text
txt / md / pdf / docx / xlsx / csv
```

2. magic number 检查

示例：

```text
PDF: 文件头包含 %PDF
DOCX/XLSX: 文件头是 PK，也就是 zip 容器
```

3. DOCX/XLSX 进一步检查内部结构

```text
docx: word/document.xml
xlsx: xl/workbook.xml
```

4. 路径安全

禁止使用用户文件名拼接保存路径。

必须保证最终路径在：

```text
backend/data/uploads
```

目录内。

5. 解析前限制复杂度

后续可以加：

- PDF 最大页数
- Excel 最大 sheet 数
- 解压后最大体积
- 单文档最大抽取文本长度

验收标准：

- 把 exe 改名成 pdf，上传会被拒绝
- 普通 zip 改名成 docx/xlsx，会被拒绝
- 文件名包含 `../` 不会影响保存路径

## Phase 3：上传状态和元数据升级

目标：让数据库更准确地表达上传和处理状态。

建议给 `documents` 增加字段：

```text
file_size
file_sha256
mime_type
upload_status
parse_status
index_status
error_message
```

或者先保持一个 `status`，但状态值升级为：

```text
uploading
uploaded
parse_failed
indexing
indexed
failed
```

更推荐后面拆成多个状态字段，因为上传、解析、索引是不同阶段。

验收标准：

- 前端可以区分“上传失败”和“索引失败”
- 后端错误能写入 `error_message`
- 页面上能看到文件大小和 hash

## Phase 4：分片上传和断点续传

目标：支持真正的大文件上传。

学习点：

- 上传任务
- 分片表
- 幂等
- 断点续传
- 分片合并
- hash 校验

新增表：

```text
upload_tasks
- id
- upload_id
- knowledge_base_id
- original_filename
- file_type
- file_size
- chunk_size
- total_parts
- uploaded_parts
- file_sha256
- status
- temp_dir
- final_path
- error_message
- created_at
- updated_at
```

```text
upload_parts
- id
- upload_id
- part_number
- part_size
- part_sha256
- storage_path
- status
- created_at
```

接口：

```text
POST /uploads/init
PUT /uploads/{upload_id}/parts/{part_number}
GET /uploads/{upload_id}
POST /uploads/{upload_id}/complete
DELETE /uploads/{upload_id}
```

关键规则：

- 同一个 `upload_id + part_number` 重复上传要幂等
- `complete` 时必须检查所有分片齐全
- 合并时按 `part_number` 顺序写入
- 合并后计算整体 sha256
- hash 不一致则标记 failed，不进入 documents

验收标准：

- 上传到一半刷新页面，可以查询缺失分片继续上传
- 同一个分片重复上传不会生成重复记录
- 故意漏一个分片，complete 会失败
- 合并后的文件 hash 和前端声明一致

## Phase 5：并发和资源控制

目标：多人同时上传时，不把服务资源打爆。

学习点：

- 网络 IO
- 磁盘 IO
- CPU 密集任务
- 队列隔离
- 限流

要控制的资源：

```text
上传阶段：网络 IO + 磁盘写入
解析阶段：CPU + 内存
embedding 阶段：模型服务 QPS / GPU / API 额度
索引阶段：Elasticsearch 写入压力
```

推荐限制：

```text
单用户同时 uploading 任务数：3
单知识库同时 uploading 任务数：5
全局上传并发：10
解析并发：2
embedding 并发：1-2
临时目录总大小：例如 20GB
过期上传任务清理：24 小时
```

为什么要分开限流：

```text
上传可以多一点，因为主要吃网络和磁盘。
解析要少一点，因为 PDF/DOCX/XLSX 解析吃 CPU 和内存。
embedding 更要少一点，因为模型服务和 API 额度有限。
```

验收标准：

- 超过用户并发限制时返回 429
- 临时目录超过限制时返回 507
- 过期 `.part` 文件能被清理
- 解析任务不会因为上传多而无限并发

## Phase 6：索引异步任务化

目标：`POST /documents/{id}/index` 不再同步完成所有重活，而是创建后台任务。

新增表：

```text
index_jobs
- id
- document_id
- knowledge_base_id
- status
- current_step
- progress
- error_message
- created_at
- updated_at
```

任务状态：

```text
pending
parsing
splitting
embedding
indexing
completed
failed
```

接口：

```text
POST /documents/{id}/index
GET /index-jobs/{job_id}
```

第一版可以用 FastAPI `BackgroundTasks`。

更企业级可以换成：

```text
Celery / RQ / Dramatiq / Arq
```

验收标准：

- 点击构建索引后立即返回 `job_id`
- 前端能看到阶段进度
- 失败后能看到错误原因
- 同一个 document 重复点击 index 不会重复创建多个运行中任务

## Phase 7：对象存储直传

目标：大文件流量不再经过后端业务服务。

适用场景：

```text
文件很大
用户很多
后端服务不想承受上传带宽
生产环境使用 OSS/S3/MinIO
```

对象存储包括：

```text
阿里云 OSS
AWS S3
腾讯云 COS
MinIO
```

链路：

```text
前端 -> 后端申请上传凭证
后端 -> 返回预签名 URL
前端 -> 直传 OSS/S3/MinIO
前端 -> 通知后端上传完成
后端 -> 校验对象信息，写 documents
```

验收标准：

- 后端不直接接收大文件 body
- 前端可以直接上传到对象存储
- 后端能校验 object key / size / hash
- documents 表保存对象存储路径

## 推荐执行顺序

结合当前项目，建议按这个顺序做：

```text
1. Phase 1：普通上传流式化
2. Phase 2：文件安全校验
3. Phase 3：documents 元数据升级
4. Phase 6：索引异步任务化
5. Phase 4：分片上传
6. Phase 5：并发和资源控制
7. Phase 7：对象存储直传
```

原因：

- 先改普通上传，收益最大，风险最低
- 安全校验属于上传基础设施，应该尽早补
- 状态字段补齐后，前端才能正确展示过程
- 索引异步化比断点续传更贴合当前 RAG 项目
- 分片上传和对象存储属于更高阶能力，可以放在后面

## 面试表达模板

可以这样讲：

```text
我会把大文件上传设计成上传任务体系，而不是一个接口直接接收完整文件。

第一阶段先把普通上传做稳：后端流式读取、写 .part 临时文件、限制大小、
计算 sha256、校验文件类型，成功后再原子移动到最终目录。

第二阶段做分片上传：前端初始化 upload task，按 part_number 上传分片，
后端记录每个分片状态，complete 时检查分片完整性、按顺序合并并校验 hash。

第三阶段把上传和索引彻底解耦：上传只负责可靠落盘和写 documents，
解析、切片、embedding、写 Elasticsearch 走后台任务，并且上传、解析、
embedding 分别设置不同并发限制，避免网络 IO、CPU 和模型服务资源互相拖垮。

生产环境如果文件更大或用户更多，会进一步使用 OSS/S3/MinIO 直传，
让大文件流量绕过后端业务服务。
```

