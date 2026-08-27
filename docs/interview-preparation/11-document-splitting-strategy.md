# 11 文档解析与结构化切片面试准备

## 一、30 秒回答

我的切分策略不是把所有文件直接按固定字符数切开，而是先根据文件格式解析出统一的 `DocumentElement`。Markdown、Word、PDF 会尽量识别标题、段落、表格、列表和代码；Excel/CSV 主要按 sheet、行、列和表格区域处理。随后在同一个 `Section` 内把结构元素组装成检索用的 `ChunkData`，通过 `target size` 控制期望长度，通过 `max size` 控制硬上限。只有单个元素过大时，才按对应结构继续细分：文本按完整句子，表格按完整行并复用表头，代码按完整代码行。对同一个原始元素被拆成多个片段的情况，设置约 20% 的语义 overlap；天然属于不同章节的内容不跨 `Section` 拼接，避免破坏语义边界。

## 二、项目中的真实链路

```text
原始文件
  -> 对应格式 Parser
  -> DocumentElement[]
  -> 补充 section_id / heading_path
  -> Chunk Assembler 内部组装记录
  -> ChunkData[]
  -> PostgreSQL 保存内容和元数据
  -> Embedding + Elasticsearch 建立检索索引
```

面试时需要说明一个实现边界：项目当前主链路已经简化为
`DocumentElement -> section context -> ChunkData`。`Section` 和 `Block` 仍用于回归快照、调试和内部组装语义，但不是每个 Parser 都必须显式创建的独立业务表，也不是必须经过的四个数据库实体。这样可以保留结构化切分能力，又避免为了概念完整而增加不必要的对象层。

## 三、四个概念怎么区分

### 1. DocumentElement

`DocumentElement` 是 Parser 从原文件中观察到的最小结构元素，重点是“原文件里看到了什么”。例如：

```text
heading     "差旅报销制度"
paragraph   "员工出差前需要提交申请。"
list_item   "出差申请单"
table       "费用类型 | 报销上限 | 证明材料"
code        "def calculate_total(...):"
```

它通常带有 `element_type`、原文、顺序、页码或行列位置等来源信息。不同格式的 Parser 输出不同，但最后都转换为这套统一表示，后面的组装逻辑就不需要知道原始文件是 PDF 还是 DOCX。

### 2. Section

`Section` 是语义章节边界，核心是 `section_id` 和 `heading_path`。Markdown 的 `## 报销范围`、Word 的 Heading 1/2、PDF 中识别出的章节标题，都可以形成 section 边界。

Excel/CSV 通常没有文章式标题，因此 section 可以按 sheet 或连续的表格区域理解：

```text
Sheet: 2026预算
  -> 一个或多个连续 table region

Sheet: 供应商名单
  -> 另一个 section/table region
```

Section 的重要规则是：不同 section 不互相拼接。这样“采购流程”章节末尾不会和“安全事件”章节开头被拼成同一个 chunk。

### 3. Block

`Block` 是适合组装的结构单元，可以理解为“在 Section 内参与拼接的一块内容”。它通常对应一个段落、列表组、表格区域、代码块或被进一步切出的子块，并保留 `block_type`、来源位置和所属 section。

一个 Block 不一定等于一个 Chunk：

```text
短段落 Block       -> 可能和相邻 Block 组成一个 Chunk
超长段落 Block     -> 先按句子拆成多个子 Block，再组成多个 Chunk
表格 Block         -> 按完整数据行拆成多个子 Block
代码 Block         -> 按完整代码行拆成多个子 Block
```

因此 Block 更偏向结构和组装语义，Chunk 更偏向最终检索和 Embedding 的边界。

### 4. ChunkData

`ChunkData` 是最终写入知识条目、生成 embedding、写入 Elasticsearch 的检索单元。它会携带：

```text
content
chunk_index
section_id / heading_path
block_type
splitter_strategy
source_position
overlap 信息（如果确实发生了同源拆分）
```

它的长度受 `target_chunk_size` 和 `max_chunk_size` 约束。`target` 是希望靠近的大小，`max` 是不能突破的硬上限。

## 四、不同格式怎样解析

### Markdown

根据 Markdown 语法识别：

```text
# / ## / ### 标题
普通段落
- 列表项或 1. 有序列表
| 表格 |
```python
代码块
```
```

