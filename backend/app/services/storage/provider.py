from app.config import get_settings, validate_oss_settings
from app.services.storage.aliyun_oss import AliyunOSSStorageAdapter
from app.services.storage.base import ObjectStorageAdapter


def get_object_storage_adapter() -> ObjectStorageAdapter:
    """返回当前启用的对象存储适配器。"""

    settings = get_settings()
    provider = settings.storage_provider.strip().lower()
    if provider != "aliyun-oss":
        raise RuntimeError(f"Unsupported storage provider: {settings.storage_provider}")

    validate_oss_settings(settings)
    return AliyunOSSStorageAdapter(settings)
