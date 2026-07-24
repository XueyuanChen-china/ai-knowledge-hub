# U1：测试基线与回归测试

这份文档解释企业化改造 U1 做了什么、为什么先做，以及相关代码应该怎么读。后续 U2-U10 会在同一个目录下分别创建自己的阶段学习文档。

## 一、为什么先做测试基线

企业项目不能只说“功能已经实现”，还要能回答三个问题：

1. 改代码之前，原来的行为是什么？
2. 改代码之后，哪些行为发生了变化？
3. 这次变化是修复、升级，还是意外回归？

测试基线就是在结构性改造之前，把当前正确行为固定下来。它相当于项目的参照物，后面做 Alembic、权限、checkpoint 或检索改造时，才能判断旧功能有没有被破坏。

## 二、这次 U1 做了什么

### 1. 修复 PDF 视觉行拼接

PDF parser 先从 PDF 中抽取带坐标的文字行，再把视觉行拼成段落。英文 PDF 可能出现这种情况：

```text
上一行：Reviewers must check status.
下一行：Only active records can enter retrieval.
```

如果只根据“两个词之间是否都是字母”判断空格，上一行最后是句号时就可能得到：

```text
Reviewers must check status.Only active records can enter retrieval.
```

现在 `infer_pdf_line_separator()` 增加了英文标点到英文单词的空格规则，同时保留中文 PDF 的伪空格清理规则。

### 2. 增加针对性回归测试

测试位于：

```text
backend/tests/test_text_splitter_structure.py
```

新增测试直接构造两条 `PdfLayoutLine`，验证句号换行后必须保留空格。这种测试比只拿最终 chunk 做字符串比较更容易定位问题，因为它直接覆盖了 PDF 行拼接这个小规则。

### 3. 固定多格式 splitter 快照

回归脚本会把完整切分链路保存成 JSON：

```text
elements -> sections -> blocks -> chunks
```

快照生成脚本：

```text
backend/scripts/generate_splitter_regression_baselines.py
```

样例和 baseline：

```text
backend/tests/fixtures/splitter_regression/
```

覆盖 Markdown、TXT、CSV、DOCX、XLSX 和 PDF。快照不仅保存最终 chunks，还能定位是 parser、section、block 还是 chunk assembler 发生了变化。

### 4. 清理项目入口

根 README 现在优先说明产品目标、架构、启动方式、测试和工程边界；原来的 Day 学习文档继续保留在 `docs/`，但不再承担项目首页的主要叙事。

## 三、核心代码链路

```text
test_splitter_regression.py
  -> build_splitter_regression_snapshot()
      -> parse_splitter_source()
      -> normalize_splitter_source()
      -> build_document_sections()
      -> build_document_blocks()
      -> assemble_chunks()
  -> evaluate_splitter_regression_snapshot()
```

重点阅读顺序：

1. `backend/tests/test_splitter_regression.py`
   - 了解测试如何遍历 cases 和 expected baseline。
2. `backend/services/document_splitter/evaluation.py`
   - 了解 snapshot 和 metrics 如何生成。
3. `backend/app/services/document_splitter/parsers/pdf_layout_parser.py`
   - 重点看 `build_pdf_paragraph_text()` 和 `infer_pdf_line_separator()`。
4. `backend/app/services/document_splitter/splitter.py`
   - 了解统一 pipeline 如何连接 parser、section、block 和 chunk。
5. `backend/app/services/document_splitter/chunk_assembler.py`
   - 了解最终 chunk 如何组合、限制长度和保留 overlap。

## 四、回归快照和普通单元测试的区别

普通单元测试回答一个小问题，例如：

```text
英文句子跨 PDF 视觉行时，是否补空格？
```

回归快照回答一个更大的问题：

```text
这次修改是否改变了整篇 PDF 从 elements 到 chunks 的输出？
```

两者应该同时存在：

- 单元测试定位局部规则，失败时容易修。
- 快照测试保护完整输出，防止某个局部改动悄悄影响后续 section、block 或 chunk。
- metrics 测试保护质量指标，例如 chunk 数量、超长 chunk、表格残片和标题前缀覆盖率。

