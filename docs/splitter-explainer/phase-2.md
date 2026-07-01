# 切分讲解：Phase 2

## Phase 2 做了什么

Phase 2 的核心不是新增切分规则，而是把已经存在的 Markdown / TXT 规则，真正迁到统一的 `DocumentElement` 体系里。

也就是说，现在的主流程不再是：

```text
原始文本
  -> 直接在 section_builder 里一边扫文本一边产出 Section / Block
```

而是变成：

```text
原始文本
  -> Parser
  -> DocumentElement[]
  -> Section
  -> Block
  -> Chunk
```

这样做的价值很直接：

1. Markdown / TXT 和后面的 Word / PDF / Excel 可以共用中间层
2. section 规则和 parser 规则分开，后续改动不会全挤在一个文件里
3. metadata 可以从 parser 阶段开始持续往后传

---

## 这一步落到哪些文件

### 1. Markdown parser

文件：

[backend/app/services/document_splitter/parsers/markdown_parser.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/parsers/markdown_parser.py:1)

这个文件负责把 Markdown 文本先拆成 `DocumentElement`。

当前会识别这些 element：

- `heading`
- `paragraph`
- `list`
- `table`
- `code`

它不直接决定 chunk 怎么拼，只负责先把结构识别出来。

例如：

```markdown
## 第二节

正文

- 列表
```

会先变成大致这样的元素序列：

```text
heading -> paragraph -> list
```

并且每个元素都会带上：

- `heading_path`
- `block_type`
- `source_parser`
- `source_index`

所以后面的 section builder 不需要再重新猜一遍结构。

---

### 2. Plain text parser

文件：

[backend/app/services/document_splitter/parsers/plain_text_parser.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/parsers/plain_text_parser.py:1)

这个文件负责处理 TXT 以及普通纯文本 fallback。

当前策略是两层：

1. 先做 `detect_plain_text_headings`
2. 如果检测到多个可靠标题，就走“按标题切元素”
3. 如果没有可靠标题，就退回“按空行切段落”

所以它兼容两类文本：

- 有章节结构的 TXT
- 没有明显标题的普通文本

这里保留了你前面定下来的规则：

- plain text 标题检测是 fallback
- 不会为了硬切 section 牺牲正文完整性

---

### 3. splitter.py

文件：

[backend/app/services/document_splitter/splitter.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/splitter.py:1)

这个文件现在的关键变化是：

- `parse_splitter_source()` 已经不只是包装原文本
- Markdown / TXT 会在这里直接生成 `elements`

也就是：

```text
parse_splitter_source
  -> md  -> parse_markdown_elements
  -> txt -> parse_plain_text_elements
  -> pdf -> 先保留 pdf_pages fallback
```

这说明 pipeline 里最前面的 parse 阶段已经真正开始承担格式解析职责了。

---

### 4. section_builder.py

文件：

[backend/app/services/document_splitter/section_builder.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:1)

这个文件现在多了一层职责：

- 既支持旧入口：`split_markdown_sections(text)`
- 也支持新入口：`build_sections_from_elements(elements, file_type)`

你可以把它理解成：

```text
旧接口继续保留
新实现逐步迁到 DocumentElement
```

这一步最重要，因为它保证了：

- 外部 API 不用立刻全改
- 内部结构已经换到新架构

---

## 现在 Markdown / TXT 是怎么走的

### Markdown

```text
Markdown 原文
  -> parse_markdown_elements
  -> heading / paragraph / list / table / code 元素
  -> build_markdown_sections_from_elements
  -> Section
  -> Block
  -> assemble_chunks
```

保留的关键规则：

- 有 `##` 时，`##` 作为主 section 边界
- `#` 更像文档标题 context
- `###` 及以下先保留在 `heading_path` 里
- 第一个 `##` 前如果只有 `# 文档标题`，不会单独生成 section

### Plain text

```text
TXT / 普通文本
  -> detect_plain_text_headings
  -> 有可靠标题: heading + paragraph elements
  -> 无可靠标题: paragraph elements
  -> build_plain_text_sections_from_elements
  -> Section / Block
  -> assemble_chunks
```

保留的关键规则：

- 检测到多个可靠标题时，按标题切 section
- 没有可靠标题时，整篇按段落处理
- 不让普通编号列表轻易误判成章节结构

---

## 为什么这一阶段重要

从工程角度看，Phase 2 解决的是“结构识别和后续切片耦合太紧”的问题。

以前如果你要改：

- Markdown heading 规则
- TXT 标题检测
- source metadata 透传

很容易同时动到 section 和 block 构建逻辑。

现在改 parser，会更聚焦：

- parser 只关心“识别成什么元素”
- section builder 只关心“这些元素怎么组成 section”
- chunk assembler 只关心“这些 block 怎么拼成 chunk”

这就是后面接 Word / PDF / Excel 时最需要的稳定边界。

---

## 当前边界

Phase 2 现在完成的是：

- Markdown -> `DocumentElement`
- TXT -> `DocumentElement`
- `Section / Block` 继续复用现有规则

还没进入这一阶段的有：

- PDF 细粒度 `DocumentElement` 解析
- Word parser
- Excel / CSV parser

这些会继续在后续 phase 往里接。

---

## 一句话理解

Phase 2 的本质是：

```text
先把 Markdown / TXT 的“结构识别”独立出来，
让它们先说清楚自己是什么元素，
再进入后面的 section / chunk 流程。
```
