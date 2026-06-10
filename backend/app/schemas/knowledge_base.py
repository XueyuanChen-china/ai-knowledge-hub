from datetime import datetime

from sqlmodel import SQLModel


class KnowledgeBaseCreate(SQLModel):
    """创建知识库时的请求体。

    客户端只需要传 name 和 description，不需要传 id、created_at 这些数据库字段。
    """

    name: str
    description: str = ""


class KnowledgeBaseUpdate(SQLModel):
    """更新知识库时的请求体。

    Day 3 使用 PUT，所以这里把 name 当作必填字段，表示提交完整的新数据。
    """

    name: str
    description: str = ""


class KnowledgeBaseRead(SQLModel):
    """返回给前端的知识库数据。

    这里包含数据库生成的 id、created_at、updated_at。
    """

    id: int
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
