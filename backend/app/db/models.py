from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class KnowledgeBase(SQLModel, table=True):
    """知识库表。

    一个 KnowledgeBase 表示一个独立知识库，例如：
    - 公司制度知识库
    - 论文阅读知识库
    - 客服 FAQ 知识库

    table=True 表示这个类不仅是 Python 数据模型，也会映射成数据库表。
    """

    # 指定数据库中的真实表名。
    __tablename__ = "knowledge_bases"

    # 主键 ID。
    # Optional[int] + default=None 表示创建对象时可以不传 id，由数据库自动生成。
    id: Optional[int] = Field(default=None, primary_key=True)

    # 知识库名称。
    # index=True 表示给这个字段建索引，后续按名称搜索会更快。
    name: str = Field(index=True, max_length=100)

    # 知识库描述，先用普通字符串存储。
    description: str = ""

    # 创建时间。
    # default_factory 会在每次新建记录时自动调用 datetime.utcnow。
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # 更新时间。
    # Day 1 先自动填初始值；后续写 update 接口时，需要手动刷新这个字段。
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Document(SQLModel, table=True):
    """文档表。

    一条 Document 表示用户上传到某个知识库里的一个源文件。
    文件本体放在 backend/data/uploads，数据库里只保存路径和处理状态。
    """

    __tablename__ = "documents"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 所属知识库。
    # foreign_key 会在数据库层面表达 documents.knowledge_base_id 指向 knowledge_bases.id。
    knowledge_base_id: int = Field(foreign_key="knowledge_bases.id", index=True)

    # 原始文件名，例如 company-policy.pdf。
    filename: str = Field(max_length=255)

    # 文件保存到本地后的路径，例如 data/uploads/company-policy.pdf。
    file_path: str = Field(max_length=500)

    # 文件类型，例如 txt / md / pdf。
    file_type: str = Field(max_length=50)

    # 文档处理状态：uploaded / indexed / failed。
    status: str = Field(default="uploaded", index=True, max_length=50)

    # 从文件中提取出的纯文本。
    # Day 6 先保存到 documents 表，后续切分 chunk 时可以直接读取这个字段。
    extracted_text: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeItem(SQLModel, table=True):
    """知识条目表。

    一条 KnowledgeItem 表示可以被管理的一段知识。
    它既可以来自手动录入，也可以来自上传文档后的抽取结果。
    """

    __tablename__ = "knowledge_items"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 所属知识库。
    knowledge_base_id: int = Field(foreign_key="knowledge_bases.id", index=True)

    # 知识标题，用于列表展示和关键词搜索。
    title: str = Field(index=True, max_length=200)

    # 知识正文。PostgreSQL 中会以 TEXT 类型保存。
    content: str

    # 标签先用字符串保存，例如 '["制度", "报销"]'。
    # 后续也可以拆成独立 tags 表，但第一版没必要复杂化。
    tags: str = ""

    # 状态：draft / active / disabled。
    # 只有 active 的知识条目才应该参与后续 RAG 检索。
    status: str = Field(default="draft", index=True, max_length=50)

    # 来源类型：manual / document。
    source_type: str = Field(default="manual", index=True, max_length=50)

    # 如果这条知识来自某个上传文档，这里记录对应 document id。
    source_document_id: Optional[int] = Field(default=None, foreign_key="documents.id")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeItemReview(SQLModel, table=True):
    """知识条目审核记录表。

    一条 KnowledgeItem 可以有多次审核记录。
    审核人可以是 human，也可以是 agent；这样后续从人工审核升级到 Agent 审核时，
    不需要推翻 knowledge_items 的主表结构。
    """

    __tablename__ = "knowledge_item_reviews"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 被审核的知识条目。
    knowledge_item_id: int = Field(foreign_key="knowledge_items.id", index=True)

    # 审核来源：human / agent。
    reviewer_type: str = Field(default="human", index=True, max_length=50)

    # 审核状态：pending / approved / rejected / need_human。
    status: str = Field(default="pending", index=True, max_length=50)

    # 置信度分数。
    # 人工审核可以不关心这个字段；Agent 审核时用它判断是否需要转人工。
    confidence_score: float = Field(default=0.0)

    # 审核理由。
    # 例如 Agent 判断“内容重复”“解析乱码”“政策风险较高”等。
    review_reason: str = ""

    # 审核备注。
    # 人工审核时可以写具体修改意见，Agent 审核时可以保存模型输出摘要。
    reviewer_note: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)


