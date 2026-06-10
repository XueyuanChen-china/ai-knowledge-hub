from fastapi import FastAPI

from app.api.knowledge_base import router as knowledge_base_router
from app.config import get_settings
from app.db.database import create_db_and_tables

# 读取项目配置，例如应用名称、数据库地址等。
settings = get_settings()

# 创建 FastAPI 应用实例。
# 后续所有接口路由都会挂载到这个 app 上。
app = FastAPI(title=settings.app_name)

# 注册知识库 CRUD 路由。
# 注册后，Swagger 里会出现 /knowledge-bases 相关接口。
app.include_router(knowledge_base_router)


@app.on_event("startup")
def on_startup() -> None:
    """应用启动时执行的初始化逻辑。

    当前只做一件事：根据 SQLModel 模型创建 SQLite 数据表。
    后续如果要初始化 Chroma、加载模型配置，也可以放在这里或拆到独立模块。
    """

    create_db_and_tables()


@app.get("/health")
def health_check() -> dict[str, str]:
    """健康检查接口。

    用于确认后端服务是否正常启动。
    验收方式：访问 GET /health，看到 {"status": "ok"} 就说明服务可用。
    """

    return {"status": "ok"}
