# text_splitter.py 代码解读

文件位置：

```text
backend/app/services/text_splitter.py
```

这个文件的职责是：

```text
只负责把文本切成 ChunkData
不操作数据库
不关心 FastAPI 请求
不写 chunks 表
```

也就是说，它是一个纯服务模块。

数据库写入发生在：

```text
backend/app/api/document.py
```

里面的：

```python
POST /documents/{document_id}/chunks
```

## 整体流程

核心入口是：

```python
split_document_text(...)
```

整体流程可以理解成：

```text
输入文档文本
  ↓
根据 file_type 选择切分策略
  ↓
生成 TextUnit
  ↓
assemble_chunks 把 TextUnit 组装成 ChunkData
  ↓
返回 list[ChunkData]
```

当前支持：

```text
txt：按段落 / 句子 / 固定窗口
md：优先按 Markdown 标题结构
pdf：优先按页码和页内段落
```

## 默认参数

```python
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200
```

含义：

```text
chunk_size=1000：每个 chunk 尽量不超过 1000 字符
chunk_overlap=200：相邻 chunk 尽量保留一部分上下文
```

这里的目标不是机械凑满 1000 字，而是：

```text
在不破坏文本结构的前提下，让 chunk 尽量接近 1000
```

为什么要接近 1000？

如果 chunk 太小：

```text
信息量不足，检索命中后上下文不完整
```

如果 chunk 太大：

```text
embedding 表达会混杂
检索不够精准
后续塞给大模型也浪费上下文
```

所以 1000 是第一版里比较稳的折中值。

## 三个数据结构

### `TextUnit`

```python
@dataclass
class TextUnit:
    content: str
    metadata: dict[str, Any]
```

`TextUnit` 是切分过程中的中间单元。

它可能来自：

```text
一个 TXT 段落
一个 Markdown section
PDF 某一页里的一个段落
```

它还带着 metadata，例如：

```json
{
  "file_type": "pdf",
  "page_start": 3,
  "page_end": 3,
  "splitter": "pdf_page_paragraph"
}
```

### `ChunkData`

```python
@dataclass
class ChunkData:
    content: str
    metadata: dict[str, Any]
```

`ChunkData` 是最终结果。

后续 API 会把它写入：

```text
chunks.content
chunks.metadata_json
```

### `PdfPageText`

```python
@dataclass
class PdfPageText:
    page_number: int
    text: str
```

它用于 PDF 切分。

`page_number` 用 1-based 页码，也就是用户看到的页码：

```text
第 1 页
第 2 页
第 3 页
```

这样以后回答引用来源时更自然。

## 入口函数：`split_document_text`

```python
def split_document_text(
    text: str,
    file_type: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    pdf_pages: Optional[list[PdfPageText]] = None,
) -> list[ChunkData]:
```

这个函数做三件事：

```text
1. 校验 chunk_size / chunk_overlap
2. 根据 file_type 选择切分策略
3. 调用 assemble_chunks 生成最终 chunk
```

核心分支：

```python
if normalized_file_type == "md":
    units = split_markdown(text)
elif normalized_file_type == "pdf" and pdf_pages is not None:
    units = split_pdf_pages(pdf_pages)
else:
    units = split_plain_text(text, normalized_file_type)
```

也就是说：

```text
md 走 Markdown 结构切分
pdf 有页码数据时走 PDF 页切分
其他情况走普通文本切分
```

## 参数校验：`validate_splitter_options`

```python
def validate_splitter_options(chunk_size: int, chunk_overlap: int) -> None:
```

它防止传入不合理参数。

例如：

```text
chunk_size <= 0
chunk_overlap < 0
chunk_overlap >= chunk_size
```

这些都不允许。

为什么 `chunk_overlap` 不能大于等于 `chunk_size`？

因为固定窗口切分时步长是：

```python
step = chunk_size - chunk_overlap
```

如果 overlap 比 chunk_size 还大，step 就会小于等于 0，切分会出问题。

## Markdown 切分：`split_markdown`

Markdown 的优势是有标题结构。

代码会识别：

```text
# 一级标题
## 二级标题
### 三级标题
```

核心正则：

```python
re.match(r"^(#{1,6})\s+(.+)$", line)
```

它会识别 1 到 6 级标题。

### heading_stack 是什么

```python
heading_stack: list[str] = []
```

它保存当前标题路径。

