# 切分讲解：Phase 1

## Phase 1 做了什么

Phase 1 的目标不是增加很多新格式，而是先把切分系统从：

```text
一个文件里同时做 parse / section / block / chunk
```

拆成更清楚的统一流程：

```text
parse
  -> normalize
  -> build_sections
  -> build_blocks
  -> assemble_chunks
```

这一步先让：

- Markdown
- TXT
- PDF 文本 fallback

走到新框架里。

---

## 新增的核心文件

### 1. splitter.py

文件位置：

[backend/app/services/document_splitter/splitter.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/splitter.py:1)

这是新的统一入口。

当前主流程是：

1. `parse_splitter_source`
2. `normalize_splitter_source`
3. `build_document_sections`
4. `build_document_blocks`
5. `assemble_chunks`

注意：

这里的 `parse` 还是 Phase 1 的过渡版。  
它先把输入包装成 `ParsedSplitterSource`，还没有完全升级到 `DocumentElement parser`。  
那个是 Phase 2 要做的事。

---

### 2. normalizer.py

文件位置：

[backend/app/services/document_splitter/normalizer.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/normalizer.py:1)

这个文件当前负责最小规范化工作：

- `normalize_file_type`
- `normalize_document_text`
- `normalize_text`

另外补了一个最小实现：

- `IdentityDocumentNormalizer`

现在它还很轻，但后面 PDF / OCR / Word 进来之后，这里会继续长出：

- 页眉页脚去重
- OCR 断行修复
- 空白标准化
- merged cells 展开

所以现在先把位置定住很重要。

---

### 3. section_builder.py

文件位置：

[backend/app/services/document_splitter/section_builder.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:1)

这个文件承接了原来 `text_splitter.py` 里最核心的结构层逻辑：

- `split_markdown_sections`
- `split_plain_text_sections`
- `split_pdf_sections`
- `flatten_sections_to_blocks`

以及 Markdown / plain text 的辅助函数。

现在可以这样理解它的职责：

```text
决定文档怎么分 section
决定 section 内怎么变成 blocks
```

但它还不负责 chunk size 和 overlap。

再直白一点说，这个文件主要负责两件事：

1. 先决定：

```text
这份文档应该怎么分成 Section
```

2. 再决定：

```text
每个 Section 里面有哪些 Block
```

也就是说，它做的是“结构建模”，不是“长度切分”。

---

### 3.1 这个文件在主流程里的位置

如果把现在的 Phase 1 pipeline 展开看：

```text
splitter.py
  -> parse_splitter_source
  -> normalize_splitter_source
  -> build_document_sections
       -> section_builder.build_sections_from_source
  -> build_document_blocks
       -> section_builder.flatten_sections_to_blocks
  -> chunk_assembler.assemble_chunks
```

那 `section_builder.py` 实际上站在中间：

- 它接收“已经标准化过的原始文本”
- 输出“后面 chunk assembler 能吃的 Block 列表”

所以它是：

```text
原始文本结构
    ->
Section / Block 结构
    ->
Chunk 结构
```

中间最关键的一层。

---

### 3.2 build_sections_from_source()

函数位置：

[build_sections_from_source](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:7)

这是这个文件的统一入口函数。

它的作用很简单：

```text
根据 file_type，把文本分发到不同的 section 构建逻辑
```

当前 Phase 1 只支持三条分支：

- `md` -> `split_markdown_sections`
- `pdf` 且有 `pdf_pages` -> `split_pdf_sections`
- 其他 -> `split_plain_text_sections`

你可以把它理解成：

```text
section builder 层的 router
```

它本身不做复杂解析，只负责把不同来源送到正确策略。

---

### 3.3 Markdown 路径：split_markdown_sections()

函数位置：

[split_markdown_sections](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:25)

这个函数是 Markdown 的主结构构建器。

它的职责不是简单“遇到标题就切”，而是同时处理三件事：

1. 决定主 section 边界级别
2. 维护标题层级栈
3. 把每个 section 内的原始行再转成 blocks

它的主规则现在是：