## 五、如何执行 U1 验证

后端全量测试：

```bash
cd backend
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

前端检查：

```bash
cd frontend
npm run lint
npm run build
```

如果 parser 行为是有意升级，先运行 baseline 生成脚本，再检查 Git diff 中是否只有预期文件和预期字段发生变化。不要为了让测试变绿而直接覆盖快照。

## 六、本次 U1 的实际结果

| 主线       | 要证明什么                                     | 证据位置                                      |
| ---------- | ---------------------------------------------- | --------------------------------------------- |
| 可信回答   | 文档解析、切分、检索、引用和人工审核能形成闭环 | splitter 回归、retrieval evaluation、Chat E2E |
| 安全边界   | 用户只能访问自己有权限的知识库和检索证据       | auth/RBAC、SQL/ES/OSS/Chat 负向测试           |
| 可重复交付 | 新环境可以启动、迁移、测试并运行完整链路       | README、Compose、CI、migration、运行手册      |

## 当前基线

更新时间：2026-07-23

| 检查项                | 当前结果        | 备注                                            |
| --------------------- | --------------- | ----------------------------------------------- |
| splitter 回归测试     | 已通过          | 覆盖 Markdown、TXT、CSV、DOCX、XLSX、PDF        |
| 后端全量测试          | 通过：131 tests | PostgreSQL 测试环境；有依赖库 warning，但无失败 |
| 前端 lint             | 通过            | ESLint 无 error、无 warning                     |
| 前端 production build | 通过，有告警    | 主 JS chunk 约 564.80 kB，后续由 U9 做路由拆包  |
| Docker Compose        | 待 U6           | 当前只保留分服务本地启动脚本                    |
| 身份与权限            | 待 U3/U4        | 当前接口仍以资源 ID 和知识库存在性为主          |
| LangGraph checkpoint  | 待 U5           | 当前实现仍是内存 checkpoint                     |

## U1：Splitter 回归证据

回归快照固定四层输出：

```text
elements -> sections -> blocks -> chunks
```

当前样例覆盖：

- Markdown 标题、列表、表格和代码块
- TXT / OCR 普通文本与标题 fallback
- CSV 表头和行组
- Excel 多 sheet 和 table region
- DOCX heading、paragraph、list、table
- PDF layout、标题、分页、表格和页眉页脚噪声

PDF U1 修复点：PDF 视觉行拼接时，英文句子可能在行尾按布局换行。上一行以 `.`、`!`、`?` 等 ASCII 标点结束，下一行以英文单词开始时，拼接器必须补回一个空格，例如：

```text
Reviewers must check status.
Only active records can enter retrieval.
```

不能变成：

```text
Reviewers must check status.Only active records can enter retrieval.
```

同时，中文词间由 PDF 视觉抽取产生的伪空格仍然需要清理，例如 `IT 设 备采购` 应恢复成 `IT设备采购`。这两个规则不能混成一个“所有行都加空格”的简单逻辑。

本次回归还验证了同一 PDF 连续生成两次的快照完全一致，说明本问题不是 parser 非确定性，而是旧 baseline 与当前已修复的英文行拼接规则不一致。

## 七、U1 的学习重点

- 什么是 characterization test，为什么遗留系统改造前要先记录行为。
- 什么是 regression test，为什么不能只测试新功能的 happy path。
- JSON snapshot 如何保护多层 pipeline 的输出。
- parser、normalizer、section builder、block builder 和 chunk assembler 如何分工。
- PDF 为什么需要同时处理文字内容和坐标布局。
- 为什么测试 baseline 不能等同于“盲目接受当前输出”。
- 如何区分真实 bug、依赖版本差异、非确定性和有意行为变化。

## 八、已知边界

U1 只负责建立可信基线，不宣称项目已经完成身份权限、Alembic、持久化 checkpoint、CI 或完整生产可观测性。这些能力按路线图的 U2-U10 逐步补齐。
