# syntax=docker/dockerfile:1

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# libgomp 是 sentence-transformers / PyTorch 在 CPU 环境的常见运行时依赖。
RUN apt-get update \
    && apt-get install --no-install-recommends -y bash libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
# 项目默认 EMBEDDING_DEVICE=cpu。先从 PyTorch CPU 源安装固定 wheel，避免
# 依赖解析在 Linux 镜像里拉取数 GB 的 CUDA 运行库；后续 requirements 安装时
# 会复用已满足的 torch 版本。
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torch==2.5.1 \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# 只复制容器运行所需代码；.dockerignore 会排除 .env、上传原件和测试数据。
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/scripts ./scripts

RUN chmod +x ./scripts/start_api.sh ./scripts/start_worker.sh

EXPOSE 8000

CMD ["./scripts/start_api.sh"]
