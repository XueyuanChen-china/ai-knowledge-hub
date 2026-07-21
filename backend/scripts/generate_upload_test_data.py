#!/usr/bin/env python3
"""生成一个超过默认 5 MiB part_size 的本地上传测试文件。"""

from pathlib import Path


TARGET_BYTES = 6 * 1024 * 1024
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "tests/fixtures/large_file_upload/sample_large_policy.txt"


def main() -> None:
    paragraph = (
        "## 供应商风险复核测试段落\n\n"
        "本段用于验证大文件上传的 multipart 分片、断点续传、SHA256 校验、"
        "下载阶段、文档解析、文本切片、Embedding 和 Elasticsearch 索引。"
        "采购委员会需要结合金额、数据访问权限、交付风险和合同责任进行复核。\n\n"
    )
    content = (paragraph * ((TARGET_BYTES // len(paragraph)) + 1)).encode("utf-8")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(content[:TARGET_BYTES])
    print(f"created {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