标题更新当前 `heading_path`，段落、列表、表格和代码块变成不同类型的 `DocumentElement`。标题本身通常作为上下文前缀进入后续 chunk，而不是单独成为没有正文的检索片段。

### DOCX

DOCX 本质上是 ZIP 容器，里面包含 OOXML 文件，但项目优先读取 Word 的原生结构：

```text
python-docx
  -> paragraph / style / table
  -> 必要时读取 OOXML 的编号或样式属性
  -> DocumentElement
```

常见判断方式是：

```text
Heading 1/2/3       -> heading
Normal              -> paragraph
List Paragraph/numPr -> list_item
doc.tables          -> table
```

这样可以保留标题层级、列表和表格，不必把整个 Word 文档先拼成一段纯文本再猜结构。OOXML 主要是特殊样式、编号或嵌套结构缺失时的补充来源。

### PDF

PDF 更接近“在页面坐标上绘制文字”，通常不天然保存标题和段落语义。Parser 读取文字对象及其坐标，例如：

```text
text = "员工"
x0 = 72, y0 = 720, x1 = 100, y1 = 735
```

主要步骤是：

```text
文字对象
  -> 根据 y 坐标聚合成视觉行
  -> 同一行按 x 坐标排序
  -> 根据行距合并成段落
  -> 结合字体、编号、空白和上下文识别标题
  -> 识别页眉页脚并避免重复
  -> 根据横向区域恢复单栏或双栏阅读顺序
  -> DocumentElement
```

双栏页面可以表现为：左栏文字的 `x0/x1` 大致在 `72~250`，右栏文字大致在 `320~500`。中间长期没有文字的横向空白就是 gutter。Parser 会先在栏内按阅读顺序组织，再从左栏切到右栏，避免把左栏第一段和右栏第一段错误交错。

当前项目支持的是有文本层的 PDF。扫描 PDF 如果只有图片，还需要单独的 OCR 服务或 OCR worker；不能因为文件扩展名是 `.pdf` 就认为后端已经完成 OCR。

### XLSX / CSV

Excel 按 sheet 处理，CSV 可以看作一个没有 sheet 的表格。基本过程是：

```text
sheet / CSV
  -> 找到 used range
  -> 划分连续表格区域
  -> 判断表头候选
  -> 识别数据行和列范围
  -> table DocumentElement
```

表头不是通过“第一行必须包含数字”判断的，而是综合观察：

```text
首行是否是较短的字段名文本
首行空值是否较少
后续行是否保持稳定列数
首行和后续行的类型模式是否不同
字段名是否具有业务列特征
```

例如：

```text
部门 | 预算金额 | 负责人
销售部 | 180000 | 李晨
```

第一行没有数字，但“预算金额”是字段名，后续行出现数字和具体人员，因此仍然可以判断为表头。生成表格 chunk 时尽量重复表头，并保存 sheet、行范围和列范围，让检索结果可回到 Excel 原位置。

### TXT

TXT 没有标题样式和坐标信息，只能依赖文本行和上下文信号推测结构：

```text
编号模式：一、 / 1. / 第一章
前后空行
行长度
末尾标点
标题后是否紧跟连续正文
```

如果标题候选不可靠，就退回按空行组织普通段落，避免把任意短句误判成章节标题。

## 五、Chunk 是怎样组装出来的

假设配置为：

```text
target_chunk_size = 900
max_chunk_size = 1000
```

一个 Section 中有三个 Block：

```text
Block A：员工出差前需要提交申请。                 260 字符
Block B：回来后需要提交发票，财务审核后付款。       310 字符
Block C：审批完成后归档，并保留相关凭证。           420 字符
```

组装过程可以是：

```text
加入 A：260
加入 B：570
尝试加入 C：990，未超过 max，可组成一个 Chunk
```

如果当前已有 700，再加入下一个 350 字符的 Block 会变成 1050：

```text
当前 Chunk = 700
下一个 Block = 350
700 + 350 > max_chunk_size
  -> 输出当前 Chunk
  -> 下一个 Block 开始新的 Chunk
```

`target` 用来让 chunk 不要过碎，`max` 用来保证不会无限变大。通常不会为了凑到 target 而跨越 Section，也不会把一个表格行或一个代码结构从中间截断。

## 六、单个 Element 过大时怎样处理

如果一个 Element 自身就超过 `max_chunk_size`，不能把它当作普通 Block 原样放入，需要先按类型拆成更小的子 Block：

