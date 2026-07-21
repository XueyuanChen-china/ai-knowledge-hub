# Phase 03 Post Upload Processing And Control

这份文档对应当前仓库的大文件上传 Phase 3。

这一阶段做的不是“再多几个上传接口”，而是把上传系统开始往真正业务链路推进：

> 文件传完以后，不再停在 OSS，而是自动进入 document 创建和后续 parse/index 流程。

同时，这一层还顺手补了几个企业里很常见的控制项：

- 批量 presign
- part 重试次数控制
- 上传任务过期清理
- 上传后处理 job 状态

## 1. Phase 3 做了什么

当前已经补上的能力：

- `POST /uploads/{upload_id}/parts/presign-batch`
- `POST /uploads/cleanup-expired`
- `upload_parts.retry_count`
- `upload_tasks.expires_at`
- `upload_tasks.document_id`
- `upload_tasks.processing_status`
- `upload_tasks.processing_error_message`
- `upload_processing_jobs` 表
- 上传完成后自动创建 `documents`
- 上传完成后自动触发 parse / split / embed / index

## 2. 为什么要把“上传完成”和“后处理”拆开

如果把上传完成后的一切都继续堆在 `/uploads/{upload_id}/complete` 这个接口里，代码会越来越乱：

- 上传逻辑一层
- 文档创建一层
- 文本抽取一层
- 切片一层
- 向量索引一层

这些本来就不是一个职责。

所以这次虽然还是同步执行，但结构已经拆成两段：

### 第一段：Upload Control

负责：

- multipart 完成
- part 校验
- upload 状态流转

### 第二段：Post Process

负责：

- 下载对象
- 校验 hash
- 创建 document
- 触发 parse / split / embed / index
- 记录 processing job 状态

也就是说：

```text
upload complete
  -> process_completed_upload_task(...)
```

今天先同步执行，后面切异步 worker 时，主要搬运的是第二段。

## 3. 批量 presign 做了什么

之前只有：

```http
POST /uploads/{upload_id}/parts/presign
```

现在新增：

```http
POST /uploads/{upload_id}/parts/presign-batch
```

请求：

```json
{
  "part_numbers": [1, 2, 3]
}
```

返回：

- 多个 `part_number -> presigned_url`
- `recommended_parallelism`

这里不是强行控制前端并发，而是给前端一个明确的建议值。

## 4. 并发上传控制现在怎么做

这一版没有直接在后端“拦网络连接并发数”，因为前端直传 OSS 后，真正的数据面已经不经过 FastAPI 了。

所以当前更合理的控制方式是：

- 后端给 `recommended_parallelism`
- 前端按这个值控制同时上传多少个 part
- 后端再限制批量 presign 一次最多多少个

这样做的好处是：

- 简单
- 可落地
- 不会伪装成后端能控制 OSS 上传并发

## 5. part 重试次数控制怎么做

每个 `upload_part` 现在有：

- `retry_count`
- `last_error_message`

当前策略是：

- 每次为这个 part 再申请 presign URL，就视为一次重试
- 超过 `UPLOAD_MAX_PART_RETRIES` 后拒绝继续 presign

这套策略虽然还不算最终版，但已经能拦住明显异常的无限重试。

## 6. 任务过期清理怎么做

每个 `upload_task` 现在会在创建时写入：

- `expires_at`

并新增：

```http
POST /uploads/cleanup-expired
```

它会做两件事：

1. 找出已过期但还没结束的上传任务
2. 尝试对 OSS 调用 `abort multipart upload`
3. 把本地任务标记为 `expired`

这一步的意义很直接：

- 避免 unfinished multipart upload 长期堆在 OSS
- 避免本地数据库里一直挂着失效任务

## 7. 更强的 hash 校验现在怎么做

Phase 2 只是把 `file_sha256` 记在上传任务里。

Phase 3 在上传完成后的后处理阶段，已经开始真正校验：

```text
storage.get_object(...)
  -> 读回对象
  -> 计算 SHA256
  -> 对比 upload_task.file_sha256
```

如果不一致：

- processing job 标记失败
- upload_task.processing_status = failed
- document 也会失败

这里要注意：

当前实现是“下载整个对象到内存后计算 hash”。

这不是最终企业级做法，但已经比“只存 hash 不校验”强很多。

后面应该升级成：

- 流式读取
- 边读边 hash
- 避免一次性吃掉大文件内存

## 8. 上传完成后自动创建 document

现在 `POST /uploads/{upload_id}/complete` 成功后，不再只是返回“上传完成”。

它会继续：

1. 从 OSS 下载对象
2. 回落到本地 `data/uploads`
3. 提取文本
4. 创建或更新一条 `documents`

这意味着：

- 上传链路已经真正和原来的文档体系连起来了
- 后面前端文档列表能直接看到这批上传生成的 document

## 9. 自动触发 parse / split / embed / index

如果 `auto_index_on_complete = true`，上传完成后会继续：

1. 文本抽取
2. `regenerate_document_chunks`
3. `add_chunks`
4. 回填 `vector_id`
5. `document.status = indexed`

也就是说：

上传完成之后，这个文档已经不只是“在 OSS 里有个原件”，而是已经进到当前知识库的索引链路里了。

## 10. 为什么要新增 `upload_processing_jobs`

因为“上传成功”和“后处理成功”不是同一回事。

可能出现这种情况：

- multipart complete 成功
- 但文本提取失败
- 或 embedding 失败

如果没有独立 job，你只能把所有失败都塞到 upload task 里，最后状态很混。

所以现在单独有：

- `upload_processing_jobs.status`
- `current_step`
- `error_message`

它的意义是：

- transport 完成是一个维度
- processing 成功是另一个维度

## 11. 当前 complete 响应变成了什么

现在 `POST /uploads/{upload_id}/complete` 返回里除了 upload 完成状态，还会带：

- `document_id`
- `processing_job_id`
- `processing_status`
- `processing_error_message`

这样前端就能直接知道：

- 上传是否完成
- document 是否创建
- 后处理是否成功

## 12. 当前边界

虽然 Phase 3 已经把链路打通了，但还没做到最终形态。

当前仍然是：

- 上传 complete 后同步执行后处理
- hash 校验还是整对象读入
- 没有真正 worker / MQ

所以它现在的定位更准确地说是：

> 代码结构上已经完成了解耦，执行模型上还没完全异步化。

## 13. 下一步真正该做什么

后面更值得做的是：

- 把 `upload_processing_jobs` 切到异步 worker
- 上传 complete 只负责写 job，不同步跑 parse/index
- 对 hash 改成流式校验
- 对不同阶段拆并发池
- 给 processing job 增加重试退避和告警

## 14. 当前结论

Phase 3 完成后，这条链路已经从：

```text
上传任务系统
```

变成了：

```text
上传任务系统
  -> 文档创建
  -> 文本抽取
  -> 切片
  -> 向量索引
```

这已经是“真实业务链路开始打通”的阶段了。
