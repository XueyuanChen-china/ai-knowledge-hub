# 多格式文档切分执行路线图

## 目标

把当前以 `text_splitter.py` 为中心的切分能力，逐步升级成一套支持多格式的统一文档切分框架。

核心原则：

```text
基础设施 -> 低风险格式 -> 中风险格式 -> 高风险格式
```

而不是按文件格式一个个零散堆逻辑。

---

## 总体设计

统一主流程：

```text
Parser
  -> DocumentElement[]
  -> Normalizer
  -> Section Builder
  -> Block Builder
  -> Chunk Assembler
  -> ChunkData[]
```

统一目标：

- 文档型文件：Markdown / TXT / Word / PDF
- 表格型文件：CSV / Excel
- 失败时统一 fallback
- metadata 全程可追溯

---

## 分阶段计划

### Phase 0：统一模型和接口

目标：

- 固化 `DocumentElement / Section / Block / ChunkData`
- 定义 parser / normalizer / section builder / chunk assembler 接口
- 先把当前 `text_splitter.py` 里的核心结构抽出来

产出：

```text
backend/app/services/document_splitter/models.py
backend/app/services/document_splitter/interfaces.py
backend/app/services/document_splitter/metadata.py
```

当前阶段说明：

- 这一阶段不追求多格式完整能力
- 先建立稳定骨架，避免后面继续把逻辑塞进单文件
- 现有 `text_splitter.py` 保持可运行，逐步迁移

验收：

- 现有 `text_splitter.py` 改为复用统一模型
- 现有测试继续通过

### Phase 1：统一 splitter pipeline

目标：

- 抽出统一主流程：
  - `parse`
  - `normalize`
  - `build_sections`
  - `build_blocks`
  - `assemble_chunks`
- 让 Markdown / TXT 先接到新框架

产出：

```text
backend/app/services/document_splitter/splitter.py
backend/app/services/document_splitter/normalizer.py
backend/app/services/document_splitter/section_builder.py
backend/app/services/document_splitter/chunk_assembler.py
```

### Phase 2：迁移现有 Markdown / TXT

目标：

- 把现有 Markdown / TXT 逻辑迁到 `DocumentElement` 体系
- 保持当前已实现的 section / block / chunk 规则不退化

重点：

- Markdown 的 `# / ## / ###` 主边界规则
- plain text 标题检测 fallback

当前阶段说明：

- `parse_splitter_source` 已经会把 Markdown / TXT 解析成 `DocumentElement[]`
- `section_builder` 已经支持从 `DocumentElement` 构建 `Section / Block`
- 兼容层 `text_splitter.py` 继续对外保留原调用方式

### Phase 3：增强 plain text fallback

目标：

- 让 TXT / OCR / PDF 文本 fallback 共用一套标题检测能力
- 增加标题检测置信度
- 增加噪声文本容错

当前阶段说明：

- `plain_text_parser` 已加入标题候选评分与正文承接过滤
- OCR 风格空格标题会先做轻量归一化
- `split_pdf_sections` 会先尝试共用 plain text 标题检测，失败再退回按页 fallback

### Phase 4：CSV parser

目标：

- 建立第一版表格型文件 parser
- 支持 header 检测、row group chunk、header 保留、行范围 metadata

当前阶段说明：

- `csv_parser` 已经会把 CSV 文本解析成 `table DocumentElement`
- 会检测 header，并转换成 Markdown table 形式供现有 table splitter 复用
- table chunk 会保留 header，并补上 `row_start / row_end / row_count` metadata

### Phase 5：Excel parser 基础版

目标：

- 支持多 sheet、used range、基础 table region 检测
- 支持 row-wise table chunk

当前阶段说明：

- `excel_parser` 已支持 `.xlsx` 工作簿解析
- 会按 sheet 检测 used range，再按空行拆基础 table region
- 每个 chunk 会保留 `sheet_name / row_start / row_end / col_start / col_end`

### Phase 6：DOCX parser 基础版

目标：

- 优先使用 Word 原生结构
- 支持 heading / paragraph / list / table

当前阶段说明：

- `docx_parser` 已支持按 Word 原生顺序读取 paragraph / table
- heading / list / table 会保留结构类型，而不是直接降成纯文本
- `.docx` 上传与切片链路已经接入

### Phase 7：PDF text fallback 版

目标：

- 先解决“一页一个 section 太粗”的问题
- 支持标题检测、跨页 section、无标题按页 fallback

当前阶段说明：

- PDF text fallback 现在只要检测到可靠标题，就会按标题组织 section
- 单个标题也可以把后续多页正文组织成跨页 section
- 完全无标题时，仍然安全退回按页 fallback

### Phase 8：PDF layout 增强版

目标：

- 引入 layout 信息
- 支持 bbox、reading order、多栏、页眉页脚去重、表格抽取

当前阶段说明：

- PDF 有文件路径时，优先走 `pdfplumber` layout parser
- 会抽取 word-level bbox，做基础双栏 reading order、重复页眉页脚去重、表格检测
- layout parser 失败时，仍然退回原来的 text fallback

### Phase 9：回归测试与评估

目标：

- 固化样本集
- 固化 `elements / sections / blocks / chunks` 输出
- 建立质量指标

当前阶段说明：

- 已新增首批回归样本集，放在 `backend/tests/fixtures/splitter_regression/samples`
- 已新增二进制回归样本集，放在 `backend/tests/fixtures/splitter_regression/binary_samples`
- 已新增快照生成与质量评估模块：`evaluation.py`
- 已新增基线生成脚本，会输出 `snapshot.json / metrics.json`
- 已新增二进制样本生成脚本，用于稳定重建 `pdf/docx/xlsx` fixtures
- 已新增回归测试，覆盖快照比对、指标比对和基础质量门禁

---

## 为什么这样排

原因：

- `DocumentElement` 是后续所有 parser 的统一契约，应该最先做
- Markdown / TXT 已有实现，迁移成本最低，适合作为新架构第一批接入对象
- CSV / Excel 比 PDF layout 简单，收益高，适合先做稳定
- DOCX 原生结构清晰，优先级高于 PDF layout
- PDF layout 最复杂，应该后置

---

## Phase 0 当前落地项

本轮先做：

1. 新增统一模型层
2. 新增统一接口层
3. 新增 metadata 工具层
4. 让现有 `text_splitter.py` 复用统一模型

本轮不做：

- 新 parser 的完整实现
- section builder / chunk assembler 的彻底拆分
- Word / PDF / CSV / Excel 接入

---

## 备注

当前设计里有两个约束需要长期保持：

1. Markdown 的主 section 边界规则独立于通用 heading 自动判断规则
2. 表格型文件不能退化成“纯字符串按长度切”，必须保留 row / header 语义
