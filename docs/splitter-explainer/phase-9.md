# 切分讲解：Phase 9

## Phase 9 做了什么

这一阶段不是继续“加新 parser”，而是把前面 0 到 8 阶段已经做出来的能力固化下来。

目标有三个：

1. 固化样本集
2. 固化 `elements / sections / blocks / chunks` 输出
3. 建立一组能长期复用的质量指标

也就是说，Phase 9 解决的是：

```text
以后改 parser、section builder、chunk assembler，
怎么快速知道有没有把老结果改坏
```

---

## 关键文件

### 1. 回归样本

目录：

[backend/tests/fixtures/splitter_regression/samples](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/fixtures/splitter_regression/samples)

当前先固化了 4 个首批样本：

- `markdown_handbook.md`
- `plain_text_policy.txt`
- `ocr_notice.txt`
- `csv_orders.csv`

这 4 个样本分别覆盖：

- Markdown 标题 / 列表 / 表格 / 代码块
- 普通 TXT 标题检测
- OCR 风格标题检测
- CSV 表格解析

这里先用文本类和表格类样本做第一批回归，是因为它们最稳定、最适合做精确快照。
现在又补进了 3 个二进制文档样本：

- `workbook_orders.xlsx`
- `word_handbook.docx`
- `pdf_policy.pdf`

它们放在：

[backend/tests/fixtures/splitter_regression/binary_samples](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/fixtures/splitter_regression/binary_samples)

这样当前 Phase 9 已经覆盖：

- Markdown
- TXT
- OCR 风格 TXT
- CSV
- Excel
- Word
- PDF

---

### 2. 快照生成与评估

文件：

[backend/app/services/document_splitter/evaluation.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/evaluation.py:1)

这个文件主要做两件事：

#### `build_splitter_regression_snapshot()`

把 splitter 全链路结果一次性跑出来，生成一个统一快照：

```text
text/file_type
  -> parse_splitter_source()
  -> normalize_splitter_source()
  -> build_document_sections()
  -> build_document_blocks()
  -> assemble_chunks()
  -> snapshot
```

最终快照里会固化 4 层输出：

- `elements`
- `sections`
- `blocks`
- `chunks`

所以以后你如果改了某一层逻辑，不需要靠肉眼猜，只要看快照 diff 就知道影响到哪一层了。

#### `evaluate_splitter_regression_snapshot()`

这个函数是在快照之上再做一层质量评估。

它现在输出的是一组轻量指标，例如：

- `element_count`
- `section_count`
- `block_count`
- `chunk_count`
- `avg_chunk_length`
- `max_chunk_length`
- `oversized_chunk_count`
- `suspicious_chunk_start_count`
- `noise_chunk_count`
- `table_fragment_chunk_count`
- `element_source_parser_coverage_ratio`
- `block_heading_path_coverage_ratio`
- `heading_prefix_applicable_chunk_count`
- `heading_prefix_ratio`
- `table_chunk_count`
- `table_header_retention_ratio`

这些指标不是业务 KPI，而是切分质量的工程指标。

---

### 3. 基线生成脚本

文件：

[backend/scripts/generate_splitter_regression_baselines.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/scripts/generate_splitter_regression_baselines.py:1)

这个脚本的作用很直接：

```text
读取样本
  -> 生成 snapshot
  -> 生成 metrics
  -> 写入 expected/
```

输出目录在：

[backend/tests/fixtures/splitter_regression/expected](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/fixtures/splitter_regression/expected)

这里每个样本都会生成两份文件：

- `xxx.snapshot.json`
- `xxx.metrics.json`

所以以后如果你明确做了“预期内的结构升级”，就可以重新跑这个脚本刷新基线。

如果你连样本文件本身也要一起重建，还可以先跑：

[backend/scripts/generate_splitter_binary_samples.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/scripts/generate_splitter_binary_samples.py:1)

它会重新生成：

- `workbook_orders.xlsx`
- `word_handbook.docx`
- `pdf_policy.pdf`

