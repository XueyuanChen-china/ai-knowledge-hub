#!/usr/bin/env python3
"""用真实 OSS + FastAPI 测试大文件上传和异步索引链路。"""

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def request_json(
    base_url: str,
    path: str,
    method: str,
    payload=None,
    access_token: str = "",
):
    body = None
    headers = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc


def put_part(presigned_url: str, part: bytes, content_type: str) -> str:
    request = urllib.request.Request(
        presigned_url,
        data=part,
        headers={"Content-Type": content_type or "application/octet-stream"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            etag = response.headers.get("ETag", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OSS PUT failed: {exc.code} {detail}") from exc

    if not etag:
        raise RuntimeError("OSS PUT response did not contain an ETag header")
    return etag


def sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as input_file:
        while True:
            data = input_file.read(1024 * 1024)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def upload_file(
    base_url: str,
    knowledge_base_id: int,
    file_path: Path,
    created_by: str,
    access_token: str = "",
) -> str:
    file_size = file_path.stat().st_size
    file_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_sha256 = sha256_file(file_path)

    init = request_json(
        base_url,
        "/uploads/init",
        "POST",
        {
            "knowledge_base_id": knowledge_base_id,
            "filename": file_path.name,
            "file_size": file_size,
            "client_mime_type": file_type,
            "file_sha256": file_sha256,
            "created_by": created_by,
        },
        access_token=access_token,
    )
    upload_id = init["upload_id"]
    part_size = int(init["part_size"])
    total_parts = int(init["total_parts"])
    print(f"upload_id={upload_id} parts={total_parts} part_size={part_size}")

    with file_path.open("rb") as input_file:
        for part_number in range(1, total_parts + 1):
            part = input_file.read(part_size)
            presign = request_json(
                base_url,
                f"/uploads/{upload_id}/parts/presign",
                "POST",
                {"part_number": part_number},
                access_token=access_token,
            )
            etag = put_part(presign["presigned_url"], part, file_type)
            request_json(
                base_url,
                f"/uploads/{upload_id}/parts/complete",
                "POST",
                {
                    "part_number": part_number,
                    "etag": etag,
                    "part_size": len(part),
                },
                access_token=access_token,
            )
            print(f"part {part_number}/{total_parts} uploaded etag={etag}")

    completed = request_json(
        base_url,
        f"/uploads/{upload_id}/complete",
        "POST",
        {"expected_total_parts": total_parts},
        access_token=access_token,
    )
    print(json.dumps(completed, ensure_ascii=False, indent=2))
    return upload_id


def wait_for_index(
    base_url: str,
    knowledge_base_id: int,
    upload_id: str,
    filename: str,
    timeout_seconds: int,
    access_token: str = "",
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        upload = request_json(
            base_url,
            f"/uploads/{upload_id}",
            "GET",
            access_token=access_token,
        )
        upload_document_id = upload.get("document_id")
        print(
            f"upload status={upload['status']} "
            f"processing_status={upload['processing_status']} "
            f"document_id={upload.get('document_id')}"
        )
        documents = request_json(
            base_url,
            f"/documents?knowledge_base_id={knowledge_base_id}",
            "GET",
            access_token=access_token,
        )
        matching = [item for item in documents if item["filename"] == filename]
        current = next(
            (
                item
                for item in matching
                if upload_document_id is not None
                and item.get("id") == upload_document_id
            ),
            None,
        )
        if current and current["status"] == "indexed":
            print(f"PASS: document {current['id']} is indexed")
            return
        if upload["processing_status"] == "failed":
            raise RuntimeError(upload.get("processing_error_message") or "processing failed")
        time.sleep(3)

    raise TimeoutError("Timed out waiting for document indexed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--knowledge-base-id", type=int, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--created-by", default="e2e-test")
    parser.add_argument(
        "--access-token",
        default=os.getenv("E2E_ACCESS_TOKEN", ""),
        help="后端 Bearer token，也可以通过 E2E_ACCESS_TOKEN 提供",
    )
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    if not args.file.is_file():
        raise SystemExit(f"file not found: {args.file}")

    upload_id = upload_file(
        args.base_url,
        args.knowledge_base_id,
        args.file,
        args.created_by,
        args.access_token,
    )
    wait_for_index(
        args.base_url,
        args.knowledge_base_id,
        upload_id,
        args.file.name,
        args.timeout,
        args.access_token,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
