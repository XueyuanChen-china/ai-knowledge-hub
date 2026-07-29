from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.auth import router as auth_router
from app.api.account import router as account_router
from app.api.admin_users import router as admin_users_router
from app.api.document import router as document_router
from app.api.knowledge_base import router as knowledge_base_router
from app.api.knowledge_item import router as knowledge_item_router
from app.api.health import router as health_router
from app.api.search import router as search_router
from app.api.upload import router as upload_router
from app.config import get_cors_allow_origins, get_settings
from app.db.database import check_database_ready
from app.graph.checkpointer import close_graph_checkpointer, initialize_graph_checkpointer
from app.middleware.request_context import RequestContextMiddleware
from app.observability.logging import configure_logging
from app.services.upload_worker import get_upload_processing_worker_manager

# 读取项目配置，例如应用名称、数据库地址等。
settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """管理独立 checkpoint 连接池和现有后台 worker 的生命周期。"""

    check_database_ready()
    initialize_graph_checkpointer(settings)
    if settings.upload_worker_enabled:
        get_upload_processing_worker_manager().start(settings)
    try:
        yield
    finally:
        get_upload_processing_worker_manager().stop()
        close_graph_checkpointer()

# 创建 FastAPI 应用实例。
# 后续所有接口路由都会挂载到这个 app 上。
app = FastAPI(title=settings.app_name, lifespan=lifespan)

# 必须先建立 request/trace 上下文，后续路由、业务日志和 Celery 投递才能复用。
app.add_middleware(RequestContextMiddleware)

# 允许前端本地开发环境跨域调用后端接口。
# 例如：localhost:3000 -> 127.0.0.1:8000
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册知识库 CRUD 路由。
# 注册后，Swagger 里会出现 /knowledge-bases 相关接口。
app.include_router(knowledge_base_router)

# 注册登录和当前用户接口。管理员必须由显式 seed 脚本创建。
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(admin_users_router)

# 注册知识条目 CRUD 路由。
# 注册后，Swagger 里会出现 /knowledge-items 相关接口。
app.include_router(knowledge_item_router)

# 注册文档上传路由。
# 注册后，Swagger 里会出现 /documents 文件上传接口。
app.include_router(document_router)

# 注册搜索路由。
# 注册后，Swagger 里会出现 /search/semantic 等检索接口。
app.include_router(search_router)

# 注册大文件上传骨架路由。
# 注册后，Swagger 里会出现 /uploads/init、/uploads/{upload_id} 等接口。
app.include_router(upload_router)

# 注册基于 LangGraph 的对话路由。
# 注册后，Swagger 里会出现 /api/chat 和 /api/review/resume。
app.include_router(chat_router)
app.include_router(health_router)


@app.get("/health", include_in_schema=False)
def health_check() -> dict[str, str]:
    """兼容旧的健康检查地址，语义与 /health/live 相同。"""

    return {"status": "ok"}
