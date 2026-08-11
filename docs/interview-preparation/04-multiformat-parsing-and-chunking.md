# 04 多格式解析与结构化切分

## 一、为什么不能统一按 1000 字硬切

固定窗口会切断标题、句子、表格行和代码块，导致：

- chunk 开头出现半句话；
- metadata 的标题与正文不匹配；
- 表头和数据行分离；
- 检索命中后缺少可理解上下文。

项目采用统一中间模型，把“文件格式差异”和“chunk 组装策略”分开。

## 二、统一 Pipeline

```text
Source File
  -> Parser
  -> DocumentElement[]
  -> Normalizer
  -> Section[]
  -> Block[]
  -> Chunk Assembler
  -> ChunkData[]
```

### 四层数据的区别

- `DocumentElement`：parser 观察到的原始结构元素，如 heading、paragraph、table；
- `Section`：带 heading path 的章节边界；
- `Block`：可用于组装的结构单元，保留类型和来源位置，不受 chunk size 直接约束；
- `ChunkData`：最终检索和 Embedding 单元，受 target/max size 与 overlap 规则约束。

## 三、各格式如何解析

### Markdown

识别 `#` 到 `######`、段落、列表、表格和 fenced code block。文档存在 `##` 时，一级标题主要作为 document title，二级标题作为主 section 边界，三级以下更新 heading path。

### TXT / OCR fallback

TXT 没有 Word 那样的 Heading 样式，也没有 PDF 的字体和坐标信息，因此 parser 只能从文本行和上下文推测结构：

```text
员工报销制度

一、适用范围

本制度适用于全体员工的差旅和日常费用报销。
```

程序会把 `一、适用范围` 作为标题候选，并综合以下信号打分：

```text
编号模式：1. / 一、/ （一）/ 第一章
行长度：标题通常较短，但只能作为辅助信号
空行关系：标题前后是否存在段落分隔
标点特征：标题通常没有完整句号
正文承接：标题后面是否有连续正文
```

只有检测到多个可靠候选时，才按标题切 Section；否则退回按空行切 paragraph，避免把普通短句误判成章节标题。OCR 文本还会经过空格归一化、编号容错和中文拆字清洗，例如将 `2 . 权限模型` 规范为更接近 `2. 权限模型`。

需要特别区分：当前项目支持的是“TXT、以及已经被外部 OCR 提取成文本后的 fallback 处理”，并没有在后端内置 Tesseract、PaddleOCR 等 OCR 引擎。因此，当前系统不能直接把纯图片扫描件自动识别成文字；真正的扫描 PDF OCR 需要后续接入独立 OCR 服务或 OCR worker。

### DOCX

DOCX 本质上是一个 ZIP 容器，内部有 `word/document.xml`、样式定义和编号定义。项目优先通过 `python-docx` 读取 Word 原生结构，而不是把所有内容先拼成纯文本再猜：

```text
DOCX
  -> python-docx
  -> paragraph / style / table
  -> 必要时读取底层 OOXML 属性
  -> DocumentElement
```

识别规则示例：

```text
paragraph.style.name = Heading 1/2/3
  -> heading element

paragraph.style.name = Normal
  -> paragraph element

paragraph 的 numPr
  -> list element

doc.tables -> rows -> cells
  -> table element
```

例如 Word 中：

```text
员工手册       Heading 1
第一段内容     Normal
• 报销材料     List Paragraph
```

parser 可以保留标题层级、列表层级和表格单元格，而不是只得到三行普通字符串。`numPr` 是 Word OOXML 中的编号/项目符号属性，用来辅助判断一个段落是否属于有序列表或无序列表。只有遇到特殊编号、样式缺失或复杂嵌套时，才需要进一步查看 OOXML。

### XLSX / CSV

XLSX 先按 sheet 处理，CSV 则视为一个没有 sheet 的表格。基本链路是：

```text
sheet
  -> used range
  -> 连续非空区域
  -> table region
  -> header candidate
  -> table element
```

表头检测不是“第一行必须有数字”。例如：

```text
部门 | 项目 | 负责人 | 备注
销售部 | 华东渠道拓展 | 李晨 | 含展会费用
交付中心 | 系统实施 | 周楠 | 含差旅费用
```

第一行没有数字，但仍然很像表头。程序会比较首行和后续数据行：

