# 切分讲解：Phase 0

## 这篇文档讲什么

这篇文档专门解释多格式文档切分方案的 `Phase 0`。

`Phase 0` 不解决“所有格式都能切分”这个问题。  
它解决的是另外一个更基础的问题：

```text
先把切分系统的公共数据结构和接口固定下来。
```

如果这一步不先做，后面继续加 `Markdown / TXT / PDF / Word / Excel / CSV` 时，代码很容易重新变成“所有逻辑都堆在一个文件里”。

---

## Phase 0 的目标

Phase 0 只做四件事：

1. 固化统一模型
2. 固化统一接口
3. 提供一个最小 metadata 工具层
4. 让现有 `text_splitter.py` 开始复用这些公共结构

这一步的重点不是“功能变多”，而是“结构先站稳”。

---

## 为什么要先做 Phase 0

当前项目里，切分能力最开始是从：

- Markdown
- TXT
- PDF 文本

这些低复杂度格式起步的。

这没有问题，但如果后面要继续接：

- DOCX
- CSV
- Excel
- 更复杂的 PDF layout

就会遇到一个问题：

```text
不同格式会产生不同的中间结果。
```

比如：

- Markdown 有标题层级
- PDF 有页码
- Excel 有 sheet / row / column
- CSV 有 header / rows
- Word 有 style

如果没有统一模型，后面每接一个格式，就会多一套自己的字段和自己的处理方式，最后 `section builder` 和 `chunk assembler` 会非常难维护。

所以 Phase 0 的核心价值是：

```text
把“后面所有格式都要共用的东西”先抽出来。
```

---

## 当前落地的文件

这一步新增了一个新的基础目录：

```text
backend/app/services/document_splitter/
```

里面当前有 4 个文件：

```text
__init__.py
models.py
interfaces.py
metadata.py
```

它们分别负责不同层次的事。

---

## models.py 负责什么

文件位置：

[backend/app/services/document_splitter/models.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/models.py:1)

这个文件负责放“切分系统最核心的数据结构”。

当前包含：

- `SplitterOptions`
- `DocumentElement`
- `Block`
- `Section`
- `ChunkData`
- `PdfPageText`

### 1. SplitterOptions

这个类表示统一切分参数：

```python
SplitterOptions(
    target_chunk_size=850,
    max_chunk_size=1000,
    chunk_overlap=200,
)
```

它的作用是把原来散落在各函数里的配置，收敛成一个统一对象。

后面不管是 Markdown、PDF 还是 Excel，最终进入 chunk assembler 时，都应该尽量吃这一套参数。

### 2. DocumentElement

这是后续多格式架构里最关键的模型。

可以把它理解成：

```text
原始文件被 parser 解析之后，得到的统一中间层元素。
```

比如：

- 一个标题
- 一段正文
- 一个表格
- 一个列表
- 一个代码块
- Excel 里的一个表格区域

都可以先表示成 `DocumentElement`。

这个类当前已经预留了很多多格式字段：

- `element_type`
- `text`
- `level`
- `page_start / page_end`
- `sheet_name`
- `row_start / row_end`
- `col_start / col_end`
- `bbox`
- `metadata`

另外还加了三个稳定标识字段：

- `element_id`
- `parent_id`
- `source_index`

这几个字段现在还没大规模用起来，但后面很有价值：

- 调试 normalizer 时，知道元素从哪来
- 调试 section builder 时，知道哪些元素被合并了
- 做回归对比时，知道哪一个 element 在哪一步发生了变化

### 3. Block

`Block` 表示一个 `Section` 内的结构块。

比如：

- `heading`
- `paragraph`
- `list`
- `table`
- `code`

当前系统里，很多 chunk 规则本质上是“按 block 类型决定怎么拆”：

- paragraph 按句子
- table 按行
- code 按代码行

所以 `Block` 是 chunk assembler 很重要的输入。

### 4. Section

`Section` 表示语义分区。

例如：

```text
["项目背景"]
["技术方案", "模型结构"]
["财务报表", "收入表"]
```

现在 Markdown / TXT 的主切分已经用到了它。  
后面 Excel / CSV / PDF / DOCX 也会统一往这个模型上靠。

### 5. ChunkData

这个类表示最终准备入库的 chunk。

也就是：

```text
最终写入 chunks 表的数据形态
```

它只有两个核心字段：

- `content`
- `metadata`

这是一个很刻意的设计。  
因为 chunk 的正文和 metadata 才是后续存储、检索、追溯最需要的东西。

### 6. PdfPageText

这个模型不是最终架构的核心，但现在还需要保留。

因为当前 PDF 还处在“文本 fallback”阶段，接口里仍然会传：

```python
list[PdfPageText]
```

所以 Phase 0 没有把它删掉，而是把它一起纳入统一模型层。

这样后面即使 PDF parser 升级了，这个过渡模型也还是有明确归属。

---

## interfaces.py 负责什么

文件位置：

[backend/app/services/document_splitter/interfaces.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/interfaces.py:1)

这个文件的作用，是先把未来系统的“四段主流程接口”定下来。

当前定义了 4 个 `Protocol`：

- `DocumentParser`
- `DocumentNormalizer`
- `SectionBuilder`
- `ChunkAssembler`

### 1. DocumentParser

职责：

```text
原始输入 -> DocumentElement[]
```

以后不同格式都会实现自己的 parser，例如：

