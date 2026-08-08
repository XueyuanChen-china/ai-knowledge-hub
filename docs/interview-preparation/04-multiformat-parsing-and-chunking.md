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

没有显式样式，需要根据行长度、编号模式、标点、上下空行和后续正文等信号给标题候选打分。可靠标题不足时退回按空行组织普通段落。

### DOCX

`python-docx` 已经从 OOXML 解析出 paragraph、style、table 等对象：

- `Heading 1/2/...` 判断标题；
- paragraph 的 `numPr` 表示 Word 编号或项目符号属性；
- `doc.tables -> rows -> cells` 保留表格结构。

它不是只读取纯文本后再猜格式，而是优先利用 Word 原生结构。

### XLSX / CSV

按 sheet 和连续 used range 构建 table region。表头检测不是“必须出现数字”，而是比较首行与后续行的类型模式、重复性、空值和文本特征。每个表格 chunk 都重复带表头，并记录 sheet、行范围和列范围。

### PDF

PDF parser 读取原 PDF 的文字对象和坐标，而不是只处理已经抽出的裸文本。通过 `x0/y0/x1/y1` 恢复行、阅读顺序、页眉页脚和栏布局；必要时使用横向 occupancy 识别中间长期空白的 gutter。

PDF 最难，因为格式描述的是“字画在页面哪个坐标”，并不天然包含语义段落。扫描 PDF 还需要 OCR，这属于另一层能力。

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
8. overlap 使用上一 chunk 的完整句子或段落，不硬截最后 200 字符。

`target_chunk_size=850`、`max_chunk_size=1000` 当前按字符近似，不是 token。目标值是组装方向，不要求每个 chunk 都接近 850：完整短 section 或表格可以更短。

## 五、为什么 overlap 仍然有大小参数

语义 overlap 需要一个预算上限。系统先选择完整语义单元，再确保其总长度不要显著超过 overlap 预算。参数控制“最多带多少上下文”，算法控制“不从半句话开始”。

短 chunk 或 section 边界不一定需要 overlap。否则会制造大量重复，降低检索多样性和浪费上下文预算。

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

## 七、切分质量如何评估

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

### 为什么 Chunk 必须关联 KnowledgeItem？

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
