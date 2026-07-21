# Large File Upload

这个目录专门放“大文件上传”相关设计和分阶段学习文档。

建议使用方式：

- 先读路线图：
  - [enterprise-upload-roadmap.md](./enterprise-upload-roadmap.md)
- 再读当前已落地的 Phase 1 设计说明：
  - [phase-01-object-storage-and-upload-contract.md](./phase-01-object-storage-and-upload-contract.md)
- 再读当前已落地的 Phase 2 设计说明：
  - [phase-02-multipart-upload-and-resume.md](./phase-02-multipart-upload-and-resume.md)
- 再读当前已落地的 Phase 3 设计说明：
  - [phase-03-post-upload-processing-and-control.md](./phase-03-post-upload-processing-and-control.md)
- 再读当前已落地的 Phase 4 设计说明：
  - [phase-04-security-and-governance.md](./phase-04-security-and-governance.md)
- 再读当前已落地的 RabbitMQ + Celery 基础接入说明：
  - [phase-05-rabbitmq-celery-foundation.md](./phase-05-rabbitmq-celery-foundation.md)

当前这份路线图的前提是：

- 目标按企业方案设计
- 默认使用对象存储
- 上传和解析、切片、embedding、索引彻底解耦
- 后续支持分片上传、断点续传、异步处理、资源隔离
