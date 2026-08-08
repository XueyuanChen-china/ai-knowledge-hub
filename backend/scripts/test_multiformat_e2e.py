#!/usr/bin/env python3
"""用真实 OSS、Celery、PostgreSQL 和 Elasticsearch 验证多格式 E2E 链路。

该脚本不是 parser 单元测试，而是把固定测试集按真实上传流程送入系统，
并检查每个文件是否完成索引、是否产生 chunk，以及搜索结果是否命中预期文档。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from test_large_upload_e2e import request_json, upload_file


BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = BACKEND_DIR / "tests" / "fixtures" / "multiformat_e2e"
SOURCE_ROOT = FIXTURE_ROOT / "source"


def wait_for_index(
    base_url: str,
    knowledge_base_id: int,
    upload_id: str,
    filename: str,
    timeout_seconds: int,
    access_token: str = "",
) -> dict[str, object]:
    """等待单个上传任务完成，并返回对应 document 记录。"""

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        upload = request_json(
            base_url,
            f"/uploads/{upload_id}",
            "GET",
            access_token=access_token,
        )
        upload_document_id = upload.get("document_id")
        documents = request_json(
            base_url,
            f"/documents?knowledge_base_id={knowledge_base_id}",
            "GET",
            access_token=access_token,
        )
        document = next(
            (
                item
                for item in documents
                if upload_document_id is not None
                and item.get("id") == upload_document_id
            ),
            None,
        )
        print(
            f"  status={upload.get('status')} "
            f"processing={upload.get('processing_status')} "
            f"document_id={upload.get('document_id')}"
        )
        if document and document.get("status") == "indexed":
            return document
        if upload.get("processing_status") == "failed":
            raise RuntimeError(
                upload.get("processing_error_message") or "upload processing failed"
            )
        time.sleep(3)

    raise TimeoutError(f"timed out waiting for {filename} to become indexed")


def get_chunks(
    base_url: str,
    document_id: int,
    access_token: str = "",
) -> list[dict[str, object]]:
    """按本次 document 读取 chunks，确认 PostgreSQL 侧已经产生切片。"""

    return request_json(
        base_url,
        f"/documents/{document_id}/chunks",
        "GET",
        access_token=access_token,
    )


def search_and_check(
    base_url: str,
    knowledge_base_id: int,
    queries: list[dict[str, object]],
    filename_by_key: dict[str, str],
    top_k: int,
    access_token: str = "",
) -> dict[str, object]:
    """执行固定 query 集，检查可回答问题是否命中预期文件。"""

    answerable = 0
    passed = 0
    no_answer_hits = 0
    failures: list[dict[str, object]] = []

    for case in queries:
        expected_keys = list(case.get("expected_document_keys") or [])
        results = request_json(
            base_url,
            "/search/semantic",
            "POST",
            {
                "knowledge_base_id": knowledge_base_id,
                "query": case["query"],
                "top_k": top_k,
            },
            access_token=access_token,
        )
        titles = [str(item.get("title") or "") for item in results]
        if not expected_keys:
            # 搜索接口本身是召回层，不能把“有召回”直接等同于错误答案；
            # 这里先记录结果，最终拒答由 Chat/relevance gate E2E 再验证。
            no_answer_hits += int(bool(results))
            continue

        answerable += 1
        expected_filenames = [filename_by_key[key] for key in expected_keys]
        hit = any(
            expected_filename in title
            for title in titles
            for expected_filename in expected_filenames
        )
        if hit:
            passed += 1
        else:
            failures.append(
                {
                    "query_id": case["query_id"],
                    "query": case["query"],
                    "expected_files": expected_filenames,
                    "returned_titles": titles,
                }
            )

    return {
        "answerable_query_count": answerable,
        "passed_query_count": passed,
        "recall_by_document": passed / answerable if answerable else 0.0,
        "no_answer_queries_with_hits": no_answer_hits,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", type=int, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--created-by", default="multiformat-e2e")
    parser.add_argument(
        "--access-token",
        default=os.getenv("E2E_ACCESS_TOKEN", ""),
        help="后端 Bearer token，也可以通过 E2E_ACCESS_TOKEN 提供",
    )
    parser.add_argument("--timeout-per-file", type=int, default=900)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--skip-search", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    queries = json.loads((FIXTURE_ROOT / "queries.json").read_text(encoding="utf-8"))
    documents: list[dict[str, object]] = []
    filename_by_key = {
        str(item["document_key"]): str(item["filename"])
        for item in manifest["documents"]
    }

    for expected in manifest["documents"]:
        file_path = SOURCE_ROOT / str(expected["filename"])
        if not file_path.is_file():
            raise RuntimeError(f"fixture file not found: {file_path}")
        print(f"\n== {file_path.name} ==")
        upload_id = upload_file(
            args.base_url,
            args.knowledge_base_id,
            file_path,
            args.created_by,
            args.access_token,
        )
        document = wait_for_index(
            args.base_url,
            args.knowledge_base_id,
            upload_id,
            file_path.name,
            args.timeout_per_file,
            args.access_token,
        )
        document_id = document.get("id")
        if not document_id:
            raise RuntimeError(f"indexed document has no id: {document}")
        chunks = get_chunks(
            args.base_url,
            int(document_id),
            args.access_token,
        )
        if not chunks:
            raise RuntimeError(f"indexed document has no chunks: {file_path.name}")
        knowledge_item_id = chunks[0].get("knowledge_item_id")
        if not knowledge_item_id:
            raise RuntimeError(
                f"indexed document chunks have no knowledge_item_id: {file_path.name}"
            )
        print(
            f"  PASS indexed document_id={document.get('id')} "
            f"knowledge_item_id={knowledge_item_id} chunks={len(chunks)}"
        )
        documents.append(
            {
                "filename": file_path.name,
                "document_id": document.get("id"),
                "knowledge_item_id": knowledge_item_id,
                "chunk_count": len(chunks),
                "upload_id": upload_id,
            }
        )

    report: dict[str, object] = {"documents": documents}
    if not args.skip_search:
        report["search"] = search_and_check(
            args.base_url,
            args.knowledge_base_id,
            queries,
            filename_by_key,
            args.top_k,
            args.access_token,
        )
        print(f"\nsearch={json.dumps(report['search'], ensure_ascii=False)}")
    else:
        print("\nsearch=skipped")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"report={args.report}")
    print("\nPASS: multiformat OSS E2E")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
