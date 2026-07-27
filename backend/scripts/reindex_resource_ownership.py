#!/usr/bin/env python
"""把 PostgreSQL 已回填 organization_id 的 chunk 重写到 Elasticsearch。

U4 改造后，旧 ES 文档缺少 organization_id，kNN filter 会安全地排除它们。
部署迁移后运行本脚本，才能让存量 chunk 带上组织过滤字段重新可检索。
"""

import argparse
import sys
from pathlib import Path

from sqlmodel import Session, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import check_database_ready, engine
from app.db.models import Chunk
from app.services.vector_service import (
    activate_index_alias,
    build_index_alias_name,
    build_concrete_index_name,
    get_elasticsearch_client,
    index_chunks,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reindex chunks with organization ownership")
    parser.add_argument("--knowledge-base-id", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be greater than zero")

    check_database_ready()
    indexed_count = 0
    with Session(engine) as session:
        statement = select(Chunk).order_by(Chunk.knowledge_base_id, Chunk.id)
        if args.knowledge_base_id is not None:
            statement = statement.where(Chunk.knowledge_base_id == args.knowledge_base_id)
        chunks = list(session.exec(statement).all())
        by_knowledge_base: dict[int, list[Chunk]] = {}
        for chunk in chunks:
            by_knowledge_base.setdefault(chunk.knowledge_base_id, []).append(chunk)

        client = get_elasticsearch_client()
        for knowledge_base_id, grouped_chunks in by_knowledge_base.items():
            # 所有 batch 都先写新版本具体索引；这个知识库全部成功后才切 alias。
            for start in range(0, len(grouped_chunks), args.batch_size):
                batch = grouped_chunks[start : start + args.batch_size]
                from app.services.vector_service import embed_chunks

                result = index_chunks(batch, embed_chunks(batch), activate_alias=False)
                for chunk, vector_id in zip(batch, result.vector_ids):
                    chunk.vector_id = vector_id
                    session.add(chunk)
                indexed_count += len(batch)
                session.commit()

            activate_index_alias(
                client,
                alias_name=build_index_alias_name(knowledge_base_id),
                concrete_index_name=build_concrete_index_name(knowledge_base_id),
            )

    print(f"Reindexed {indexed_count} chunks with organization ownership")


if __name__ == "__main__":
    main()