```text
超长段落 -> 尽量按句子切
超长列表 -> 尽量保持一个列表项完整
超长表格 -> 按完整数据行切，每片带表头
超长代码 -> 按完整代码行切，尽量保留函数名或代码块上下文
仍然过长 -> 最后才使用固定长度兜底
```

固定长度只是最后的防线，不是默认策略。因为固定长度可能切断句子、表格行或代码行，降低下游检索的可读性。

## 七、Overlap 什么时候使用

Overlap 不是每个 chunk 都默认复制前一个 chunk 的内容，也不是因为“加入下一个自然 Block 超过 max”就一定触发。项目中的原则是：

```text
自然边界下的多个段落/Block
  -> 直接分到相邻 Chunk，不强行复制

同一个原始 Element/Block 太大，被拆成多个子片段
  -> 这些同源子片段可以使用约 20% overlap
```

例如同一个超长段落被拆成两片：

```text
Chunk 1：员工出差前需要申请。回来后需要提交发票。财务复核金额。
Chunk 2：财务复核金额。审批完成后归档。相关凭证需要保存。
```

这里“财务复核金额”是 Chunk 1 末尾的完整语义单元，也被放到 Chunk 2 开头，降低句子刚好落在边界时的检索损失。

相反，如果：

```text
Block A：员工出差前需要申请。
Block B：回来后需要提交发票。
```

只是两个自然相邻、长度正常的 Block 因容量限制进入两个 Chunk，通常不复制 Block A。因为它们已经是结构上独立的单元，复制会增加重复内容和 embedding 噪声。

因此面试时可以明确说：

> overlap 服务于“同源超长内容的边界连续性”，不是所有 chunk 的固定模板；优先复制完整句子、完整表格行或完整代码行，无法保留结构时才按固定窗口兜底。

## 八、元信息为什么重要

切片不仅要保存正文，还要保存来源信息：

```text
block_type
  表示 paragraph / table / list / code 等结构类型

chunk_index
  表示同一 document 内的 chunk 顺序

splitter_strategy
  表示 normal、sentence、table_row、code_line 等切法

section_id / heading_path
  表示它属于哪个章节

source_position
  表示页码、段落序号、sheet、行列范围等来源位置
```

这些字段用于三件事：

1. 检索命中后给模型提供标题、页码和结构上下文。
2. 用户点击引用时定位到原始文件。
3. Parser 或 assembler 修改后，通过快照和指标判断变化发生在哪一层。

## 九、如何证明切分不是“看起来能用”

项目用多层回归快照固定：

```text
elements -> sections -> blocks -> chunks
```

修改 Parser 后，先看结构 diff，再看最终 chunk diff。基础指标包括：

```text
oversized chunk 数量
可疑 chunk 开头数量
表格残片数量
heading_path 覆盖率
表头保留率
parser source metadata 覆盖率
```

还要结合下游检索指标：Recall@K、MRR、nDCG、引用正确率和无答案拒答率。切片快照通过，只能说明结构稳定，不能单独证明问答效果一定提升。

## 十、常见面试追问

### 问：为什么不直接固定 1000 字切？

固定切分简单，但容易切断标题、句子、表格行和代码结构。结构化切分先尊重语义边界，再使用长度限制兜底，通常更适合企业文档。

### 问：一个 Block 可以生成多个 Chunk 吗？

可以。普通 Block 通常参与拼接；如果一个段落、表格或代码块超过硬上限，就先按句子、行等结构拆成子 Block，再生成多个 Chunk。

### 问：为什么表格 chunk 要重复表头？

只存数据行会失去列含义。把表头和数据行放在一起，模型和 BM25 都更容易理解“180000”对应的是预算金额，而不是普通数字。

### 问：PDF 双栏怎么处理？

读取文字对象的坐标，按 y 聚合行、按 x 排序，并通过左右栏区域和中间 gutter 判断阅读顺序。扫描 PDF 只有图片时还需要 OCR，不能只靠普通文字 Parser。

### 问：怎么避免 overlap 造成重复太多？

不对所有 chunk 默认复制，只对同一个超长 Element 拆出的连续片段启用；优先复制完整语义单元，并通过重复率、检索去重和评测集观察实际影响。

### 问：切片质量最终看什么？

先看 elements/sections/blocks/chunks 的结构快照和异常指标，再看 Recall@K、MRR、nDCG、引用准确率以及用户是否能从命中片段理解完整问题。两类指标要一起看。