例如：

```md
# 公司制度
## 报销制度
### 差旅报销
```

当前路径就是：

```python
["公司制度", "报销制度", "差旅报销"]
```

这个路径会写入 metadata：

```json
{
  "heading_path": ["公司制度", "报销制度", "差旅报销"]
}
```

### 为什么处理代码块

```python
if stripped.startswith("```"):
    in_code_block = not in_code_block
```

如果 Markdown 代码块里出现：

```md
# 这不是标题
```

不应该把它识别成标题。

所以代码块内不会做标题匹配。

## 普通文本切分：`split_plain_text`

```python
paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
```

这句表示：

```text
先按空行切段落
```

例如：

```text
第一段

第二段

第三段
```

会变成 3 个 `TextUnit`。

每个段落会带 metadata：

```json
{
  "file_type": "txt",
  "paragraph_start": 0,
  "paragraph_end": 0,
  "splitter": "paragraph_sentence"
}
```

注意：这里函数名里的 `paragraph_sentence` 不是说它马上切句子。

当前实际逻辑是：

```text
先切段落
如果单个段落超过 chunk_size
再进入 split_large_unit 按句子切
如果句子也切不开
最后才固定窗口切
```

## PDF 切分：`split_pdf_pages`

PDF 的关键是保留页码。

输入是：

```python
list[PdfPageText]
```

也就是：

```text
第 1 页文本
第 2 页文本
第 3 页文本
```

每一页内部再按空行切段落。

metadata 会保存：

```json
{
  "file_type": "pdf",
  "page_start": 1,
  "page_end": 1,
  "paragraph_start": 0,
  "paragraph_end": 0,
  "splitter": "pdf_page_paragraph"
}
```

后续如果一个 chunk 合并了多页内容，`merge_metadata` 会把页码范围合并成：

```json
{
  "page_start": 1,
  "page_end": 2
}
```

## 组装 chunk：`assemble_chunks`

这是整个文件最核心的函数。

它接收多个 `TextUnit`，然后把它们组装成接近 `chunk_size` 的 `ChunkData`。

核心目标：

```text
小段落合并
大段落细切
尽量不超过 1000
保留 overlap
```

### 正常小段落怎么处理

如果一个段落不超过 1000，就尝试放入当前 chunk。

代码会计算：

```python
next_length = current_length + separator_length + len(content)
```

如果加入后不超过 `chunk_size`，就合并进去。

例如：

```text
段落 1：300 字
段落 2：250 字
段落 3：350 字
```

合起来 900 字，就会形成一个 chunk。

这比每段单独一个 chunk 更好，因为上下文更完整。

### 加入后超过 1000 怎么处理

如果当前 chunk 已经有内容，再加一个段落会超过 1000：

```python
if current_parts and next_length > chunk_size:
```

就会先把当前 chunk 写出去：

```python
flush_current_chunk(...)
```

然后从上一块末尾取 overlap：

```python
overlap_text = build_overlap_text(current_parts, chunk_overlap)
```

再开始组装下一个 chunk。

### 超长段落怎么处理

这是你刚才指出的重点。

当前代码不是直接固定切超长段落。

实际逻辑是：

```python
if len(content) > chunk_size:
    chunks.extend(split_large_unit(content, unit.metadata, chunk_size, chunk_overlap))
```

进入：

```python
split_large_unit(...)
```

而 `split_large_unit` 的逻辑是：

```text
先按句子切
如果能切出多个句子，就把句子重新组装成 chunk
如果只有一个超长句子，最后才固定窗口切
```

所以正确顺序是：

```text
长段落
  ↓
先按句号 / 问号 / 感叹号 / 分号切句子
  ↓
句子组装成接近 1000 的 chunk
  ↓
单个句子仍然超过 1000
  ↓
固定窗口兜底
```

固定窗口只是最后兜底，不是优先策略。

## 长文本处理：`split_large_unit`

```python
sentences = split_sentences(content)
if len(sentences) <= 1:
    return split_by_fixed_window(...)