- 有 `##` 时，`##` 作为主 section 边界
- `#` 作为文档标题上下文
- `###` 及以下默认不单独开主 section，只更新 `heading_path`

这个函数内部最重要的几个变量是：

- `heading_stack`
  - 当前看到的完整标题路径
- `current_heading_path`
  - 当前主 section 的标题路径
- `current_heading_context_path`
  - 当前主 section 之前的上层标题上下文
- `current_lines`
  - 当前 section 暂存的原始文本行

所以它本质上在做的是：

```text
扫描 Markdown 原始行
  -> 遇到主 boundary 时切 Section
  -> 遇到子标题时只更新路径
  -> 最后把 section 里的行交给 build_markdown_blocks()
```

---

### 3.4 detect_markdown_section_boundary_level()

函数位置：

[detect_markdown_section_boundary_level](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:123)

这个函数只做一件事：

```text
Markdown 里主 section 默认按哪一级标题切
```

当前规则很明确：

- 如果文档里出现过 `##`
  - 主边界就是 level 2
- 否则
  - 退化成 level 1

这个函数的价值在于：

```text
把“Markdown 主边界判断规则”单独收敛成一个地方
```

这样后面如果你要改：

- `##` 优先
- 或者支持配置化 boundary level

就不用再去动 `split_markdown_sections()` 的主循环。

---

### 3.5 is_only_heading_context_section()

函数位置：

[is_only_heading_context_section](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:97)

这个函数是一个很关键的小辅助函数。

它解决的问题是：

```text
如果文档前面只有 # 文档标题，没有正文，
遇到第一个 ## 时，要不要先生成一个空的前置 Section？
```

答案是：

- 如果前面只有上层标题上下文，不生成 Section
- 如果前面已经有正文，就正常生成前置 Section

所以这个函数的作用可以概括成：

```text
避免“只有文档标题，没有正文”的假 Section
```

它是 Markdown 结构质量提升里一个很实际的点。

---

### 3.6 append_markdown_section()

函数位置：

[append_markdown_section](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:146)

这个函数负责把：

- `heading_path`
- `level`
- `current_lines`

真正落成一个 `Section` 对象。

它内部会调用：

- `build_markdown_blocks()`

也就是说，`split_markdown_sections()` 负责“什么时候该结束一个 section”，  
`append_markdown_section()` 负责“结束之后怎么把它实体化”。

这是一个典型的“扫描逻辑”和“构建逻辑”拆开的写法，后面维护起来会比全塞在一个循环里清楚。

---

### 3.7 Plain Text 路径：split_plain_text_sections()

函数位置：

[split_plain_text_sections](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:170)

这个函数是纯文本的主入口。

它的思路是两层策略：

1. 先尝试识别可靠标题
2. 如果识别不到多个可靠标题，就退回纯段落模式

所以它不是简单按空行切，而是：

```text
先试结构化
失败再退回非结构化
```

这一步对：

- TXT
- OCR 文本
- DOCX 提取纯文本 fallback
- PDF 文本 fallback

都很重要，因为很多格式失败后，最终都会退到 plain text 形态。

---

### 3.8 detect_plain_text_headings() 及相关函数

核心函数位置：

- [detect_plain_text_headings](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:186)
- [is_plain_text_heading_candidate](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:211)
- [is_isolated_plain_text_heading_line](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:229)
- [detect_plain_text_heading_level](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:238)

这几组函数合起来，组成 plain text 的“标题检测器”。

它们分工是：

#### `detect_plain_text_headings`

负责扫描所有行，找出标题候选。

#### `is_plain_text_heading_candidate`

负责判断某一行“长得像不像标题”。

比如支持：

- `第一章 总则`
- `1.1 适用范围`
- `一、项目背景`
- `（1）定义`

同时会排掉：

- 太长的行
- 句号结尾的普通正文句子

#### `is_isolated_plain_text_heading_line`

负责判断这一行是不是相对独立。

这个判断的目的，是避免把正文里的某个编号句子误判成标题。

#### `detect_plain_text_heading_level`

负责给识别出来的标题一个粗粒度 level。

