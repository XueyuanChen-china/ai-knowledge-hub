from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.auth import router as auth_router
from app.api.document import router as document_router
from app.api.knowledge_base import router as knowledge_base_router
from app.api.knowledge_item import router as knowledge_item_router
from app.api.search import router as search_router
from app.api.upload import router as upload_router
from app.config import get_cors_allow_origins, get_settings
from app.db.database import check_database_ready
from app.services.upload_worker import get_upload_processing_worker_manager

# 读取项目配置，例如应用名称、数据库地址等。
settings = get_settings()

# 创建 FastAPI 应用实例。
# 后续所有接口路由都会挂载到这个 app 上。
app = FastAPI(title=settings.app_name)

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


@app.on_event("startup")
def on_startup() -> None:
    """应用启动时执行的初始化逻辑。

    当前只检查 PostgreSQL 连接和 Alembic revision。
    数据库结构必须由发布流程显式执行 Alembic，应用启动不会自动改表。
    """

    check_database_ready()
    if settings.upload_worker_enabled:
        get_upload_processing_worker_manager().start(settings)


@app.on_event("shutdown")
def on_shutdown() -> None:
    """应用停止时关闭后台 worker。"""

    get_upload_processing_worker_manager().stop()


@app.get("/health")
def health_check() -> dict[str, str]:
    """健康检查接口。

    用于确认后端服务是否正常启动。
    验收方式：访问 GET /health，看到 {"status": "ok"} 就说明服务可用。
    """

    return {"status": "ok"}
