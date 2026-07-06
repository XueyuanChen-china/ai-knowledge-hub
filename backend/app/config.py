from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目配置类。

    BaseSettings 会自动从环境变量和 .env 文件中读取配置。
    后续如果要加大模型 API Key、Elasticsearch 参数、Embedding 模型配置，也建议统一放在这里。
    """

    # 应用名称：会显示在 FastAPI 自动生成的接口文档标题里。
    app_name: str = "AI Knowledge Hub"

    # 当前运行环境：development / testing / production。
    app_env: str = "development"

    # SQLite 数据库连接地址。
    # sqlite:///./data/sqlite/ai_knowledge_hub.db 表示数据库文件在 backend/data/sqlite/ 目录下。
    database_url: str = "sqlite:///./data/sqlite/ai_knowledge_hub.db"

    # Elasticsearch 连接地址。默认先按本地开发的单节点服务处理。
    elasticsearch_url: str = "http://localhost:9200"

    # Elasticsearch 用户名密码。开发环境先允许为空。
    elasticsearch_username: str = ""
    elasticsearch_password: str = ""

    # 是否校验证书。HTTP 本地开发先默认关闭。
    elasticsearch_verify_certs: bool = False

    # 向量索引名前缀。后面会拼上 knowledge_base_id。
    elasticsearch_index_prefix: str = "knowledge_chunks_"

    # content 字段使用的 analyzer。第一版显式使用 ES 内置 cjk analyzer，
    # 这样中文关键词检索时比默认 standard 更合适。
    elasticsearch_content_analyzer: str = "cjk"

    # 搜索阶段的 analyzer，默认先和写入 analyzer 保持一致。
    elasticsearch_content_search_analyzer: str = "cjk"

    # 写入后是否等 refresh 完成再返回。
    # 允许值：false / true / wait_for。当前默认 wait_for，方便开发时“刚写完就能搜到”。
    elasticsearch_write_refresh: str = "wait_for"

    # Embedding 模型名称。这里直接改成 BGE-M3。
    embedding_model_name: str = "BAAI/bge-m3"

    # 先默认走 CPU，后续如果本机有 GPU 再改配置即可。
    embedding_device: str = "cpu"

    # 是否对 embedding 向量做归一化。
    embedding_normalize: bool = True

    # BGE-M3 dense embedding 维度。官方模型为 1024。
    embedding_dimensions: int = 1024

    # 一次 embedding 的批大小。
    embedding_batch_size: int = 16

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
