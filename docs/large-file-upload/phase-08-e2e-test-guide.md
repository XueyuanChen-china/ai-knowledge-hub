# 大文件上传完整测试指南

## 已准备的测试文件

当前仓库已经有五种真实样例：

```text
backend/tests/fixtures/splitter_regression/samples/plain_text_policy.txt
backend/tests/fixtures/splitter_regression/samples/markdown_handbook.md
backend/tests/fixtures/splitter_regression/binary_samples/pdf_policy.pdf
backend/tests/fixtures/splitter_regression/binary_samples/word_handbook.docx
backend/tests/fixtures/splitter_regression/binary_samples/workbook_orders.xlsx
backend/tests/fixtures/large_file_upload/sample_large_policy.txt
```

这些文件可以同时测试：

- TXT / Markdown 文本提取
- PDF layout 解析
- DOCX heading、列表和表格解析
- XLSX 多 sheet 和表格解析
- 超过 5 MiB 后的 multipart 多分片上传
- 切片、Embedding、Elasticsearch 入库

## 前置条件

确认 `backend/.env` 至少配置：

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ai_knowledge_hub
ELASTICSEARCH_URL=http://localhost:9200
OSS_ACCESS_KEY_ID=你的阿里云 AccessKey ID
OSS_ACCESS_KEY_SECRET=你的阿里云 AccessKey Secret
CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
UPLOAD_PROCESSING_BACKEND=celery
```

然后依次启动：

```bash
cd backend
bash scripts/start_rabbitmq_local.sh
bash scripts/start_celery_worker.sh
uvicorn app.main:app --reload
```

确认服务：

```bash
curl http://127.0.0.1:8000/health
curl http://localhost:9200
```

## 推荐的自动化测试

先创建一个知识库，记住返回的 `knowledge_base_id`。然后运行：

```bash
cd backend
python scripts/generate_upload_test_data.py

python scripts/test_large_upload_e2e.py \
  --knowledge-base-id 7 \
  tests/fixtures/splitter_regression/samples/plain_text_policy.txt
```

再分别测试其他格式：

```bash
python scripts/test_large_upload_e2e.py --knowledge-base-id 7 tests/fixtures/splitter_regression/samples/markdown_handbook.md
python scripts/test_large_upload_e2e.py --knowledge-base-id 7 tests/fixtures/splitter_regression/binary_samples/pdf_policy.pdf
python scripts/test_large_upload_e2e.py --knowledge-base-id 7 tests/fixtures/splitter_regression/binary_samples/word_handbook.docx
python scripts/test_large_upload_e2e.py --knowledge-base-id 7 tests/fixtures/splitter_regression/binary_samples/workbook_orders.xlsx
python scripts/test_large_upload_e2e.py --knowledge-base-id 7 tests/fixtures/large_file_upload/sample_large_policy.txt
```

脚本会自动执行：

```text
计算 SHA256
POST /uploads/init
申请每个 part 的 presigned URL
PUT 文件分片到 OSS
回写每个 part 的 ETag
POST /uploads/{upload_id}/complete
轮询上传处理状态
检查 documents.status=indexed
```

## 手工检查数据库和 Elasticsearch

上传完成后在 PostgreSQL 中检查：

```sql
SELECT id, upload_task_id, stage, status, depends_on_job_id,
       attempt_count, current_step, error_message
FROM upload_processing_jobs
ORDER BY id;
```

正常应该看到：

```text
download -> validate -> parse -> split -> embed -> index
```

并且每个 job 都是 `completed`。

检查文档：

```sql
SELECT id, filename, status
FROM documents
ORDER BY id DESC;
```

检查 chunks：

```sql
SELECT id, document_id, chunk_index, vector_id,
       length(embedding_json) AS embedding_cache_length
FROM chunks
ORDER BY document_id, chunk_index;
```

最终 `vector_id` 应该不为空，`embedding_cache_length` 应该为 0，因为 index 阶段已经消费并清理了 embedding 缓存。

## 失败重试测试

可以先停止 Elasticsearch：

```bash
docker stop <elasticsearch-container-name>
```

再上传一个文件，观察：

```text
index job: running -> retry_scheduled -> running
```

恢复 Elasticsearch 后，任务应该继续尝试；超过最大次数后变为 `failed`。

测试完恢复：

```bash
docker start <elasticsearch-container-name>
```

## 本次验收标准

- OSS 中存在完整原始对象
- `upload_tasks.status = completed`
- 6 个阶段 job 都存在
- 每个后续 job 的 `depends_on_job_id` 指向前一阶段
- `documents.status = indexed`
- `chunks.vector_id` 已回填
- Elasticsearch 对应索引存在文档
- 关闭 Elasticsearch 时，失败阶段进入重试状态