```text
首行是否相对完整，空单元格是否较少
首行是否是较短的字段名文本
后续行是否具有稳定的列数和重复结构
首行与后续行的类型模式是否发生变化
字段名是否包含部门、金额、状态、负责人等特征
```

另一个例子：

```text
第一行：部门 | 预算金额 | 负责人
第二行：销售部 | 180000 | 李晨
```

首行呈现“短文本字段名”模式，第二行呈现“文本 + 数字 + 文本”数据模式，因此首行的表头置信度较高。即使后续数据也是纯文本，也可以通过列数、重复性、空值和文本长度判断。

表格切 Chunk 时不会只保存数据行，还会重复表头：

```markdown
| 部门 | 项目 | 负责人 |
| --- | --- | --- |
| 销售部 | 华东拓展 | 李晨 |
```

metadata 同时记录 `sheet_name`、`sheet_index`、`header_row`、`row_start`、`row_end`、`col_start` 和 `col_end`，保证检索结果可以追溯回 Excel 原位置。

### PDF

PDF 不像 DOCX 那样天然保存“这是标题、这是段落”的语义。它更接近：

```text
在页面坐标 (x, y) 处绘制这些文字
```

因此 PDF parser 读取原 PDF 的文字对象、字体和坐标，而不是只处理已经抽出的裸文本。一个文字对象可能包含：

```text
text = "员工"
x0 = 72, y0 = 720, x1 = 100, y1 = 735
```

其中：

```text
x0/x1：文字在页面上的左右边界
y0/y1：文字在页面上的上下边界
```

主要恢复过程是：

```text
文字对象
  -> 根据 y 坐标聚合成视觉行
  -> 同一行内按 x 坐标排序
  -> 根据行间距合并成段落
  -> 根据字体、编号、空白和正文承接识别标题
  -> 根据页面位置去重页眉页脚
  -> 根据布局恢复单栏/双栏阅读顺序
  -> DocumentElement
```

#### 单栏示例

```text
行 1：x0=72, x1=520
行 2：x0=72, x1=500
行 3：x0=72, x1=510
```

每一行都横跨页面大部分宽度，更像单栏正文。程序按：

```text
行 1 -> 行 2 -> 行 3
```

恢复阅读顺序。

#### 双栏示例

```text
左栏：x0=72,  x1=250
左栏：x0=72,  x1=245

右栏：x0=320, x1=500
右栏：x0=320, x1=505
```

页面中间 `250 ~ 320` 长期没有文字覆盖，像一个 gutter。项目会对整页做横向 occupancy 统计：

```text
左边覆盖高 | 中间覆盖低 | 右边覆盖高
```

只有左、右两侧都有足够文字，并且中间长期低覆盖，才判定为双栏，避免把“单栏长行横跨页面中线”误判成双栏。

#### 标题、页眉和页脚

PDF 标题通常综合判断：

```text
字体大小和粗细
行长度
上下留白
编号模式
后续是否有正文承接
```

例如一行大号粗体的 `供应商准入流程`，下方紧跟普通字号正文，就比正文中的短句更像标题。若 `Page 1`、`Page 2` 在每页底部相同位置重复出现，则作为页脚噪声候选去重，而不是固化进正文。

#### 扫描 PDF 与 OCR 边界

扫描 PDF 可能只有图片，没有可读取的文字对象：

```text
扫描图片 PDF
  -> OCR 引擎识别文字和坐标
  -> 再进入 PDF 行、栏、标题和段落恢复
```

当前项目的 PDF layout parser 负责处理“已有文字对象和坐标”的 PDF，并处理 OCR 或外部抽取文本的部分清洗；尚未内置真正的 OCR 引擎。因此，图片型 PDF 需要后续接入 OCR 服务，不能把当前 parser 描述成完整 OCR 能力。

## 四、Chunk 组装规则

优先级：

```text
标题/section
  -> paragraph/list/table/code block
  -> sentence
  -> fixed window 最后兜底
```

核心规则：

1. 默认不跨主 section pack；
2. `table <-> non-table`、`code <-> non-code` 边界默认 flush；
3. 多个小 paragraph/list 可以拼到接近 target size；
4. 单个超长段落先按句子切；
5. 单句仍超长才固定窗口；
6. 表格按行切并重复表头，代码按行切；
7. chunk 内容补 heading prefix；
8. overlap 只用于同一个原始 Block 被拆成多个子 Block 的情况；独立 Block 因 packing 结束时默认不复制前一块内容。