虽然现在 plain text 的 level 还比较粗，但这个字段后面继续做多级 `heading_path` 时会很有用。

---

### 3.9 build_plain_text_heading_sections()

函数位置：

[build_plain_text_heading_sections](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:249)

这个函数负责把“检测到的标题列表”真正变成 `Section[]`。

它会做两件事：

1. 如果第一个标题前面就已经有正文内容
   - 先生成一个前置 section

2. 然后按标题边界，把后续文本分成多个 section

所以它是 plain text 标题检测和 section 实体构建之间的桥梁。

---

### 3.10 build_plain_text_heading_blocks() / build_plain_text_paragraph_blocks()

函数位置：

- [build_plain_text_heading_blocks](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:308)
- [build_plain_text_paragraph_blocks](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:344)

这两个函数负责 plain text 的 block 构造。

其中：

#### `build_plain_text_heading_blocks`

会先生成一个 `heading block`，再把剩余正文生成 `paragraph blocks`。

也就是说，按标题切 section 之后，section 里面仍然保留结构信息：

- 第一个 block 是标题
- 后面是段落

#### `build_plain_text_paragraph_blocks`

负责按空行切段落，生成 `paragraph block`。

这是 plain text 最基础的 block builder，也会给 paragraph 记录：

- `heading_path`
- `paragraph_start`
- `paragraph_end`
- `splitter`

---

### 3.11 PDF fallback 路径：split_pdf_sections()

函数位置：

[split_pdf_sections](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:366)

这个函数现在还是 PDF 的基础 fallback 版。

它的逻辑比较保守：

- 以页为 section
- 页内按空行切 paragraph block

所以它解决的是：

```text
先让 PDF 文本能进入统一 Section / Block 体系
```

但它还没有解决更复杂的问题：

- 跨页章节
- 标题跨页延续
- 页眉页脚污染
- 多栏阅读顺序

这些会留到后面的 PDF phase。

---

### 3.12 flatten_sections_to_blocks()

函数位置：

[flatten_sections_to_blocks](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:398)

这是 `section_builder.py` 和 `chunk_assembler.py` 之间最关键的连接点。

它做的事是：

```text
把 Section 结构展开成 Block 列表
同时把 section 级 metadata 合并到 block 上
```

它会补充这些很关键的信息：

- `heading_path`
- `section_heading_path`
- `section_level`
- `section_index`
- `block_index`

这些字段后面为什么重要？

因为 chunk assembler 在做：

- 不跨主 section pack
- overlap
- metadata merge

时，都要依赖这些字段。

所以 `flatten_sections_to_blocks()` 本质上不是简单的“拍平数组”，  
而是：

```text
把 section 语义信息显式下沉到 block 层
```

这一步做完后，后面的 chunk assembler 才能只面向 `Block[]` 工作。

---

### 3.13 build_markdown_blocks() 在这个文件里的角色

函数位置：

[build_markdown_blocks](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:421)

这个函数虽然名字叫 `build_markdown_blocks`，但它在这个文件里地位很重要。

因为 `split_markdown_sections()` 只负责切出“章节范围”，  
真正决定 section 内有哪些结构块的，是这个函数。

它会识别：

- heading
- paragraph
- list
- table
- code

也就是说：

```text
Markdown 的 section 是 split_markdown_sections() 切的
Markdown 的 block 是 build_markdown_blocks() 建的
```

这样 Section 和 Block 两层职责就完全分开了。

---

### 4. chunk_assembler.py

文件位置：

[backend/app/services/document_splitter/chunk_assembler.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/chunk_assembler.py:1)

这个文件承接了原来 `text_splitter.py` 里所有“长度控制和 overlap”的逻辑：

- `validate_splitter_options`
- `assemble_chunks`
- paragraph / list / table / code 的切分规则
- semantic overlap
- metadata merge

也就是说：

```text
Section / Block 怎么来，不归它管
Chunk 怎么拼，归它管
```

这是这一步最关键的职责分离。

再直白一点说，这个文件负责的是：

