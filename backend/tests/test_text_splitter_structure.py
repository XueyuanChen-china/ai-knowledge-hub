import unittest
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.text_splitter import (
    detect_plain_text_headings,
    PdfPageText,
    flatten_sections_to_blocks,
    is_only_heading_context_section,
    split_markdown_sections,
    split_document_text,
    split_pdf_sections,
    split_plain_text_sections,
)


class TextSplitterStructureTests(unittest.TestCase):
    def test_markdown_sections_and_blocks_keep_structure_boundaries(self) -> None:
        markdown_text = """# 第一章

第一段内容。

- 列表 1
- 列表 2
  列表续行

| 列 1 | 列 2 |
| --- | --- |
| A | B |

```python
print("hello")
print("world")
```

## 第二节

第二节正文。
"""

        sections = split_markdown_sections(markdown_text)

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].heading_path, ["第一章"])
        self.assertEqual(sections[1].heading_path, ["第一章", "第二节"])

        first_section_block_types = [block.block_type for block in sections[0].blocks]
        self.assertEqual(
            first_section_block_types,
            ["heading", "paragraph", "list", "table", "code"],
        )

        self.assertEqual(
            sections[0].blocks[3].content,
            "| 列 1 | 列 2 |\n| --- | --- |\n| A | B |",
        )
        self.assertEqual(
            sections[0].blocks[4].content,
            '```python\nprint("hello")\nprint("world")\n```',
        )

        flattened_blocks = flatten_sections_to_blocks(sections)
        self.assertEqual(flattened_blocks[0].metadata["heading_path"], ["第一章"])
        self.assertEqual(flattened_blocks[-1].metadata["heading_path"], ["第一章", "第二节"])

    def test_markdown_uses_h2_as_default_section_boundary(self) -> None:
        markdown_text = """# 文档标题

总览内容。

## 第一节

第一节正文。

### 第一节-子节A

A内容。

### 第一节-子节B

B内容。

## 第二节

第二节正文。
"""

        sections = split_markdown_sections(markdown_text)

        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[0].heading_path, ["文档标题"])
        self.assertEqual(sections[1].heading_path, ["文档标题", "第一节"])
        self.assertEqual(sections[2].heading_path, ["文档标题", "第二节"])

        flattened_blocks = flatten_sections_to_blocks(sections)
        subsection_paragraphs = [
            block
            for block in flattened_blocks
            if block.block_type == "paragraph" and block.content in {"A内容。", "B内容。"}
        ]
        self.assertEqual(
            [block.metadata["section_index"] for block in subsection_paragraphs],
            [1, 1],
        )
        self.assertEqual(
            [block.metadata["heading_path"] for block in subsection_paragraphs],
            [
                ["文档标题", "第一节", "第一节-子节A"],
                ["文档标题", "第一节", "第一节-子节B"],
            ],
        )

    def test_markdown_falls_back_to_h1_when_no_h2_exists(self) -> None:
        markdown_text = """# 第一章

第一章内容。

# 第二章

第二章内容。
"""

        sections = split_markdown_sections(markdown_text)

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].heading_path, ["第一章"])
        self.assertEqual(sections[1].heading_path, ["第二章"])

    def test_heading_context_before_first_h2_does_not_create_section(self) -> None:
        markdown_text = """# 文档标题

## 第一节

第一节正文。

## 第二节

第二节正文。
"""

        sections = split_markdown_sections(markdown_text)

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].heading_path, ["文档标题", "第一节"])
        self.assertEqual(sections[1].heading_path, ["文档标题", "第二节"])

    def test_heading_context_with_preface_body_still_creates_section(self) -> None:
        markdown_text = """# 文档标题

导语内容。

## 第一节

第一节正文。
"""

        sections = split_markdown_sections(markdown_text)

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].heading_path, ["文档标题"])
        self.assertEqual(sections[1].heading_path, ["文档标题", "第一节"])

    def test_is_only_heading_context_section(self) -> None:
        self.assertTrue(
            is_only_heading_context_section(
                ["# 文档标题", "", "   "],
                section_boundary_level=2,
            )
        )
        self.assertFalse(
            is_only_heading_context_section(
                ["# 文档标题", "", "导语内容。"],
                section_boundary_level=2,
            )
        )
        self.assertFalse(
            is_only_heading_context_section(
                ["## 第一节"],
                section_boundary_level=2,
            )
        )

    def test_plain_text_becomes_paragraph_blocks(self) -> None:
        text = "第一段第一行\n第一段第二行\n\n第二段\n\n第三段"

        sections = split_plain_text_sections(text, "txt")

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].heading_path, [])
        self.assertEqual(
            [block.block_type for block in sections[0].blocks],
            ["paragraph", "paragraph", "paragraph"],
        )
        self.assertEqual(sections[0].blocks[0].content, "第一段第一行\n第一段第二行")

    def test_plain_text_headings_create_multiple_sections(self) -> None:
        text = """第一章 总则

这里是第一章内容。

第二章 范围

这里是第二章内容。
"""

        sections = split_plain_text_sections(text, "txt")

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].heading_path, ["第一章 总则"])
        self.assertEqual(sections[1].heading_path, ["第二章 范围"])
        self.assertEqual(sections[0].blocks[0].block_type, "heading")
        self.assertEqual(sections[0].blocks[1].content, "这里是第一章内容。")

    def test_plain_text_without_reliable_multiple_headings_falls_back(self) -> None:
        text = """1. 公司应当建立制度。
2. 公司应当持续改进。

这是后续说明段落。
"""

        sections = split_plain_text_sections(text, "txt")

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].heading_path, [])
        self.assertTrue(all(block.block_type == "paragraph" for block in sections[0].blocks))

    def test_detect_plain_text_headings(self) -> None:
        lines = [
            "第一章 总则",
            "",
            "这里是第一章内容。",
            "",
            "1.1 适用范围",
            "",
            "这里是适用范围内容。",
        ]

        self.assertEqual(
            detect_plain_text_headings(lines),
            [
                (0, 1, "第一章 总则"),
                (4, 2, "1.1 适用范围"),
            ],
        )

    def test_pdf_becomes_page_sections_with_paragraph_blocks(self) -> None:
        pages = [
            PdfPageText(page_number=1, text="第一页第一段\n\n第一页第二段"),
            PdfPageText(page_number=2, text="第二页唯一段"),
        ]

        sections = split_pdf_sections(pages)

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].metadata["page_start"], 1)
        self.assertEqual(sections[1].metadata["page_start"], 2)
        self.assertEqual(
            [block.block_type for block in sections[0].blocks],
            ["paragraph", "paragraph"],
        )
        self.assertEqual(sections[0].blocks[0].metadata["page_start"], 1)
        self.assertEqual(sections[1].blocks[0].content, "第二页唯一段")

    def test_markdown_chunks_keep_heading_prefix_and_do_not_cross_heading(self) -> None:
        markdown_text = """# 第一章

这是第一章第一句。这是第一章第二句。这是第一章第三句。这是第一章第四句。这是第一章第五句。这是第一章第六句。

## 第二节

这是第二节第一句。这是第二节第二句。这是第二节第三句。这是第二节第四句。这是第二节第五句。
"""

        chunks = split_document_text(
            markdown_text,
            "md",
            target_chunk_size=20,
            max_chunk_size=30,
            chunk_overlap=10,
        )

        self.assertGreaterEqual(len(chunks), 4)
        self.assertTrue(all(chunk.content.startswith("#") for chunk in chunks))

        first_section_chunks = [chunk for chunk in chunks if chunk.metadata["heading_path"] == ["第一章"]]
        second_section_chunks = [
            chunk
            for chunk in chunks
            if chunk.metadata["heading_path"] == ["第一章", "第二节"]
        ]

        self.assertTrue(first_section_chunks)
        self.assertTrue(second_section_chunks)
        self.assertTrue(all(chunk.content.startswith("# 第一章") for chunk in first_section_chunks))
        self.assertTrue(all(chunk.content.startswith("## 第二节") for chunk in second_section_chunks))
        self.assertTrue(all("## 第二节" not in chunk.content for chunk in first_section_chunks))

    def test_semantic_overlap_does_not_start_with_half_sentence(self) -> None:
        markdown_text = """# 标题

第一句内容比较长一些。第二句内容比较长一些。第三句内容比较长一些。第四句内容比较长一些。
"""

        chunks = split_document_text(
            markdown_text,
            "md",
            target_chunk_size=18,
            max_chunk_size=26,
            chunk_overlap=8,
        )

        self.assertGreaterEqual(len(chunks), 2)

        expected_body_starts = {
            "第一句内容比较长一些。",
            "第二句内容比较长一些。",
            "第三句内容比较长一些。",
            "第四句内容比较长一些。",
        }

        for chunk in chunks:
            body = chunk.content.split("\n\n", 1)[1]
            first_body_line = body.split("\n\n", 1)[0]
            self.assertIn(first_body_line, expected_body_starts)

    def test_markdown_table_chunks_do_not_start_from_middle_row(self) -> None:
        markdown_text = """# 表格节

| 列1 | 列2 |
| --- | --- |
| 第一行很长的数据A | 第一行很长的数据B |
| 第二行很长的数据A | 第二行很长的数据B |
| 第三行很长的数据A | 第三行很长的数据B |
"""

        chunks = split_document_text(
            markdown_text,
            "md",
            target_chunk_size=40,
            max_chunk_size=70,
            chunk_overlap=16,
        )

        self.assertGreaterEqual(len(chunks), 2)
        table_header = "| 列1 | 列2 |\n| --- | --- |"
        self.assertTrue(all(table_header in chunk.content for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