class Chunk(SQLModel, table=True):
    """文本切片表。

    Chunk 是实际参与向量检索的最小文本单元。
    后续做 embedding 时，每个 chunk 会对应向量库里的一个 vector_id。
    """

    __tablename__ = "chunks"

    id: Optional[int] = Field(default=None, primary_key=True)

    knowledge_base_id: int = Field(foreign_key="knowledge_bases.id", index=True)

    # 如果 chunk 来自上传文档，这里冗余记录原始 document id，方便按文件追溯。
    # 手动录入的知识没有原始文档，所以这个字段允许为空。
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)

    # 按当前设计，所有 chunk 都必须先归属于某条 KnowledgeItem。
    # 无论知识来自上传文档还是手动录入，都会先形成 KnowledgeItem，再切 chunk。
    knowledge_item_id: int = Field(foreign_key="knowledge_items.id", index=True)

    # 同一个文档或知识条目下的第几个 chunk。
    chunk_index: int = Field(default=0)

    # chunk 的正文内容。
    content: str

    # 向量索引里的稳定 ID。
    # 当前会同步写入 PostgreSQL 和 Elasticsearch，用它把两边的数据关联起来。
    vector_id: Optional[str] = Field(default=None, index=True, max_length=255)

    # 元数据 JSON 字符串，例如页码、来源、标签等。
    metadata_json: str = ""

    # 阶段级流水线中暂存 embedding 结果。
    # embed 阶段写入，index 阶段消费后清空，避免重复调用模型。
    embedding_json: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)


