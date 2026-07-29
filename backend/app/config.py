from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    """项目配置类。

    BaseSettings 会自动从环境变量和 .env 文件中读取配置。
    后续如果要加大模型 API Key、Elasticsearch 参数、Embedding 模型配置，也建议统一放在这里。
    """

    # 应用名称：会显示在 FastAPI 自动生成的接口文档标题里。
    app_name: str = "AI Knowledge Hub"

    # 当前运行环境：development / testing / production。
    app_env: str = "development"

    # 标准 logging 的最低输出级别。U7 会统一转成 JSON 日志。
    log_level: str = "INFO"

    # 允许跨域访问的前端来源，多个值用逗号分隔。
    # 例如：http://localhost:3000,http://127.0.0.1:3000
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # 数据库连接地址。
    # 当前项目统一使用 PostgreSQL，不再保留 SQLite 运行时支持。
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_knowledge_hub"

    # LangGraph checkpoint 使用独立连接池。为空时复用 database_url，但不复用
    # SQLModel 业务 Session，避免一次图执行长期占用请求事务。
    graph_checkpoint_database_url: str = ""
    graph_checkpoint_pool_min_size: int = 1
    graph_checkpoint_pool_max_size: int = 4
    graph_checkpoint_pool_timeout_seconds: float = 10.0

    # JWT 签名密钥只从环境变量读取；生产环境必须显式配置，不能使用代码默认值。
    auth_jwt_secret: str = Field(
        default="",
        validation_alias="AUTH_JWT_SECRET",
    )

    # JWT 的发行者和受众，防止一个服务签发的 token 被另一个服务误接受。
    auth_jwt_issuer: str = "ai-knowledge-hub"
    auth_jwt_audience: str = "ai-knowledge-hub-web"

    # access token 短时有效，长期登录状态后续再接 refresh token 体系。
    auth_access_token_ttl_seconds: int = 15 * 60

    # 登录接口的单进程失败限流。多副本分布式限流留到集群阶段。
    auth_login_rate_limit_max_attempts: int = 5
    auth_login_rate_limit_window_seconds: int = 60
    auth_login_failure_backoff_base_seconds: float = 0.5

    # JWT 撤销黑名单使用的 Redis 地址。Redis 不保存 token 正文，只保存 jti 和剩余 TTL。
    auth_redis_url: str = "redis://localhost:6379/0"
    auth_token_blacklist_prefix: str = "ai-knowledge-hub:auth:blacklist:"
    auth_redis_socket_timeout_seconds: float = 2.0

    # 默认组织由 identity migration 创建，管理员必须由显式 seed 脚本创建。
    auth_default_organization_slug: str = "default"
    auth_default_organization_name: str = "Default Organization"

    # Elasticsearch 连接地址。默认先按本地开发的单节点服务处理。
    elasticsearch_url: str = "http://localhost:9200"

    # Elasticsearch 用户名密码。开发环境先允许为空。
    elasticsearch_username: str = ""
    elasticsearch_password: str = ""

    # 是否校验证书。HTTP 本地开发先默认关闭。
    elasticsearch_verify_certs: bool = False

    # 向量索引名前缀。后面会拼上版本号和 knowledge_base_id。
    elasticsearch_index_prefix: str = "knowledge_chunks_"

    # 资源授权字段进入 ES 后使用新的具体索引。检索统一走 alias，
    # 这样重建存量向量时可以先写新索引，再原子切换，不会覆盖旧索引。
    elasticsearch_index_version: int = 2

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

    # LLM Router 的 OpenAI 兼容 Base URL。
    # 例如 DashScope / Model Studio 的 compatible-mode/v1 地址。
    llm_router_base_url: str = ""

    # LLM Router 使用的 API Key。
    llm_router_api_key: str = ""

    # LLM Router 使用的模型名。
    llm_router_model: str = ""

    # Router 请求超时时间，单位秒。
    llm_router_timeout_seconds: int = 20

    # Answer Node 的 OpenAI 兼容 Base URL。为空时回退到 Router 配置。
    llm_answer_base_url: str = ""

    # Answer Node 使用的 API Key。为空时回退到 Router 配置。
    llm_answer_api_key: str = ""

    # Answer Node 使用的模型名。为空时回退到 Router 配置。
    llm_answer_model: str = ""

    # Answer Node 请求超时时间，单位秒。
    llm_answer_timeout_seconds: int = 40

    # Relevance Check 的低分阈值。
    # 如果 top score 低于这个值，就先不直接编答案，而是标记 need_human_review。
    relevance_low_score_threshold: float = 0.78

    # 对象存储提供方。Phase 1 先固定支持阿里云 OSS。
    storage_provider: str = "aliyun-oss"

    # 阿里云 OSS 接入参数。
    oss_endpoint: str = "oss-cn-shanghai.aliyuncs.com"
    oss_region: str = "cn-shanghai"
    oss_bucket: str = "ai-knowledge-hub-xueyuan-dev"
    # 这两个敏感配置只从环境变量读取，不在代码里提供真实默认值。
    oss_access_key_id: str = Field(
        default="",
        validation_alias="OSS_ACCESS_KEY_ID",
    )
    oss_access_key_secret: str = Field(
        default="",
        validation_alias="OSS_ACCESS_KEY_SECRET",
    )
    oss_storage_prefix: str = "raw/dev"
    oss_presign_expire_seconds: int = 900

    # 上传任务默认分片大小。Phase 1 还不做真实分片上传，但要把协议字段先固化下来。
    upload_default_part_size: int = 5 * 1024 * 1024

    # 单文件上传大小上限，默认 10GB。
    upload_max_file_size: int = 10 * 1024 * 1024 * 1024

    # 上传任务默认有效期，过期后需要清理未完成任务。
    upload_task_expire_hours: int = 24

    # 单个 part 最多允许重试多少次。
    upload_max_part_retries: int = 5

    # 批量 presign 一次最多发多少个 part。
    upload_presign_batch_max_parts: int = 20

    # 返回给前端的建议并发上传度。
    upload_recommended_parallelism: int = 3

    # 上传完成后是否默认自动创建 documents。
    upload_auto_create_document: bool = True

    # 上传完成后是否默认自动触发 parse / split / embed / index。
    upload_auto_index_on_complete: bool = True

    # 是否启用应用内上传后处理 worker。
    upload_worker_enabled: bool = True

    # 上传后处理执行后端：in_app / celery。
    # Phase C 开始支持 celery 单阶段 download 消费。
    upload_processing_backend: str = "celery"

    # 后处理 job 轮询间隔。
    upload_job_poll_interval_seconds: int = 2

    # 后处理 worker 最大并发任务数。
    upload_job_max_workers: int = 4

    # 后处理 job 被某个 worker 抢占后的租约时长。
    # 如果进程崩溃，超过这个时间后其他 worker 可以重新 claim。
    upload_job_lease_seconds: int = 3600

    # 下载/解析阶段的并发上限。
    upload_download_stage_concurrency: int = 2

    # 索引阶段的并发上限。
    upload_index_stage_concurrency: int = 1

    # 后处理 job 最大重试次数。
    upload_job_max_retries: int = 3

    # 后处理重试退避起始秒数。
    upload_job_retry_backoff_seconds: int = 5

    # 后处理重试退避最大秒数。
    upload_job_retry_backoff_max_seconds: int = 300

    # 每个上传发起人允许的活跃上传任务上限。
    upload_max_active_tasks_per_actor: int = 20

    # 每个上传发起人每天允许申请的总字节配额。
    upload_daily_quota_bytes: int = 20 * 1024 * 1024 * 1024

    # 本地回落文件的保留时长。
    upload_local_retention_hours: int = 24

    # magic number 识别时读取的头部字节数。
    upload_magic_sniff_bytes: int = 8192

    # Office zip 文档允许的最大成员数。
    upload_zip_max_members: int = 5000

    # Office zip 文档允许的总解压大小。
    upload_zip_max_uncompressed_bytes: int = 512 * 1024 * 1024

    # Office zip 文档允许的最大压缩比。
    upload_zip_max_compression_ratio: float = 200.0

    # Celery / RabbitMQ 基础接入。Phase B 只跑 hello task，不接完整上传流程。
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = ""
    celery_task_default_queue: str = "ai_knowledge_hub"

    # RabbitMQ publisher confirm。Producer 发布后等待 Broker 返回确认，
    # 未确认时 apply_async 会抛出异常，调用方可以保留 job 为待投递状态并重试。
    celery_publisher_confirm: bool = True

    # 终态失败消息使用的死信交换机和队列。
    celery_dead_letter_exchange: str = "ai_knowledge_hub.dlx"
    celery_dead_letter_queue: str = "ai_knowledge_hub.dead"
    celery_dead_letter_routing_key: str = "dead"

    # 告诉 pydantic-settings 从 backend/.env 文件读取配置。
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        populate_by_name=True,
        # .env 里如果临时多写了其他字段，不让程序直接报错。
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """获取全局配置对象。

    lru_cache 会缓存 Settings 实例，避免每次调用都重新读取 .env。
    """

    return Settings()


def get_cors_allow_origins() -> list[str]:
    """把逗号分隔的 CORS origins 配置解析成列表。"""

    settings = get_settings()
    return [
        origin.strip()
        for origin in settings.cors_allow_origins.split(",")
        if origin.strip()
    ]


def validate_oss_settings(settings: Settings) -> None:
    """校验阿里云 OSS Phase 1 所需的最小配置。"""

    required_values = {
        "OSS_ENDPOINT": settings.oss_endpoint,
        "OSS_REGION": settings.oss_region,
        "OSS_BUCKET": settings.oss_bucket,
        "OSS_STORAGE_PREFIX": settings.oss_storage_prefix,
        "OSS_ACCESS_KEY_ID": settings.oss_access_key_id,
        "OSS_ACCESS_KEY_SECRET": settings.oss_access_key_secret,
    }
    missing_keys = [
        key
        for key, value in required_values.items()
        if not str(value).strip()
    ]
    if missing_keys:
        missing_text = ", ".join(missing_keys)
        raise RuntimeError(f"Missing required OSS settings: {missing_text}")
