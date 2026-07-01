# 切分讲解：Phase 4

## Phase 4 做了什么

这一阶段先把第一版 CSV parser 接进统一切分框架。

这次没有新造一套表格 chunk pipeline，而是复用了你现在已经稳定的：

```text
DocumentElement
  -> Section
  -> Block
  -> table splitter
  -> Chunk
```

所以 Phase 4 的重点是三件事：

1. 把 CSV 文本解析成稳定的 `table DocumentElement`
2. 检测 header，并在 chunk 时重复保留
3. 给每个 table chunk 补上行范围 metadata

---

## 关键文件

### 1. CSV parser

文件：

[backend/app/services/document_splitter/parsers/csv_parser.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/parsers/csv_parser.py:1)

这个文件的主链路是：

```text
CSV text
  -> detect_csv_dialect()
  -> read_csv_rows()
  -> split_csv_table_regions()
  -> detect_csv_header()
  -> format_csv_region_as_markdown_table()
  -> table DocumentElement[]
```

也就是说，它先把 CSV 读成结构化行数据，再决定：

- 分隔符是什么
- 有没有 header
- 这一段是不是 table region

最后再转成统一的 `DocumentElement`。

---

### 2. 为什么先转成 Markdown table

当前 parser 没有直接把 CSV 变成专用的 `row_group` block，而是先转成 Markdown table 文本。

原因很实际：

```text
现有 chunk_assembler 已经有成熟的 table split 逻辑
```

它已经支持：

- 按行切表格
- 尽量保表头
- 超长行再拆

所以 Phase 4 先复用这套能力，成本更低，也更稳。

你可以把它理解成：

```text
CSV parser 负责把“表结构”说清楚
chunk_assembler 负责把“表怎么分块”处理好
```

---

### 3. header 检测怎么做

`detect_csv_header()` 现在是两层：

1. 先尝试 `csv.Sniffer().has_header()`
2. 如果 sniff 不稳定，再走本地 heuristic

fallback heuristic 主要看：

- 第一行是不是更像字段名
- 第二行是不是更像数据
- 第一行和第二行是否明显不同

这不是完美判断，但对第一版 CSV parser 足够保守。

---

### 4. row group chunk 是怎么形成的

真正的 row-group 发生在：

[backend/app/services/document_splitter/chunk_assembler.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/chunk_assembler.py:238)

这里的 `split_table_block()` 现在会：

1. 识别 header lines
2. 把数据行按 `max_chunk_size` 聚合
3. 每次生成一个 table chunk block
4. 给这个 block 补上：
   - `row_start`
   - `row_end`
   - `row_count`
   - `header_retained`

所以现在所谓的 `row group chunk`，本质上就是：

```text
若干连续数据行
+ 重复保留的 header
+ 对应的行范围 metadata
```

---

## 一个小例子

输入：

```csv
id,name,score
1,Alice,95
2,Bob,88
3,Charlie,91
4,Denise,86
```

parser 会先产出一个 `table DocumentElement`，内容大致变成：

```text
| id | name | score |
| --- | --- | --- |
| 1 | Alice | 95 |
| 2 | Bob | 88 |
| 3 | Charlie | 91 |
| 4 | Denise | 86 |
```

然后如果长度超限，`split_table_block()` 会把它切成多个 chunk。

例如：

```text
chunk 1:
| id | name | score |
| --- | --- | --- |
| 1 | Alice | 95 |
| 2 | Bob | 88 |

metadata:
row_start = 2
row_end = 3

chunk 2:
| id | name | score |
| --- | --- | --- |
| 3 | Charlie | 91 |
| 4 | Denise | 86 |

metadata:
row_start = 4
row_end = 5
```

这样后面做检索和回溯时，会更清楚：

- 这一块来自哪几行
- 这一块是不是保留了 header

---

## 当前边界

这一阶段已经完成：

- CSV 分隔符检测
- header 检测
- 空行分隔的 table region
- header 保留
- row range metadata

还没做的是：

- 多 sheet Excel
- 单元格类型更细的语义判断
- merged cells
- 表头多行识别
- 更强的 schema / column type 推断

这些会在后面的 Excel / 高级表格阶段继续做。

---

## 一句话理解

Phase 4 的本质是：

```text
先把 CSV 当成“结构化表格文本”稳定接入，
让它在现有 table chunk 体系里可靠地产生带 header 和行范围的 chunks。
```