`target_chunk_size=850`、`max_chunk_size=1000` 当前按字符近似，不是 token。目标值是组装方向，不要求每个 chunk 都接近 850：完整短 section 或表格可以更短。

## 五、什么时候使用 overlap

overlap 不是每个 Chunk 都自动添加。它主要解决“同一个连续内容被迫拆开”时的上下文断裂：

```text
同一个超长 paragraph Block
  -> 句子 Block 1、句子 Block 2、句子 Block 3
  -> 长度切分成 Chunk 0、Chunk 1
  -> Chunk 1 开头复制 Chunk 0 尾部的完整句子
```

例如：

```text
Chunk 0：员工申请出差后需要提交预算。审批通过后才能订票。
Chunk 1：审批通过后才能订票。出差结束后需要上传发票。
```

下面这些情况默认不做跨 Block overlap：

```text
独立 paragraph Block 1 -> 独立 paragraph Block 2
不同 Section
paragraph <-> table
table <-> code
code <-> paragraph
```

原因是这些内容已经有清晰结构边界。若仅仅因为当前 Chunk 达到 target，就把完整的 Block 1 复制到 Block 2 所在的 Chunk，会增加重复、浪费检索上下文，而且没有真正补回被切断的语义。

不同类型的超大 Block 使用各自的完整结构单元做 overlap：

```text
paragraph -> 完整句子
list      -> 完整列表项
table     -> 完整数据行，并保留表头
code      -> 完整代码行
fixed window -> 对齐后的字符窗口
```

`chunk_overlap=200` 是语义 overlap 的长度预算，不代表一定硬复制 200 个字符。算法会优先选择完整语义单元；如果没有合适的完整单元，则可以不产生 overlap。第一个 Chunk 没有前置 overlap，只有发生同源内容切分时，后一个 Chunk 才会复用前一个 Chunk 尾部的一部分内容。

## 六、Metadata 要回答什么

一个 chunk 至少需要可追溯到：

```text
organization_id
knowledge_base_id
document_id / knowledge_item_id
file_type / filename
heading_path
page / sheet / row / paragraph range
block_type
chunk_index
splitter strategy
```

metadata 不是越大越好。需要过滤和查询的权限字段应是 ES 显式 mapping；只用于展示的来源细节可以放 metadata。

## 七、*切分质量如何评估*

回归快照固定四层输出：elements、sections、blocks、chunks。修改 parser 后可以看 diff 是在哪一层发生变化。

质量指标包括：

- oversized chunk 数；
- 可疑 chunk 开头；
- 表格残片；
- heading prefix 覆盖；
- heading path 覆盖；
- 表头保留率；
- parser 来源信息覆盖。

这些指标不能完全证明语义质量，还需要多格式人工样本和下游检索评估共同验证。

## 八、常见追问

### chunk 越小越好吗？

不是。太小会丢失完整语义并增加向量数；太大会混入多个主题，降低检索精度并浪费 LLM token。应通过真实问答集调整，而不是相信固定行业数字。

### 为什么 Chunk 必须关联 KnowledgeItem（*这个是什么*）？

KnowledgeItem 是可审核、可编辑、带状态的知识管理单元；Chunk 是检索派生数据。统一关联后，手工知识和文档知识都能通过条目治理，并可重建 chunk。

### PDF 双栏如何判断？

先按 y 坐标聚合视觉行，再统计页面横向区间的文字 occupancy。若左、右区域长期有文字而中间 gutter 长期低占用，才判双栏；单栏长行横跨中线，不应被误判。

## 九、关键代码

- [统一模型](../../backend/app/services/document_splitter/models.py)
- [Parser 接口](../../backend/app/services/document_splitter/interfaces.py)
- [Pipeline](../../backend/app/services/document_splitter/splitter.py)
- [Section Builder](../../backend/app/services/document_splitter/section_builder.py)
- [Chunk Assembler](../../backend/app/services/document_splitter/chunk_assembler.py)
- [各格式 Parser](../../backend/app/services/document_splitter/parsers)
- [回归评估](../../backend/app/services/document_splitter/evaluation.py)
- [切分框架阶段讲解入口](../splitter-explainer/phase-0.md)
