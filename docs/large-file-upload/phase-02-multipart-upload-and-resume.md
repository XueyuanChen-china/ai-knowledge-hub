# Phase 02 Multipart Upload And Resume

这份文档对应当前仓库的大文件上传 Phase 2。

这一阶段的目标很明确：

> 把 Phase 1 的上传任务骨架，推进到“前端可以直传 OSS，后端可以做 part 级确认和断点续传查询”的状态。

## 1. Phase 2 做了什么

当前已经补上这些接口：

```http
POST /uploads/{upload_id}/parts/presign
POST /uploads/{upload_id}/parts/complete
POST /uploads/{upload_id}/abort
GET  /uploads/{upload_id}/parts
POST /uploads/{upload_id}/complete
```

这里最后一个 `POST /uploads/{upload_id}/complete` 也从 Phase 1 的“占位接口”，升级成了真正执行 multipart complete 的接口。

## 2. 当前链路

当前推荐的前端调用顺序：

```text
1. POST /uploads/init
   -> 拿到 upload_id / object_key / part_size / total_parts

2. 对每个 part:
   POST /uploads/{upload_id}/parts/presign
   -> 拿到 presigned_url

3. 前端直接 PUT 到 OSS

4. 每个 part 上传成功后：
   POST /uploads/{upload_id}/parts/complete
   -> 后端校验 OSS 上确实存在该 part
   -> 写 upload_parts

5. 如果中断：
   GET /uploads/{upload_id}/parts
   -> 查看 local_parts / remote_parts / missing_part_numbers

6. 全部完成后：
   POST /uploads/{upload_id}/complete
   -> 后端再次校验 OSS part 列表
   -> 调用 complete multipart upload
```

## 3. 为什么要有 `parts/complete`

很多人第一次做 multipart upload 会觉得：

> 既然前端已经把文件传到 OSS 了，为什么还要再调一次后端？

原因是：

- 前端知道“浏览器这次请求成功了”
- 但后端还不知道“这个 part 是否真的作为业务状态被确认”

如果没有 `parts/complete` 这一步，后端就只有两种糟糕选择：

- 要么完全相信前端自己报的状态
- 要么等最后 complete 时一次性猜测

这两个都不够稳。

所以这里的做法是：

- 前端上传成功后，回调后端
- 后端调用 OSS `list parts`
- 确认对应 `part_number / etag / size` 真的存在
- 再把这片写进 `upload_parts`

这样本地状态才有依据。

## 4. 断点续传查询怎么做

当前 `GET /uploads/{upload_id}/parts` 会返回三类信息：

### 4.1 `local_parts`

后端数据库里已经登记过的 part。

也就是：

- 用户已经上传
- 后端已经确认
- 本地状态已经落库

### 4.2 `remote_parts`

OSS 远端真实存在的 part 列表。

也就是：

- 文件数据已经上传到 OSS
- 但不一定已经同步回本地

### 4.3 `missing_part_numbers`

后端根据 `total_parts` 减去当前可确认完成的 part，算出还缺哪些 part。

前端断点续传时最需要这个字段。

## 5. 为什么同时返回 `local_parts` 和 `remote_parts`

这两个字段看起来像重复，其实不是。

它们反映的是两套事实源：

- `local_parts`：数据库里记录的业务状态
- `remote_parts`：OSS 里真实存在的底层上传状态

这两者短时间内可能不一致。

例如：

1. 前端把 part 传到 OSS 成功
2. 浏览器在调 `parts/complete` 前刷新了

这时候：

- `remote_parts` 有
- `local_parts` 没有

如果只看本地表，你会误以为这一片没传过。
如果只看 OSS，你又缺少业务确认状态。

所以这两个都要保留。

## 6. 最终 complete 为什么还要再校验一次

`POST /uploads/{upload_id}/complete` 现在不是盲目调用 OSS complete。

它会先做三层校验：

### 第一层：本地 part 是否齐全

是否每个 part 都已经有 `upload_parts` 记录，并且状态是 `uploaded``。

### 第二层：OSS part 列表是否齐全

是否 OSS 侧的 `list parts` 和本地确认过的 part 数一致。

### 第三层：etag 是否一致

本地记录的 `etag` 是否和 OSS 返回的一致。

只有这三层都过了，才会真正执行：

```text
complete_multipart_upload
```

这样做的原因很简单：

- 避免本地状态错
- 避免前端伪造 part 完成
- 避免某个 part 被覆盖或不一致

## 7. 当前状态流转

当前主要状态：

```text
initiated
uploading
uploaded
completed
failed
cancelled
```

大致流转：

```text
init
  -> initiated

presign
  -> uploading

part complete 全部完成
  -> uploaded

multipart complete 成功
  -> completed

abort
  -> cancelled
```

## 8. 当前接口语义

### `POST /uploads/{upload_id}/parts/presign`

输入：

```json
{
  "part_number": 1
}
```

输出：

- `presigned_url`
- `expire_seconds`
- `status`

这里不返回 body form 字段，是因为当前先按单 URL 上传模式处理。

### `POST /uploads/{upload_id}/parts/complete`

输入：

```json
{
  "part_number": 1,
  "etag": "xxx",
  "part_size": 5242880,
  "part_sha256": ""
}
```

后端会拿这个输入去对照 OSS `list parts`。

### `GET /uploads/{upload_id}/parts`

输出：

- `local_parts`
- `remote_parts`
- `missing_part_numbers`

前端断点续传直接消费这个结构就够了。

### `POST /uploads/{upload_id}/abort`

作用：

- 调 OSS `abort multipart upload`
- 把任务状态改成 `cancelled`

### `POST /uploads/{upload_id}/complete`

作用：

- 先对齐本地和 OSS part 列表
- 再调 OSS complete
- 成功后把任务改成 `completed`

## 9. 当前实现边界

虽然 Phase 2 已经可用了，但还没做到真正企业最终态。

当前还没做：

- 前端批量 presign
- 并发上传控制
- part 重试次数控制
- 任务过期清理
- 文件 hash 强校验
- 上传完成后自动创建 `documents`
- 上传完成后自动触发 parse / index

这些都是下一阶段要补的。

## 10. 测试重点

当前已经补的测试重点：

- presign 是否能返回 URL
- presign 是否会推进任务状态
- `parts/complete` 是否会校验远端 part
- `GET /parts` 是否能给出 resume 所需信息
- `POST /complete` 是否会校验全部 parts 并调用 OSS complete
- `POST /abort` 是否会推进为 cancelled

## 11. 当前结论

Phase 2 完成后，上传链路已经从“只有任务骨架”升级成：

> 前端可按 part 直传 OSS，后端可校验 part、支持续传查询，并最终完成 multipart upload。

这已经是一个像样的企业上传控制面了。
