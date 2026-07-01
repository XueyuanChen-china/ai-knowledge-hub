# 切分讲解：Phase 5

## Phase 5 做了什么

这一阶段把第一版 Excel parser 接进了统一切分框架。

目标是先解决最基础、最常见的 Excel 文档场景：

1. 一个工作簿里有多个 sheet
2. 每个 sheet 只取真正有内容的 used range
3. 用空行拆出基础 table region
4. 继续复用现有 table chunk 逻辑，按行切分

---

## 关键文件

### 1. Excel parser

文件：

[backend/app/services/document_splitter/parsers/excel_parser.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/parsers/excel_parser.py:1)

当前主链路是：

```text
workbook path
  -> load_workbook()
  -> detect_used_range()
  -> extract_sheet_rows()
  -> split_sheet_table_regions()
  -> detect_excel_header()
  -> format_excel_region_as_markdown_table()
  -> table DocumentElement[]
```

它的职责很明确：

- 负责把 Excel 读成结构化表格
- 负责识别 sheet 和 table region
- 不直接做 chunk 拼接

---

### 2. 为什么输入用 workbook path

Excel 跟 Markdown / TXT 不一样。

对 Excel 来说，真正重要的信息不是纯文本，而是：

- sheet 边界
- 行列位置
- used range
- 空行分区

所以这一阶段不是只传 `text`，而是给 `split_document_text()` 增加了：

```text
spreadsheet_path
```

这样 parser 才能直接读取真实工作簿结构。

对应入口在：

[backend/app/services/document_splitter/splitter.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/splitter.py:42)

---

### 3. used range 怎么做

`detect_used_range()` 会扫描 sheet 里非空单元格，找出：

- 最小行
- 最大行
- 最小列
- 最大列

然后只在这个范围内抽数据。

这一步的价值是：

```text
把 Excel 里外围的大块空白裁掉
```

否则很多表格明明只有 `B2:C20` 有内容，却会被错误当成从 `A1` 开始的大矩形。

---

### 4. table region 怎么做

当前是第一版保守规则：

```text
used range 内，遇到整行空白就断开
```

也就是说，一个 sheet 里如果有：

- 上面一张表
- 中间一行空白
- 下面另一张表

就会拆成两个 table region。

这一步还没有做“按空列拆横向表格”，但对第一版已经够用了。

---

### 5. row-wise table chunk 怎么落地

这一步没有额外造 Excel 专用 chunk assembler。

Excel parser 会先把表格转成 Markdown table 文本，然后继续复用现有：

[backend/app/services/document_splitter/chunk_assembler.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/chunk_assembler.py:238)

这里的 `split_table_block()` 已经支持：

- 按行分块
- 保留 header
- 超长行再拆
- 给块补 `row_start / row_end / row_count`

所以 Excel 的 row-wise chunk，本质上还是：

```text
header + 连续数据行 + 行范围 metadata
```

---

## 现在每个 Excel chunk 会带什么

第一版已经会带这些关键信息：

- `sheet_name`
- `sheet_index`
- `sheet_used_range`
- `has_header`
- `header_row`
- `row_start`
- `row_end`
- `row_count`
- `col_start`
- `col_end`

这样后面做检索、定位、回显时，至少已经能回答：

```text
这块内容来自哪个 sheet
来自哪些行
来自哪些列
```

---

## 一个小例子

假设 workbook 里有两个 sheet：

### Sheet 1: `Sales`

```text
id | customer | amount
1  | Alice    | 95
2  | Bob      | 88
3  | Charlie  | 91
```

### Sheet 2: `Inventory`

```text
sku | name     | stock
K01 | Keyboard | 30
M02 | Mouse    | 50
```

parser 会先得到两个 sheet 下的 `table DocumentElement`。

如果 `Sales` 太长，切 chunk 后可能变成：

```text
chunk 1:
sheet_name = Sales
row_start = 2
row_end = 3

chunk 2:
sheet_name = Sales
row_start = 4
row_end = 4
```

而 `Inventory` 会是另一组 chunk，不会和 `Sales` 混在一起。

---

## 上传接口也接上了什么

当前 `.xlsx` 已经接进文档上传与切片流程：

- 上传允许 `.xlsx`
- `documents.extracted_text` 会保存 workbook 的可读文本摘要
- 真正切 chunk 时，会把 `spreadsheet_path` 传给 splitter，走 Excel parser

对应文件：

[backend/app/api/document.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/document.py:1)

这意味着 Phase 5 不是只做了本地 parser，而是已经能接项目当前的上传链路。

---

## 当前边界

这一阶段已经完成：

- `.xlsx` 基础解析
- 多 sheet
- used range
- 空行拆基础 table region
- row-wise table chunk
- 行列范围 metadata

还没做的是：

- `.xls` 老格式
- 合并单元格语义
- 横向多表切分
- 多行表头识别
- 公式 / 批注 / 样式语义

这些留到更后面的高级表格阶段更合适。

---

## 一句话理解

Phase 5 的本质是：

```text
先把真实 Excel 工作簿按 sheet 和表格区域稳定读出来，
再让它进入现有的 table chunk 流程，
产出带 sheet/行列坐标的可追溯 chunks。
```
