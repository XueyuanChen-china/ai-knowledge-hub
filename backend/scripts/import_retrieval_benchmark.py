"""把已准备的公开检索基准导入当前 PostgreSQL/Elasticsearch 链路。

语料已经是 passage 级单元，因此这里不会再次调用项目的 TextSplitter，避免把
公开基准的 corpus id 与本地 chunk 边界混在一起。每个 passage 对应一个 active
KnowledgeItem 和一个 Chunk，benchmark_doc_id 写入 metadata 供 qrels 对齐。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlmodel import Session, select


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import engine
from app.db.models import Chunk, KnowledgeBase, KnowledgeItem, User
from app.services import vector_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--knowledge-base-id", type=int, required=True)
    parser.add_argument("--created-by-user-id", type=int, required=True)
    parser.add_argument("--dataset", required=True, help="例如 BEIR/scifact")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=64)
    return parser.parse_args()


def load_corpus(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def import_benchmark(args: argparse.Namespace) -> None:
    corpus = load_corpus(args.source_dir / "corpus.jsonl")
    if not corpus:
        raise ValueError("corpus.jsonl must contain at least one passage")

    with Session(engine) as session:
        knowledge_base = session.exec(
            select(KnowledgeBase).where(
                KnowledgeBase.id == args.knowledge_base_id,
                KnowledgeBase.organization_id == args.organization_id,
            )
        ).one_or_none()
        if knowledge_base is None:
            raise ValueError("knowledge base does not belong to organization")
        if session.get(User, args.created_by_user_id) is None:
            raise ValueError("created-by user does not exist")

        chunks_to_index: list[Chunk] = []
        created_items = 0
        existing_items = 0
        for item in corpus:
            external_id = str(item["_id"])
            benchmark_tag = f"benchmark:{args.dataset}:{external_id}"
            knowledge_item = session.exec(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_base_id == args.knowledge_base_id,
                    KnowledgeItem.tags == benchmark_tag,
                )
            ).one_or_none()
            if knowledge_item is None:
                knowledge_item = KnowledgeItem(
                    organization_id=args.organization_id,
                    created_by_user_id=args.created_by_user_id,
                    knowledge_base_id=args.knowledge_base_id,
                    title=str(item.get("title", ""))[:200] or external_id,
                    content=str(item.get("text", "")),
                    tags=benchmark_tag,
                    status="active",
                    source_type="benchmark",
                )
                session.add(knowledge_item)
                session.flush()
                created_items += 1
            else:
                existing_items += 1

            chunk = session.exec(
                select(Chunk).where(
                    Chunk.knowledge_item_id == knowledge_item.id,
                    Chunk.chunk_index == 0,
                )
            ).one_or_none()
            if chunk is None:
                chunk = Chunk(
                    organization_id=args.organization_id,
                    knowledge_base_id=args.knowledge_base_id,
                    knowledge_item_id=knowledge_item.id,
                    chunk_index=0,
                    content=str(item.get("text", "")),
                    metadata_json=json.dumps(
                        {
                            "benchmark_dataset": args.dataset,
                            "benchmark_doc_id": external_id,
                            "title": str(item.get("title", "")),
                            "source": "public_retrieval_benchmark",
                        },
                        ensure_ascii=False,
                    ),
                )
                session.add(chunk)
                session.flush()
            if not chunk.vector_id:
                chunks_to_index.append(chunk)

            if len(chunks_to_index) >= args.batch_size:
                session.commit()
                index_and_persist(session, chunks_to_index)
                chunks_to_index = []

        session.commit()
        if chunks_to_index:
            index_and_persist(session, chunks_to_index)

    print(
        f"Imported dataset={args.dataset} corpus={len(corpus)} "
        f"created_items={created_items} existing_items={existing_items}"
    )


def index_and_persist(session: Session, chunks: list[Chunk]) -> None:
    result = vector_service.add_chunks(chunks)
    for chunk, vector_id in zip(chunks, result.vector_ids):
        chunk.vector_id = vector_id
        session.add(chunk)
    session.commit()
    print(f"Indexed batch chunks={len(chunks)} index={result.index_name}")


if __name__ == "__main__":
    import_benchmark(parse_args())