```text
拿到一串已经结构化好的 Block
按照长度、section 边界、block 类型、overlap 规则
拼成最终要入库的 ChunkData
```

---

### 4.1 它不是“直接按 1000 字硬切”

这个文件现在的拼接逻辑，不是：

```text
全文每 1000 个字符切一刀
```

而是分两步：

1. **先预处理 block**
   - 看每个 block 会不会太大
   - 如果太大，按 block 类型先拆小

2. **再组装 chunk**
   - 尽量接近 `target_chunk_size`
   - 不超过 `max_chunk_size`
   - 不跨主 section
   - flush 后再补语义 overlap

所以它更像：

```text
结构优先的 chunk 组装器
```

不是简单长度切割器。

---

### 4.2 这份文件里的主流程怎么走

代码侧的主线其实很清楚：

1. `validate_splitter_options()`
   - 先校验参数是否合法

2. `assemble_chunks()`
   - 主入口
   - 真正负责把一串 block 拼成多个 chunk

3. `prepare_blocks_for_packing()`
   - 在正式 pack 前，先把过大的 block 拆成更适合拼接的小块

4. `flush_current_chunk()`
   - 当当前 chunk 该结束时，真正落成一个 `ChunkData`

5. `merge_metadata()`
   - 把多个 block 的 metadata 合成一个 chunk 级 metadata

所以如果你只想抓主干，可以先看：

```text
assemble_chunks
  -> prepare_blocks_for_packing
  -> flush_current_chunk
  -> merge_metadata
```

---

### 4.3 assemble_chunks() 是怎么拼的

函数位置：

[assemble_chunks](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/chunk_assembler.py:27)

它现在的大逻辑是：

#### 1. 先拿到适合 pack 的 blocks

先调用：

- `prepare_blocks_for_packing()`

把明显过大的 block 先拆掉，避免后面边拼边处理太多特殊情况。

#### 2. 按顺序往当前 chunk 里加 block

它会维护一个“当前 chunk 缓冲区”：

- `current_blocks`
- `current_length`

然后一个 block 一个 block 往里放。

#### 3. 遇到主 section 边界就先结束

它不是单纯按长度控制。  
如果发现下一个 block 属于新的 `section_index`，就会先 flush 当前 chunk。

所以这里实现的是：

```text
默认不跨主 section pack
```

#### 4. 接近 target 时优先结束，超过 max 时强制结束

这就是 `target_chunk_size` 和 `max_chunk_size` 两层控制的意义：

- `target`
  - 希望拼到这里左右
- `max`
  - 绝对不能超过这里

所以 chunk 的大小控制不是一个硬阈值，而是一个“理想值 + 上限值”的双阈值结构。

#### 5. flush 之后补语义 overlap

结束一个 chunk 后，不是直接从零开始拼下一个 chunk。  
它会先把上一个 chunk 的一部分语义单元带过去。

这一步用的是：

- `build_semantic_overlap_blocks()`

这样下一个 chunk 的开头更完整，不容易出现半句、半个表格行这种情况。

---

### 4.4 这个文件里的函数大概分 4 组

你不用一开始逐个读所有函数，可以先把它们按组理解。

#### 第一组：主流程函数

这组函数负责“真正拼 chunk”：

- `validate_splitter_options`
- `assemble_chunks`
- `flush_current_chunk`
- `merge_metadata`

这是最核心的一组。

#### 第二组：block 预处理函数

这组函数负责“把过大的 block 先拆小”：

- `prepare_blocks_for_packing`
- `split_block_for_packing`
- `split_text_block`
- `split_list_block`
- `split_table_block`
- `split_code_block`
- `split_block_by_fixed_window`

这组函数决定的是：

```text
不同 block 类型太大时，应该怎么拆
```

#### 第三组：overlap 和边界函数

这组函数负责“不要让下一个 chunk 开头太难看”：

- `build_semantic_overlap_blocks`
- `build_overlap_units_from_block`
- `build_table_overlap_rows`
- `build_code_overlap_lines`

这组函数对应的是：

```text
overlap 不按字符硬截，而按语义单元复制
```

#### 第四组：兜底工具函数

