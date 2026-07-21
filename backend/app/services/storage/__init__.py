"""对象存储抽象层。

Phase 1 先把业务层和具体 OSS SDK 解耦，后面如果要接 MinIO / S3，
只需要补新的 adapter，而不用重写 upload service。
"""