- `MarkdownParser`
- `PlainTextParser`
- `DocxParser`
- `PdfParser`
- `CsvParser`
- `XlsxParser`

### 2. DocumentNormalizer

职责：

```text
DocumentElement[] -> 清洗后的 DocumentElement[]
```

这是企业级切分里非常重要的一层，因为很多脏数据不是 parser 解析错了，而是原始文档本身有问题。

例如：

- PDF 页眉页脚重复
- OCR 断行
- Excel merged cells
- Word 空段落

这些都更适合在 normalizer 层做，而不是把 parser 搞得越来越重。

### 3. SectionBuilder

职责：

```text
DocumentElement[] -> Section[]
```

这一层专门决定：

- 哪些地方应该开启新 section
- heading_path 怎么维护
- fallback 怎么退

它不关心 chunk size，不关心 overlap，它只处理“结构边界”。

### 4. ChunkAssembler

职责：

```text
Block[] -> ChunkData[]
```

它只关心 chunk 怎么组装：

- 是否超过 `max_chunk_size`
- overlap 怎么做
- 表格按行切还是按列切
- 代码按行切

这层和 parser / section builder 分开之后，逻辑会清楚很多。

---

## metadata.py 负责什么

文件位置：

[backend/app/services/document_splitter/metadata.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/metadata.py:1)

这个文件当前很小，只做了一件事：

- 定义 `MetadataDict`
- 提供 `merge_metadata_dicts()`

为什么这一步就要把它单独抽出来？

因为 metadata 后面一定会越来越重。

例如：

- parser 会加 `file_type`
- PDF parser 会加 `page_start / page_end`
- Word parser 会加 `style_name`
- Excel parser 会加 `sheet_name / row_start / row_end`
- chunk assembler 会加 `target_chunk_size / max_chunk_size / chunk_overlap`

这些字段最后会在多个阶段逐步叠加。  
所以 metadata 合并迟早要成为一个独立关注点。

Phase 0 先做一个最小版工具层，是合理的。

---

## text_splitter.py 在 Phase 0 里发生了什么变化

文件位置：

[backend/app/services/text_splitter.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/text_splitter.py:1)

这一步没有重写现有切分逻辑。  
做的事更克制：

```text
不改当前主逻辑，只把数据结构的定义移到公共模型层。
```

也就是说：

- `Block`
- `Section`
- `ChunkData`
- `PdfPageText`
- 默认 chunk 参数

这些现在不再定义在 `text_splitter.py` 里，而是从：

```python
app.services.document_splitter.models
```

导入。

这样做的好处是：

1. 当前功能不容易被大改动搞坏
2. 新架构已经开始落地
3. 后面 Phase 1 再拆 pipeline 时，基础模型不用再重复迁一次

这是一种比较稳的重构方式：

```text
先抽公共模型，再抽流程，再迁逻辑。
```

---

## Phase 0 做了什么，没做什么

### 已经做了

- 建立统一模型层
- 建立统一接口层
- 建立 metadata 工具层
- 让现有 splitter 复用统一模型
- 保持现有测试继续通过

### 还没做

- 还没有真正把 `splitter pipeline` 抽成独立模块
- 还没有把 Markdown / TXT 变成 `DocumentElement parser`
- 还没有实现 Word / PDF / CSV / Excel parser
- 还没有把 section builder / chunk assembler 完整拆出去

所以你可以把 Phase 0 理解成：

```text
不是功能升级阶段，而是架构打地基阶段。
```

---

## 为什么 Phase 0 不直接把所有东西都拆掉

因为如果一上来就同时做下面这些事：

- 抽模型
- 抽接口
- 抽 parser
- 抽 section builder
- 抽 chunk assembler
- 再把 Markdown / TXT / PDF 全迁过去

那这次改动面会非常大，回归风险也高。

当前项目还在快速演进期，更合适的方式是：

1. 先把公共模型固定
2. 让旧逻辑接到新模型
3. 再逐步把流程拆出来

这样每一步都能验证，出问题也容易定位。

---

## 你后面读代码时可以怎么理解

如果你现在再看代码，可以按这个顺序理解：

1. 先看 `models.py`
   - 明白系统里有哪些基础数据对象

2. 再看 `interfaces.py`
   - 明白未来主流程准备拆成哪几层

3. 再看 `text_splitter.py`
   - 明白当前逻辑还暂时集中在哪

也就是说：

```text
models.py 是“系统里有什么东西”
interfaces.py 是“这些东西以后怎么流动”
text_splitter.py 是“现在这些逻辑暂时还堆在哪”
```

这样读会比较顺。

---

## 下一步应该做什么

Phase 0 结束后，最自然的下一步就是 Phase 1：

```text
把统一 pipeline 真正拆出来
```

建议顺序：

1. 新建 `splitter.py`
2. 新建 `section_builder.py`
3. 新建 `chunk_assembler.py`
4. 先让 Markdown / TXT 接入新流程
5. 保持当前测试不退化

这样你会看到整个系统开始从：

```text
一个大文件里做所有事
```

逐步变成：

```text
各层职责清楚的多模块系统
```

---

## 一句话总结

Phase 0 的本质不是“让切分更聪明”，而是：

```text
先把未来多格式切分系统的公共语言定下来。
```

这个“公共语言”，就是：

- 统一模型
- 统一接口
- 统一 metadata 基础设施

只要这一步站稳，后面再接 Word、PDF、Excel、CSV，代码就不会继续失控膨胀。
