# 切分讲解：Phase 6

## Phase 6 做了什么

这一阶段把第一版 DOCX parser 接进了统一切分框架。

重点不是把 Word 文档导出成纯文本再做 fallback，而是尽量保留 Word 自己的原生结构：

1. heading
2. paragraph
3. list
4. table

也就是说，这一阶段的核心是：

```text
优先使用 Word 原生结构，而不是先降级成 plain text
```

---

## 关键文件

### 1. DOCX parser

文件：

[backend/app/services/document_splitter/parsers/docx_parser.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/parsers/docx_parser.py:1)

当前主链路是：

```text
docx path
  -> python-docx Document()
  -> iter_block_items()
  -> 按原生顺序识别 paragraph / table
  -> heading / list / paragraph / table
  -> DocumentElement[]
```

这里最关键的是 `iter_block_items()`。

它不是只看 `document.paragraphs`，因为那样会漏掉表格顺序。
它是直接按 body 里的原生块顺序遍历：

- paragraph
- table
- paragraph
- table

所以表格不会被挤到最后，也不会丢失和上下文的相对位置。

---

### 2. heading 怎么识别

`detect_docx_heading_level()` 会优先读 Word 的样式信息：

- `Heading 1 ~ Heading 6`
- 中文样式名里的 `标题 1 ~ 标题 6`

识别到之后，会更新 `heading_path`，再产出 `heading DocumentElement`。

也就是说，这里不是靠正文长相猜标题，而是优先信任 Word 自己的样式语义。

这就是“优先使用 Word 原生结构”的核心之一。

---

### 3. list 怎么识别

`is_docx_list_paragraph()` 现在主要看两类信号：

1. 段落样式名里有 `List` / `列表`
2. Word 编号属性 `numPr`

如果是连续 list paragraph，就会在 parser 阶段先聚合成一个 `list DocumentElement`。

例如 Word 里两条连续项目符号：

```text
- 准备发票
- 填写金额
```

会变成一个 `list` 元素，而不是两个普通 paragraph。

这一步的作用是让后面的 chunk assembler 更容易保住列表语义。

---

### 4. table 怎么处理

Word 表格会先被抽成二维行列数据，再转成 Markdown table 文本。

这样做不是因为 Markdown 更高级，而是因为你当前的 table chunk 逻辑已经很稳定：

- 支持表头保留
- 支持按行切 chunk
- 支持 `row_start / row_end / row_count`

所以 DOCX table 直接复用现有 `table splitter`，工程上更稳。

---

## 一个简化例子

假设 Word 文档顺序是：

```text
Heading 1: 员工手册
Paragraph: 第一段正文
List item: 准备材料
List item: 提交审批
Table:
  字段 | 说明
  状态 | 草稿
```

parser 最终会产出大致这样的元素序列：

```text
heading
paragraph
list
table
```

并且每个元素会带：

- `heading_path`
- `style_name`
- `source_parser = docx_parser`

表格还会额外带：

- `has_header`
- `row_start / row_end`
- `col_start / col_end`

---

## 为什么这一步重要

如果 DOCX 先被粗暴提成纯文本，再走 plain text fallback，会有几个问题：

1. heading 样式丢了
2. list 语义容易退化成普通段落
3. table 会变成对齐不稳定的文本块

而这一阶段做完后，Word 文档至少已经能保住：

- 标题层级
- 列表边界
- 表格边界

这对后面的 chunk 质量影响很大。

---

## 上传链路也接上了什么

当前 `.docx` 已经接进上传与切片流程：

- 上传接口允许 `.docx`
- `documents.extracted_text` 会保存一份按结构展开的可读文本摘要
- 真正切 chunk 时，会把 `word_path` 传给 splitter，直接走 DOCX parser

对应文件：

[backend/app/api/document.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/document.py:1)

所以这阶段不是“本地 parser demo”，而是已经能走项目当前实际链路。

---

## 当前边界

这一阶段已经完成：

- `.docx` 基础解析
- Word 原生 paragraph / table 顺序遍历
- heading / paragraph / list / table
- `.docx` 上传与切片接入

还没做的是：

- 图片、批注、脚注、页眉页脚
- 多级编号的更精细恢复
- 合并单元格语义
- 更强的表格 header 检测

这些留到更后面的 Word 增强阶段更合适。

---

## 一句话理解

Phase 6 的本质是：

```text
让 DOCX 先按 Word 自己的结构被理解成 heading / list / table，
再进入统一 chunk pipeline，
而不是先被降成普通文本再去猜。
```