这组函数负责一些底层工具能力：

- `split_sentences`
- `split_list_items`
- `split_text_to_windows`
- `choose_window_end`
- `align_window_start`
- `is_split_boundary`
- `calculate_blocks_length`
- `build_code_chunk_text`
- `is_markdown_table_separator`

这些函数不是主线，但它们让主流程更稳。

---

### 4.5 不同 block 现在是怎么拼的

你可以只记这几个核心规则：

#### paragraph

- 小段落直接拼
- 太长先按句子拆
- 再不行才固定窗口兜底

#### list

- 优先按 item 拆
- item 太长再按句子拆
- 最后固定窗口兜底

#### table

- 优先按完整行拆
- 每个 chunk 尽量保留表头
- 不从半行开始

#### code

- 优先按代码行拆
- 尽量保留 fenced code block 结构
- 不从半行开始

所以它的设计思想一直是：

```text
先保语义边界
最后才用固定长度兜底
```

---

### 4.6 为什么这个文件重要

因为最终进数据库、进向量库、进检索系统的，不是 `Section`，也不是 `Block`，而是：

```text
ChunkData
```

而 `chunk_assembler.py` 正是决定：

- chunk 长什么样
- chunk 从哪里开始、在哪里结束
- overlap 怎么做
- metadata 怎么汇总

这一步如果设计粗糙，前面 section / block 做得再好，最后检索质量还是会掉。

所以它是整个切分链路里非常靠后的阶段，但对最终效果影响非常大。

---

## text_splitter.py 现在是什么角色

文件位置：

[backend/app/services/text_splitter.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/text_splitter.py:1)

现在它已经不再是“真正承载所有逻辑的实现文件”了。  
它现在更像一个：

```text
兼容层 + 导出层
```

作用是：

- 保持旧的 import 路径不变
- 把现有 API 继续暴露给上层
- 内部实际转发到 `document_splitter/` 下面的新模块

这样做的好处是：

- `document.py` 这种上层调用代码不用立刻改
- 测试可以先继续跑
- 内部实现已经切到新架构

这是一种很稳的迁移方式。

---

## Phase 1 和 Phase 0 的区别

### Phase 0

重点是：

```text
先把公共模型和接口抽出来
```

### Phase 1

重点是：

```text
开始把真实流程拆成多个模块
```

所以两步的关系是：

```text
Phase 0 = 先定系统里的“对象”
Phase 1 = 再定这些对象如何流动
```

---

## 当前还没做完的地方

虽然叫“统一 pipeline”，但 Phase 1 还是过渡版。

还没完成的点主要有：

1. `parse` 还不是 `DocumentElement[]`
2. Markdown / TXT 还没有真正改造成独立 parser 模块
3. `section_builder` 还在直接处理原始文本
4. `chunk_assembler` 还没有完全解耦成更小的策略模块

所以你可以把 Phase 1 理解成：

```text
先把流程形状拆出来，但底层输入还没完全标准化。
```

---

## 这一阶段的实际收益

虽然这一步看起来像“只是拆文件”，但收益很实在：

1. 后面加新格式时，知道应该接到哪一层
2. 结构问题和 chunk 问题不再混在一起
3. 测试更容易分层写
4. `text_splitter.py` 不会继续膨胀成单点大文件

这对后面接：

- DOCX
- CSV
- Excel
- PDF layout

都非常重要。

---

## 下一步应该怎么接

Phase 1 结束后，最自然的下一步就是 Phase 2：

```text
把 Markdown / TXT 迁到真正的 DocumentElement parser
```

也就是把现在的：

```text
原始文本 -> section_builder
```

逐步升级成：

```text
原始文本 -> parser -> DocumentElement[] -> section_builder
```

这样 Phase 0 和 Phase 1 才会真正闭环。

---

## 一句话总结

Phase 1 的本质不是“新增切分规则”，而是：

```text
先把切分系统的主流程拆出来，让旧逻辑开始跑进新骨架。
```

这一步做完后，系统已经从“单文件实现”进入“多模块流水线实现”的状态了。
