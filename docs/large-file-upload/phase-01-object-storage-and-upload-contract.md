# Phase 01 Object Storage And Upload Contract

这份文档对应当前仓库的大文件上传 Phase 1。

边界先说清楚：

- 以当前真实项目为准
- 保持 `PostgreSQL + SQLModel`
- 不引入 Alembic
- 不重做现有数据库架构
- 当前对象存储只接阿里云 OSS
- 当前只做上传任务模型和 API 骨架

## 1. Phase 1 目标

本阶段只解决一件事：

> 让“企业级上传”最核心的控制面先成立。

也就是先把这些基础设施固化下来：

- OSS 配置
- 对象存储适配器
- 上传任务主表
- 上传分片子表
- 初始化上传 API
- 查询上传任务 API
- 完成上传 API 骨架

这样 Phase 2 再接真实 multipart 上传时，就不是推翻重写，而是在现有合同上往前补。

## 2. 为什么 Phase 1 不直接做完整分片上传

因为当前仓库里已经有：

- 文档上传
- 文档解析
- 文本切片
- embedding
- Elasticsearch

如果这个阶段再同时把：

- multipart upload
- presign URL
- 断点续传
- Worker
- MQ
- 自动 parse/index

一起塞进来，风险会很高，而且你很难看清每一层是否真的稳定。

所以这里刻意拆成两层：

### Phase 1 做

- 合同
- 状态
- 表结构
- 适配器边界

### Phase 2 再做

- 真正的数据上传流程
- part 级别恢复
- OSS 完整性校验

这个拆法更稳。

## 3. 当前配置

当前 Phase 1 采用阿里云 OSS，配置如下：

```env
OSS_ENDPOINT=oss-cn-shanghai.aliyuncs.com
OSS_REGION=cn-shanghai
OSS_BUCKET=ai-knowledge-hub-xueyuan-dev
OSS_STORAGE_PREFIX=raw/dev
OSS_PRESIGN_EXPIRE_SECONDS=900
```

下面两个值只从本地环境变量读取：

```env
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
```

要求：

- 禁止硬编码
- 禁止写入日志
- 禁止提交到 Git

## 4. 当前对象路径规则

对象路径统一由后端生成：

```text
raw/dev/{knowledge_base_id}/{upload_id}/source.{extension}
```

例如：

```text
raw/dev/7/upl_2f8797e6a38c4d01/source.pdf
```

这里的设计重点是：

- 用户原始文件名只用于展示
- 不直接进入 object key
- object key 始终可预测、可审计、可追溯

## 5. 当前代码结构

Phase 1 建议的代码分层如下：

```text
backend/app/config.py
  -> OSS 配置

backend/app/db/models.py
  -> UploadTask / UploadPart

backend/app/services/storage/base.py
  -> ObjectStorageAdapter 协议

backend/app/services/storage/aliyun_oss.py
  -> 阿里云 OSS 实现

backend/app/services/storage/provider.py
  -> 适配器工厂

backend/app/services/upload_service.py
  -> 上传业务逻辑

backend/app/api/upload.py
  -> /uploads API
```

这个边界的价值是：

- API 层只接请求和响应
- service 层做业务规则
- storage adapter 层负责和 OSS SDK 交互

后面要换 provider，不用重写上传业务。

## 6. 数据模型说明

### 6.1 `upload_tasks`

主表负责描述一次上传任务。

建议字段：

```text
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

其中几个关键字段：

- `upload_id`

  - 业务侧稳定 ID
  - 用于前端轮询和 complete 调用
- `storage_upload_id`

  - OSS multipart upload 的真实 upload_id
  - 属于对象存储内部标识
- `part_size`

  - 单片大小
- `total_parts`

  - 总片数

### 6.2 `upload_parts`

子表负责描述每一片的状态。

```text
upload_task_id
part_number
etag
part_size
part_sha256
status
created_at
updated_at
```

这里先做结构，不要求 Phase 1 真写入 part 记录。

## 7. 状态设计

当前建议状态：

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

Phase 1 真正会用到的主要是：

- `initiated`
- `completed`
- `failed`

其他状态先保留结构位，给 Phase 2 和 Phase 3 使用。

## 8. API 合同

### 8.1 `POST /uploads/init`

作用：

- 校验知识库存在
- 校验文件名 / 文件大小 / 扩展名
- 生成 `upload_id`
- 生成 `object_key`
- 调用 OSS 初始化 multipart upload
- 落库 `upload_tasks`

请求体：

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

响应体：

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

### 8.2 `GET /uploads/{upload_id}`

作用：

- 查询上传任务元数据
- 供前端刷新状态

返回：

- 当前状态
- 对象路径
- part 信息字段
- storage_upload_id

### 8.3 `POST /uploads/{upload_id}/complete`

作用：

- 作为完成上传的协议入口

但 Phase 1 只做骨架，不做完整 multipart complete。

默认行为：

- 返回“接口合同已存在，真实 complete 在 Phase 2”

可选测试行为：

- 允许通过 `force_complete_for_testing=true` 触发最小闭环

这样可以在测试里校验状态迁移和接口通路。

## 9. Phase 1 测试重点

建议至少覆盖这些点：

- object key 生成是否符合规则
- 非法文件名是否被拒绝
- 非法扩展名是否被拒绝
- file_size 是否被限制
- 初始化上传是否会落库
- storage_upload_id 是否会保存
- 上传任务查询接口是否可用
- complete 接口是否返回受控响应
- upload_parts 的唯一约束是否存在

## 10. Phase 1 明确不做的内容

这些都故意留到下一阶段：

- 真实分片上传 URL 下发
- part 完成回调
- 断点续传恢复
- 服务端对 OSS 已上传分片的比对
- 上传完成后自动创建 `documents`
- 上传完成后自动 parse / split / embed / index

## 11. Phase 2 接口方向

下一阶段建议补：

```http
POST /uploads/{upload_id}/parts/presign
POST /uploads/{upload_id}/parts/complete
POST /uploads/{upload_id}/abort
GET  /uploads/{upload_id}/parts
```

这样前端才能真正做：

- 直接上传到 OSS
- 中断后续传
- 重试失败 part
- 最终 complete

## 12. 当前结论

Phase 1 的价值不是“把大文件上传全做完”，而是：

> 先把企业级上传最关键的合同层和状态层搭对。

只要这一层是对的，后面加 presign、断点续传、异步处理，就会顺很多。