class Conversation(SQLModel, table=True):
    """会话表。

    一条 Conversation 表示用户围绕某个知识库发起的一次问答会话。
    thread_id 后续会用于 LangGraph checkpoint 恢复工作流状态。
    """

    __tablename__ = "conversations"

    id: Optional[int] = Field(default=None, primary_key=True)

    knowledge_base_id: int = Field(foreign_key="knowledge_bases.id", index=True)

    # 会话标题，可以后续由用户问题或 AI 自动生成。
    title: str = Field(default="New Conversation", max_length=200)

    # LangGraph 里的 thread_id，用来关联 checkpoint。
    thread_id: str = Field(index=True, max_length=255)

    # 是否在会话列表置顶。
    is_pinned: bool = Field(default=False, index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Message(SQLModel, table=True):
    """消息表。

    保存一次会话里的用户消息和助手回复。
    """

    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)

    conversation_id: int = Field(foreign_key="conversations.id", index=True)

    # role 通常是 user / assistant / system。
    role: str = Field(index=True, max_length=50)

    # 消息正文。
    content: str

    # 附加信息 JSON 字符串，例如引用来源、token 数、模型名称等。
    metadata_json: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewTask(SQLModel, table=True):
    """人工审核任务表。

    后续 LangGraph 检索结果置信度较低时，可以创建 ReviewTask，
    让人确认是否继续生成答案。
    """

    __tablename__ = "review_tasks"

    id: Optional[int] = Field(default=None, primary_key=True)

    conversation_id: int = Field(foreign_key="conversations.id", index=True)

    # 用户原始问题。
    question: str

    # 检索到的文档预览，用 JSON 字符串保存。
    docs_preview: str = ""

    # 审核状态：pending / approved / rejected。
    status: str = Field(default="pending", index=True, max_length=50)

    # 人工审核备注。
    human_note: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UploadTask(SQLModel, table=True):
    """上传任务表。

    Phase 1 先把“上传任务”作为独立主表落下来。
    它负责承接前端初始化上传、对象存储 upload_id、文件元数据和后续完成上传的状态。
    """

    __tablename__ = "upload_tasks"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 对外暴露的稳定上传任务 ID。
    upload_id: str = Field(index=True, unique=True, max_length=64)

    # 上传文件归属哪个知识库。
    knowledge_base_id: int = Field(foreign_key="knowledge_bases.id", index=True)

    # 用户原始文件名只保留作展示，不直接参与 object_key 生成。
    original_filename: str = Field(max_length=255)

    # 当前对象存储提供方。Phase 1 固定是 aliyun-oss。
    storage_provider: str = Field(max_length=50)

    # 目标 bucket。
    bucket_name: str = Field(max_length=255)

    # 后端统一生成的对象路径，例如 raw/dev/7/upl_xxx/source.pdf。
    object_key: str = Field(max_length=500)

    # 文件后缀类型，例如 pdf / docx / xlsx。
    file_type: str = Field(max_length=50)

    # 客户端声明的 MIME 类型。
    client_mime_type: str = Field(default="", max_length=255)

    # 服务端后续可补充探测结果。Phase 1 先允许为空。
    detected_mime_type: str = Field(default="", max_length=255)

    # 文件总大小，单位字节。
    file_size: int = Field(default=0)

    # 上传协议里约定的单片大小，单位字节。
    part_size: int = Field(default=0)

    # 按 file_size / part_size 推导出的总片数。
    total_parts: int = Field(default=0)

    # 整文件 SHA256，前端可选上传；Phase 1 先只存储，不强制。
    file_sha256: str = Field(default="", max_length=128)

    # 对象存储返回的 multipart upload_id。
    storage_upload_id: str = Field(default="", max_length=255)

    # 上传任务状态。
    status: str = Field(default="initiated", index=True, max_length=50)

    # 已完成片数。Phase 1 还不会真实推进，但字段先固化。
    completed_parts: int = Field(default=0)

    # 上传任务过期时间。
    expires_at: datetime = Field(default_factory=datetime.utcnow)

    # 上传完成后是否自动创建 documents。
    auto_create_document: bool = Field(default=True)

    # 上传完成后是否自动触发 parse / split / embed / index。
    auto_index_on_complete: bool = Field(default=True)

    # 上传完成后关联生成的 document。
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)

    # 上传完成后的处理状态。
    processing_status: str = Field(default="", index=True, max_length=50)

    # 上传完成后的处理错误。
    processing_error_message: str = ""

    # 错误信息。失败时用于排查。
    error_message: str = ""

    # 预留上传人标识，当前先不强依赖用户系统。
    created_by: str = Field(default="", max_length=100)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UploadPart(SQLModel, table=True):
    """上传分片表。

    Phase 1 不实现真实分片上传，但先把 part 级别的存储结构准备好，
    这样后面接预签名 URL、断点续传时不需要推翻表设计。
    """

    __tablename__ = "upload_parts"
    __table_args__ = (
        UniqueConstraint(
            "upload_task_id",
            "part_number",
            name="uq_upload_parts_task_part",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    # 所属上传任务。
    upload_task_id: int = Field(foreign_key="upload_tasks.id", index=True)

    # 第几片，从 1 开始。
    part_number: int = Field(index=True)

    # 对象存储返回的 etag，后续 complete multipart upload 时会用到。
    etag: str = Field(default="", max_length=255)

    # 这一片的字节大小。
    part_size: int = Field(default=0)

    # 这一片的可选 hash。
    part_sha256: str = Field(default="", max_length=128)

    # 当前分片状态：pending / uploaded / verified / failed。
    status: str = Field(default="pending", index=True, max_length=50)

    # 当前分片的重试次数。
    retry_count: int = Field(default=0)

    # 最近一次错误信息。
    last_error_message: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UploadProcessingJob(SQLModel, table=True):
    """上传完成后的文档处理任务。

    当前先把上传后处理抽成独立 job 记录。
    即使今天还是同步执行，后面也能平滑切到异步 worker。
    """

    __tablename__ = "upload_processing_jobs"

    id: Optional[int] = Field(default=None, primary_key=True)

    upload_task_id: int = Field(foreign_key="upload_tasks.id", index=True)
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)

    # job_type 是粗粒度业务类型；stage 是流水线里的具体阶段。
    # Phase A 先创建 stage=download 的第一阶段 job，后续再接 parse/split/embed/index。
    job_type: str = Field(default="upload_pipeline", index=True, max_length=50)
    stage: str = Field(default="download", index=True, max_length=50)
    depends_on_job_id: Optional[int] = Field(
        default=None,
        foreign_key="upload_processing_jobs.id",
        index=True,
    )

    status: str = Field(default="pending", index=True, max_length=50)
    current_step: str = Field(default="", max_length=100)

    # retry_count 继续保留给旧逻辑；attempt_count/max_attempts 用于阶段级 job 语义。
    retry_count: int = Field(default=0)
    max_retry_count: int = Field(default=3)
    attempt_count: int = Field(default=0)
    max_attempts: int = Field(default=3)
    next_run_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    celery_task_id: str = Field(default="", index=True, max_length=255)

    # claim_token / locked_by / lease_expires_at 用来表达“这个 job 已被某个 worker 抢占”。
    # 后续如果 worker 崩溃，租约过期后其他 worker 可以重新抢占。
    claim_token: str = Field(default="", index=True, max_length=64)
    locked_by: str = Field(default="", index=True, max_length=100)
    claimed_at: Optional[datetime] = Field(default=None)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)

    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    last_alert_at: Optional[datetime] = Field(default=None)
    alert_status: str = Field(default="", max_length=50)
    error_message: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UploadAuditLog(SQLModel, table=True):
    """上传治理审计日志。

    先记录关键控制面事件，后面接告警平台或审计系统时可复用这张表。
    """

    __tablename__ = "upload_audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)

    upload_task_id: Optional[int] = Field(default=None, foreign_key="upload_tasks.id", index=True)
    processing_job_id: Optional[int] = Field(default=None, foreign_key="upload_processing_jobs.id", index=True)

    actor: str = Field(default="", index=True, max_length=100)
    event_type: str = Field(index=True, max_length=100)
    level: str = Field(default="info", index=True, max_length=20)
    detail_json: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)
