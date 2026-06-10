from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目配置类。

    BaseSettings 会自动从环境变量和 .env 文件中读取配置。
    后续如果要加通义千问 API Key、Chroma 路径、模型名称，也建议统一放在这里。
    """

    # 应用名称：会显示在 FastAPI 自动生成的接口文档标题里。
    app_name: str = "AI Knowledge Hub"

    # 当前运行环境：development / testing / production。
    app_env: str = "development"

    # SQLite 数据库连接地址。
    # sqlite:///./data/sqlite/ai_knowledge_hub.db 表示数据库文件在 backend/data/sqlite/ 目录下。
    database_url: str = "sqlite:///./data/sqlite/ai_knowledge_hub.db"

    # 告诉 pydantic-settings 从 backend/.env 文件读取配置。
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # .env 里如果临时多写了其他字段，不让程序直接报错。
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """获取全局配置对象。

    lru_cache 会缓存 Settings 实例，避免每次调用都重新读取 .env。
    """

    return Settings()
