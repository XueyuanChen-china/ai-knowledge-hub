import hashlib
import json
import re
import unittest
from pathlib import Path

from app.services.document_splitter.splitter import (
    build_document_blocks,
    build_document_sections,
    normalize_splitter_source,
    parse_splitter_source,
    split_document_text,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "multiformat_e2e"
SOURCE_ROOT = FIXTURE_ROOT / "source"


class MultiformatE2EFixtureTests(unittest.TestCase):
    """保证 U10 黄金测试输入和人工标注不会静默漂移。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
        cls.queries = json.loads((FIXTURE_ROOT / "queries.json").read_text(encoding="utf-8"))
        cls.parser_expectations = json.loads(
            (FIXTURE_ROOT / "expected" / "parser_expectations.json").read_text(encoding="utf-8")
        )["documents"]
        cls.chunk_expectations = json.loads(
            (FIXTURE_ROOT / "expected" / "chunk_expectations.json").read_text(encoding="utf-8")
        )

    def test_source_files_match_manifest_hashes(self) -> None:
        self.assertEqual(len(self.manifest["documents"]), 5)
        for document in self.manifest["documents"]:
            source_path = SOURCE_ROOT / document["filename"]
            self.assertTrue(source_path.is_file(), source_path)
            digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
            self.assertEqual(digest, document["sha256"], source_path.name)
            self.assertEqual(source_path.stat().st_size, document["size_bytes"])
            self.assertLessEqual(source_path.stat().st_size, 2 * 1024 * 1024)

    def test_query_set_has_fixed_count_and_categories(self) -> None:
        expected_counts = {
            "factual": 8,
            "semantic": 7,
            "condition": 5,
            "process": 5,
            "cross_format": 4,
            "no_answer": 3,
            "permission": 3,
            "summary": 5,
        }
        self.assertEqual(len(self.queries), 40)
        self.assertEqual(len({item["query_id"] for item in self.queries}), 40)
        actual_counts = {
            category: sum(item["category"] == category for item in self.queries)
            for category in expected_counts
        }
        self.assertEqual(actual_counts, expected_counts)
        document_keys = {item["document_key"] for item in self.manifest["documents"]}
        for query in self.queries:
            self.assertTrue(set(query["expected_document_keys"]).issubset(document_keys))
            if query["category"] in {"no_answer", "permission"}:
                self.assertEqual(query["expected_document_keys"], [])

    def test_current_pipeline_can_parse_and_chunk_every_source(self) -> None:
        for document in self.manifest["documents"]:
            with self.subTest(filename=document["filename"]):
                source_path = SOURCE_ROOT / document["filename"]
                file_type = document["file_type"]
                text = source_path.read_text(encoding="utf-8") if file_type in {"txt", "md"} else ""
                path_options = self.build_path_options(file_type, source_path)
                parsed = normalize_splitter_source(
                    parse_splitter_source(text, file_type, **path_options)
                )
                elements = parsed.elements or []
                parser_expectation = self.parser_expectations[document["document_key"]]
                self.assertTrue(elements)
                parser_names = {
                    element.metadata.get("source_parser") for element in elements
                }
                self.assertEqual(parser_names, {document["expected_parser"]})

                # parser 层断言：每个元素都必须带来源解析器、结构类型和来源序号，
                # 这样后面的 section/block/chunk 出错时还能追溯到原始结构。
                self.assertEqual(len(elements), parser_expectation["expected_element_count"])
                self.assertEqual(
                    len({element.source_index for element in elements}),
                    len(elements),
                )
                for element in elements:
                    self.assertEqual(element.metadata.get("source_parser"), document["expected_parser"])
                    self.assertEqual(element.metadata.get("block_type"), element.element_type)
                    self.assertIn("splitter", element.metadata)

                element_type_counts = {
                    element_type: sum(element.element_type == element_type for element in elements)
                    for element_type in {element.element_type for element in elements}
                }
                self.assertEqual(
                    element_type_counts.get("heading", 0),
                    parser_expectation["expected_heading_count"],
                )
                self.assertEqual(
                    element_type_counts.get("table", 0),
                    parser_expectation["expected_table_count"],
                )
                self.assertEqual(
                    element_type_counts.get("list", 0),
                    parser_expectation["expected_list_count"],
                )
                # 当前 PDF layout parser 会把页码写入 element；DOCX 的页数依赖
                # Word/LibreOffice 排版结果，属于渲染断言而不是 parser 结构断言。
                if file_type == "pdf" and "expected_page_count" in parser_expectation:
                    self.assertEqual(
                        len({element.page_start for element in elements if element.page_start is not None}),
                        parser_expectation["expected_page_count"],
                    )
                if "expected_sheet_count" in parser_expectation:
                    self.assertEqual(
                        len({element.sheet_name for element in elements if element.sheet_name}),
                        parser_expectation["expected_sheet_count"],
                    )
                    self.assertEqual(
                        sorted({element.sheet_name for element in elements if element.sheet_name}),
                        sorted(parser_expectation["expected_sheet_names"]),
                    )
                if "expected_code_count" in parser_expectation:
                    self.assertEqual(
                        element_type_counts.get("code", 0),
                        parser_expectation["expected_code_count"],
                    )

                element_text = "\n".join(element.text for element in elements)
                element_heading_values = {
                    heading
                    for element in elements
                    for heading in element.metadata.get("heading_path", [])
                }
                for required_heading in parser_expectation["required_heading_paths"]:
                    self.assertTrue(
                        required_heading in element_text or required_heading in element_heading_values,
                        f"missing heading/content: {required_heading}",
                    )

                blocks = build_document_blocks(parsed)
                block_types = {block.block_type for block in blocks}
                self.assertTrue(set(document["expected_block_types"]).issubset(block_types))

                # section/block 层断言：section_index、block_index 和 heading_path
                # 必须连续且可追踪，表格不能在 block 层丢失类型。
                sections = build_document_sections(parsed)
                self.assertEqual(len(sections), parser_expectation["expected_section_count"])
                self.assertEqual(len(blocks), parser_expectation["expected_block_count"])
                block_offset = 0
                for section_index, section in enumerate(sections):
                    section_blocks = blocks[block_offset : block_offset + len(section.blocks)]
                    self.assertEqual(
                        [block.metadata.get("block_index") for block in section_blocks],
                        list(range(len(section.blocks))),
                    )
                    for block in section_blocks:
                        self.assertEqual(block.metadata.get("section_index"), section_index)
                        self.assertEqual(block.metadata.get("block_type"), block.block_type)
                        block_heading_path = block.metadata.get("heading_path") or []
                        self.assertEqual(block_heading_path[: len(section.heading_path)], section.heading_path)
                    block_offset += len(section.blocks)

                chunks = split_document_text(text, file_type, **path_options)
                self.assert_chunk_contract(document, chunks)

    def assert_chunk_contract(self, document: dict[str, object], chunks: list[object]) -> None:
        """校验最终 chunk 的边界、来源、标题和表格完整性。"""

        expectation = self.chunk_expectations["documents"][document["document_key"]]
        defaults = self.chunk_expectations["defaults"]
        self.assertGreaterEqual(len(chunks), expectation["min_chunk_count"])
        self.assertLessEqual(len(chunks), expectation["max_chunk_count"])

        combined_content = "\n".join(chunk.content for chunk in chunks)
        for term in expectation["required_content_terms"]:
            self.assertIn(term, combined_content, f"missing chunk content: {term}")

        for chunk in chunks:
            self.assertTrue(chunk.content.strip())
            metadata = chunk.metadata
            self.assertEqual(metadata.get("target_chunk_size"), defaults["target_chunk_size"])
            self.assertEqual(metadata.get("max_chunk_size"), defaults["max_chunk_size"])
            self.assertEqual(metadata.get("chunk_overlap"), defaults["chunk_overlap"])
            self.assertIn("splitter", metadata)
            block_types = metadata.get("block_types") or [metadata.get("block_type")]
            self.assertTrue(any(block_types))

            # 标题存在时，chunk 第一行必须从完整标题开始，不能从标题中间截断。
            heading_paths = metadata.get("heading_paths") or []
            heading_path = metadata.get("heading_path")
            if heading_path:
                heading_paths = [heading_path]
            if heading_paths:
                first_line = chunk.content.splitlines()[0].strip()
                self.assertRegex(first_line, r"^#{1,6}\s+")
                self.assertTrue(any(str(path[-1]) in first_line for path in heading_paths if path))

            if document["file_type"] == "pdf":
                self.assertIn("page_start", metadata)
                self.assertIn("page_end", metadata)
            if document["file_type"] == "xlsx":
                self.assertTrue(metadata.get("sheet_name"))

            body = re.sub(r"^#{1,6}\s+[^\n]+\n*", "", chunk.content.strip())
            self.assertFalse(re.match(r"^[a-z]{1,3}\b", body))
            self.assertFalse(re.match(r"^[,.;:!?，。！？；：、)]", body))

            if "table" in block_types:
                self.assertIn("|", chunk.content)
                self.assertIn("---", chunk.content)

    @staticmethod
    def build_path_options(file_type: str, source_path: Path) -> dict[str, str]:
        if file_type == "pdf":
            return {"pdf_path": str(source_path)}
        if file_type == "docx":
            return {"word_path": str(source_path)}
        if file_type == "xlsx":
            return {"spreadsheet_path": str(source_path)}
        return {}


if __name__ == "__main__":
    unittest.main()