---

### 4. 回归测试

文件：

[backend/tests/test_splitter_regression.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_splitter_regression.py:1)

这里做了三层检查：

#### 第一层：快照对比

重新跑 splitter，和 `expected/*.snapshot.json` 做精确比对。

这层解决的是：

```text
elements / sections / blocks / chunks
有没有结构性回归
```

#### 第二层：指标对比

重新算一遍 metrics，和 `expected/*.metrics.json` 对比。

这层解决的是：

```text
整体质量画像有没有变
```

#### 第三层：质量门禁

现在先做了比较保守的门禁：

- `oversized_chunk_count == 0`
- `noise_chunk_count == 0`
- `table_fragment_chunk_count == 0`
- `element_source_parser_coverage_ratio == 1.0`
- `block_heading_path_coverage_ratio == 1.0`
- 适用标题前缀规则的样本必须保留标题前缀

也就是说，哪怕有人以后刷新了 snapshot，如果这些底线指标被打破，测试还是会拦住。

---

## 这些指标分别想防什么问题

### 1. `oversized_chunk_count`

防 chunk 超过 `max_chunk_size`。

这能防住：

- pack 逻辑失效
- table/code 特殊切分失效
- overlap 叠加后把 chunk 撑爆

### 2. `suspicious_chunk_start_count`

防 chunk 以明显异常的前缀开头，例如：

- 标点
- 半截英文 token

这不是绝对语义判断，只是一个轻量启发式检查。

它主要是为了尽早发现：

```text
overlap 或 fixed window
又把边界切坏了
```

### 3. `table_fragment_chunk_count`

防表格 chunk 从中间残缺开始。

现在的检查方式是：

- 如果这个 chunk 被识别成 table chunk
- 那它正文第一行就应该是表头
- 第二行应该是 Markdown table separator

如果不满足，就说明它更像是表格残片。

### 4. `noise_chunk_count`

防明显的噪声 chunk，例如：

- `Page 1`
- `Page 2`
- `1/8`
- `第 3 页`

它主要是为了拦住 PDF / OCR / layout 类文档里的页脚、页码、短噪声文本。

### 5. `element_source_parser_coverage_ratio`

检查 `DocumentElement.metadata["source_parser"]` 有没有丢。

这个字段很重要，因为后面调试时你需要知道：

```text
这段结构到底是谁解析出来的
```

### 6. `block_heading_path_coverage_ratio`

检查 block 层是不是都还带着 heading path。

因为 chunk 合并、检索追溯、标题前缀补充，后面都依赖这个字段。

### 7. `heading_prefix_ratio`

检查“本来就应该带标题上下文的 chunk”有没有补标题前缀。

现在它不只覆盖 Markdown，也覆盖：

- DOCX
- PDF
- Excel

配套的 `heading_prefix_applicable_chunk_count` 用来表示当前样本里到底有多少 chunk 适用这条规则，避免在“不适用的样本”上出现误导性的 `1.0`。

---

## 一个你后面会经常用到的操作

如果你后面改了 splitter，并且确定新结果就是你想要的，可以跑：

```bash
python3 backend/scripts/generate_splitter_regression_baselines.py
python3 -m unittest backend/tests/test_splitter_regression.py
```

第一条命令是刷新基线。
第二条命令是验证“当前输出”和“新基线”一致。

如果你还改了二进制样本本身，就先跑：

```bash
python3 backend/scripts/generate_splitter_binary_samples.py
python3 backend/scripts/generate_splitter_regression_baselines.py
python3 -m unittest backend/tests/test_splitter_regression.py
```

---

## 这阶段的价值

前面 0 到 8 阶段解决的是：

```text
怎么把结构切出来
```

Phase 9 解决的是：

```text
以后继续演进时，怎么稳
```

这是两件不同的事。

没有 Phase 9，后面每改一处 parser 都只能靠人工抽查。
有了 Phase 9，至少能先用自动化把最容易退化的结构问题拦住。