```

这段含义是：

```text
如果文本能切成多个句子，就走句子切分
如果切不出句子，说明可能是一整个超长字符串，只能固定窗口切
```

例如：

```text
这是第一句。这是第二句。这是第三句。
```

会按句子处理。

但如果是：

```text
aaaaaaaaaaaaaaaaaaaa...
```

没有标点，只能固定窗口。

## 分句：`split_sentences`

```python
parts = re.split(r"(?<=[。！？；.!?;])\s*", text)
```

支持中文和英文标点：

```text
。！？；
. ! ? ;
```

这个是轻量分句，不是严格 NLP 分句。

第一版够用。

后续如果要更准，可以接：

```text
中文分词 / 句子边界检测库
```

## 固定窗口兜底：`split_by_fixed_window`

```python
step = chunk_size - chunk_overlap
```

默认：

```text
chunk_size=1000
chunk_overlap=200
step=800
```

也就是：

```text
第 1 块：0-1000
第 2 块：800-1800
第 3 块：1600-2600
```

中间重叠 200 字。

这个函数只应该处理最后兜底场景：

```text
单个句子太长
没有可用标点
超长连续字符串
```

## 写出 chunk：`flush_current_chunk`

```python
content = "\n\n".join(part for part in parts if part.strip()).strip()
```

多个段落合并时，用两个换行隔开。

这样 chunk 内部仍然能保留段落感，而不是全部挤成一行。

最后生成：

```python
ChunkData(
    content=content,
    metadata=merge_metadata(...)
)
```

## 合并 metadata：`merge_metadata`

如果一个 chunk 合并了多个段落或多个页，就需要合并 metadata。

例如 PDF chunk 合并了第 1 页和第 2 页：

```json
{
  "page_start": 1,
  "page_end": 2
}
```

代码里对 start 类字段取最小值：

```python
for key in ("page", "page_start", "paragraph_start"):
    merged[key] = min(values)
```

对 end 类字段取最大值：

```python
for key in ("page_end", "paragraph_end"):
    merged[key] = max(values)
```

这样就能表达一个 chunk 覆盖的范围。

## overlap：`build_overlap_text`

```python
def build_overlap_text(parts: list[str], chunk_overlap: int) -> str:
```

这个函数从上一块末尾取一小段内容，作为下一块开头。

它优先保留完整段落：

```text
能放完整段落，就放完整段落
如果一个段落本身超过 overlap，就只取末尾 200 字
```

这样比机械取最后 200 字稍微稳一点。

## 文本标准化：`normalize_text`

```python
return text.replace("\r\n", "\n").replace("\r", "\n").strip()
```

作用：

```text
统一 Windows / macOS / Linux 换行
去掉首尾空白
避免生成空 chunk
```

## 当前实现的真实切分顺序

最终可以总结为：

```text
Markdown：
  标题 section
    ↓
  section 太长就按句子
    ↓
  句子太长才固定窗口

TXT：
  空行段落
    ↓
  段落太长就按句子
    ↓
  句子太长才固定窗口

PDF：
  页
    ↓
  页内段落
    ↓
  段落太长就按句子
    ↓
  句子太长才固定窗口
```

## 一个小问题

当前 `split_plain_text` 里的 metadata 写的是：

```python
"splitter": "paragraph_sentence"
```

但它这一层实际只做了段落切分。

真正的句子切分发生在：

```python
split_large_unit
```

所以这个名字略微提前表达了整体策略，不是函数本身马上切句子。

如果想更严谨，可以改成：

```python
"splitter": "paragraph"
```

然后在 `split_large_unit` 中再变成：

```python
"paragraph_sentence"
```

这不是功能 bug，只是命名可以更准确。

## 后续可优化点

### 1. Markdown heading_path 合并

当前如果一个 chunk 合并多个 Markdown section，只保留第一个 heading_path。

后续可以保存：

```json
{
  "heading_paths": [
    ["制度", "报销"],
    ["制度", "请假"]
  ]
}
```

### 2. token 级别长度

当前用字符数控制：

```text
chunk_size=1000 字符
```

后续接模型时，更精确的方式是按 token 数控制。

### 3. 更强的句子切分

当前分句是正则：

```text
。！？；.!?;
```

后续可以接更专业的中文句子切分工具。

### 4. 表格和代码块保护

Markdown 表格、代码块最好尽量不要切断。

当前只避免把代码块里的 `#` 误判成标题，还没有做表格整体保护。

## 一句话总结

`text_splitter.py` 的核心思路是：

```text
先保护文档结构，再控制 chunk 大小。
能按标题/页码/段落切，就不要硬切。
只有句子都切不开时，才用固定窗口兜底。
```
