"""下载并裁剪一个公开 IR 基准，生成当前项目可使用的评测输入。

默认使用 BEIR SciFact 的 test split。脚本不把公开语料提交进 Git，只在本地
生成 corpus.jsonl、cases.json 和 manifest.json，方便固定版本后重复导入与评测。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import urllib.request
import zipfile
from pathlib import Path


DATASET_URLS = {
    "scifact": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
    "nfcorpus": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nfcorpus.zip",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATASET_URLS), default="scifact")
    parser.add_argument("--split", default="test", choices=("test", "dev", "train"))
    parser.add_argument("--query-limit", type=int, default=100)
    parser.add_argument("--negative-per-query", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260804)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/retrieval_benchmarks/scifact-mini"),
    )
    parser.add_argument("--force-download", action="store_true")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_dataset(dataset: str, output_dir: Path, *, force: bool) -> Path:
    archive_path = output_dir / "source.zip"
    output_dir.mkdir(parents=True, exist_ok=True)
    if force or not archive_path.exists():
        print(f"Downloading {dataset} from the official BEIR mirror...")
        urllib.request.urlretrieve(DATASET_URLS[dataset], archive_path)
    return archive_path


def extract_dataset(archive_path: Path, output_dir: Path, dataset: str) -> Path:
    extracted_dir = output_dir / "source"
    marker = extracted_dir / dataset / "corpus.jsonl"
    if marker.exists():
        return extracted_dir / dataset

    extracted_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted_dir)
    return extracted_dir / dataset


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_qrels(path: Path) -> dict[str, dict[str, float]]:
    qrels: dict[str, dict[str, float]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle):
            if line_number == 0 and line.startswith("query-id"):
                continue
            columns = line.rstrip("\n").split("\t")
            if len(columns) == 3:
                query_id, corpus_id, score = columns
            elif len(columns) == 4:
                query_id, _iteration, corpus_id, score = columns
            else:
                raise ValueError(f"Unexpected qrels columns at line {line_number + 1}")
            qrels.setdefault(query_id, {})[corpus_id] = float(score)
    return qrels


def prepare_subset(args: argparse.Namespace) -> None:
    archive_path = download_dataset(args.dataset, args.output_dir, force=args.force_download)
    dataset_dir = extract_dataset(archive_path, args.output_dir, args.dataset)

    queries = load_jsonl(dataset_dir / "queries.jsonl")
    corpus = load_jsonl(dataset_dir / "corpus.jsonl")
    qrels = load_qrels(dataset_dir / "qrels" / f"{args.split}.tsv")
    corpus_by_id = {str(item["_id"]): item for item in corpus}

    selected_queries = [
        item for item in queries if str(item["_id"]) in qrels and qrels[str(item["_id"])]
    ][: args.query_limit]
    if not selected_queries:
        raise RuntimeError(f"No queries with qrels found for split={args.split}")

    rng = random.Random(args.seed)
    all_corpus_ids = sorted(corpus_by_id)
    selected_ids: set[str] = set()
    cases: list[dict] = []
    for query in selected_queries:
        query_id = str(query["_id"])
        relevant = {
            str(doc_id): score
            for doc_id, score in qrels[query_id].items()
            if score > 0 and str(doc_id) in corpus_by_id
        }
        if not relevant:
            continue

        negative_pool = [doc_id for doc_id in all_corpus_ids if doc_id not in relevant]
        negative_count = min(args.negative_per_query, len(negative_pool))
        selected_ids.update(relevant)
        selected_ids.update(rng.sample(negative_pool, negative_count))
        cases.append(
            {
                "case_id": f"{args.dataset}-{args.split}-{query_id}",
                "category": "external_ir",
                "query": str(query["text"]),
                "expected_chunk_ids": [],
                "expected_external_ids": sorted(relevant),
                "relevance_by_external_id": {
                    str(doc_id): score for doc_id, score in relevant.items()
                },
                "dataset": f"BEIR/{args.dataset}",
                "source_query_id": query_id,
                "notes": "公开检索基准 qrels；不用于企业权限或无答案指标。",
            }
        )

    corpus_output = args.output_dir / "corpus.jsonl"
    with corpus_output.open("w", encoding="utf-8") as handle:
        for corpus_id in sorted(selected_ids):
            item = corpus_by_id[corpus_id]
            handle.write(
                json.dumps(
                    {
                        "_id": corpus_id,
                        "title": str(item.get("title", "")),
                        "text": str(item.get("text", "")),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    (args.output_dir / "cases.json").write_text(
        json.dumps(
            {
                "version": "beir-mini-v1",
                "description": "固定裁剪的公开 BEIR 检索评测集。",
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "dataset": f"BEIR/{args.dataset}",
                "source_url": DATASET_URLS[args.dataset],
                "source_archive_sha256": sha256_file(archive_path),
                "split": args.split,
                "query_limit": args.query_limit,
                "negative_per_query": args.negative_per_query,
                "seed": args.seed,
                "query_count": len(cases),
                "corpus_count": len(selected_ids),
                "qrels": "positive relevance only",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Prepared dataset={args.dataset} queries={len(cases)} "
        f"corpus={len(selected_ids)} output={args.output_dir}"
    )


if __name__ == "__main__":
    prepare_subset(parse_args())
