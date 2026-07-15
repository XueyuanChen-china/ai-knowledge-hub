# Large File Upload

这个目录专门放“大文件上传”相关设计和分阶段学习文档。

建议使用方式：

- 先读路线图：
  - [enterprise-upload-roadmap.md](./enterprise-upload-roadmap.md)
- 再按阶段往这个目录继续补学习文档：
  - `phase-01-object-storage-and-upload-contract.md`
  - `phase-02-multipart-upload-and-resume.md`
  - `phase-03-async-processing-and-resource-control.md`
  - `phase-04-security-and-governance.md`

当前这份路线图的前提是：

- 目标按企业方案设计
- 默认使用对象存储
- 上传和解析、切片、embedding、索引彻底解耦
- 后续支持分片上传、断点续传、异步处理、资源隔离
