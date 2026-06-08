# AI Knowledge Hub

企业知识库管理与专家 Agent 平台。

Day 1 目标是先完成后端基础骨架：

- FastAPI 应用入口
- `.env` 配置读取
- SQLite 数据库配置
- SQLModel 初始化
- 健康检查接口 `GET /health`

## 项目结构

```text
ai-knowledge-hub/
  backend/
    app/
      main.py
      config.py
      db/
        database.py
        models.py
    data/
      uploads/
      sqlite/
    .env.example
    requirements.txt
  docs/
    day-01-backend-foundation.md
  README.md
```

## 本地启动

进入后端目录：

```bash
cd backend
```

创建虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

安装依赖：

```bash
pip install -r requirements.txt
```

创建本地环境变量文件：

```bash
cp .env.example .env
```

启动服务：

```bash
uvicorn app.main:app --reload
```

打开健康检查：

```bash
curl http://127.0.0.1:8000/health
```

期望返回：

```json
{"status":"ok"}
```
