# 多格式 E2E 测试集要求

## 1. 文档目的

这份文档定义 U10 企业化 E2E 测试集的建设标准。后续生成测试文件、查询问题和验收脚本时，必须以本要求为准，不能临时凭感觉扩展内容。

E2E 是 **End-to-End，端到端测试**。它不是只调用某一个函数，而是从用户动作开始，验证完整业务链路：

```text
上传文件
  -> OSS 保存原文件
  -> PostgreSQL 创建 documents
  -> 解析
  -> Section / Block
  -> 切片
  -> PostgreSQL 写入 chunks
  -> embedding
  -> Elasticsearch 建立检索索引
  -> 语义搜索
  -> RAG 回答和引用
```

## 2. 测试集目标

测试集必须同时验证以下能力：

1. 文件上传和原文件保存。
2. 不同文件格式的解析器选择正确。
3. 标题、段落、列表、代码块和表格结构保留正确。
4. chunk 边界没有明显半句、半词和表格残片。
5. PostgreSQL 中的 document、knowledge item、chunk 关系完整。
6. Elasticsearch 中的 content、embedding、metadata 和权限字段完整。
7. Dense、BM25、RRF 和 rerank 能召回正确证据。
8. RAG 回答引用的文档和 chunk 与标准答案一致。
9. 无答案问题不会基于弱相关内容硬答。
10. 跨组织资源不会被检索或对话访问。

## 3. 固定文件矩阵

第一版固定 5 个文件，不追求文件数量，而追求每个文件承担明确测试职责。

| 文件 | 最低要求 | 必须覆盖的结构 |
| --- | --- | --- |
| `travel_reimbursement.txt` | 1,500-3,000 字 | 空行段落、普通文本标题、编号列表、金额条件 |
| `supplier_admission.md` | 2,000-4,000 字 | `# / ## / ###`、列表、Markdown 表格、代码块或配置片段 |
| `security_incident_response.pdf` | 3-5 页 | 页眉页脚、标题、多栏或视觉换行、列表、表格、跨页段落 |
| `employee_handbook.docx` | 3-5 页 | Heading、Normal、编号列表、项目符号、Word 原生表格 |
| `budget_and_risk_register.xlsx` | 3 个 sheet | 表头、数据行、文本数字混合、空单元格、多个 table region |

文件总大小建议控制在 1-5 MB。单个文件不得超过 2 MB，避免测试被文件体积而不是业务逻辑主导。

## 4. 统一业务主题

所有文件围绕同一个虚构企业的“制度与运营知识库”，但每个事实只在一个或两个文件中出现，避免所有文件重复相同内容。

必须包含这些可验证事实：

```text
差旅报销：普通员工提交发票、行程单和审批单；超过 20,000 元需要额外审批。
供应商准入：涉及客户数据处理时必须完成安全问卷和隐私影响评估。
安全事件：P1 事件 15 分钟内通知值班负责人，30 分钟内建立处置群。
预算总表：AI 知识库重构项目预算为 135,000 元，负责人为王璐。
风险清单：R-003 是知识库数据权限配置错误，影响等级为高。
```

金额、编号、负责人、时间限制和状态必须使用稳定且不重复的值，方便测试 BM25、关键实体门禁和表格检索。

## 5. manifest 要求

测试集必须有 `manifest.json`，不能只依赖文件名推断预期结果。

每个文件至少记录：

```json
{
  "document_key": "budget-risk-xlsx",
  "filename": "budget_and_risk_register.xlsx",
  "file_type": "xlsx",
  "sha256": "固定值",
  "expected_parser": "excel_parser",
  "expected_sheet_count": 3,
  "expected_block_types": ["table"],
  "expected_heading_paths": ["预算总表", "风险清单", "供应商评分"],
  "min_chunk_count": 3,
  "max_chunk_count": 20
}
```

PDF、DOCX 和 XLSX 还必须记录格式特有的预期，例如页数、表格数量、sheet 名称和标题数量。

## 6. 查询集要求

第一版准备 40 条 query，固定保存在 `queries.json`，不得每次运行随机生成。

| 类别 | 数量 | 例子 | 主要验证 |
| --- | ---: | --- | --- |
| 精确事实 | 8 | AI 知识库项目预算是多少？ | BM25 和表格检索 |
| 语义改写 | 7 | 哪些采购事项需要做安全评估？ | Dense 检索 |
| 条件与金额 | 5 | 什么情况下需要额外审批？ | 数字和条件实体 |
| 流程问题 | 5 | 安全事件发生后前 30 分钟做什么？ | 多段证据和排序 |
| 跨格式检索 | 4 | 预算负责人是谁，相关风险是什么？ | XLSX + 文档联合检索 |
| 无答案 | 3 | 公司食堂夜班补贴标准是多少？ | no-answer / human review |
| 权限问题 | 3 | 受限组织的内部制度是什么？ | 组织过滤和越权防护 |
| 总结问题 | 5 | 总结供应商准入的主要风险。 | 多 chunk 召回 |

每条 answerable query 至少记录：

```json
{
  "query_id": "budget-001",
  "query": "AI 知识库项目预算是多少？",
  "category": "factual",
  "expected_document_keys": ["budget-risk-xlsx"],
  "expected_keywords": ["AI 知识库重构", "135000", "王璐"],
  "expected_answer_contains": ["135,000", "王璐"]
}
```

无答案 query 的 `expected_document_keys` 必须为空；权限 query 必须记录允许组织和禁止组织。

## 7. 分层验收标准

### 7.1 解析验收

- 文件类型与 parser 一致。
- Markdown 标题层级正确。
- DOCX Heading 和列表类型正确。
- XLSX sheet 名称、表头和数据行保留。
- PDF 页眉页脚不进入正文，表格不被识别成普通段落。

### 7.2 切片验收

- 每个文件的 chunk 数在 manifest 规定范围内。
- chunk 不以半个英文单词或半句话开头。
- 表格 chunk 必须包含表头或明确的表格上下文。
- 标题前缀和 `heading_path` 匹配。
- `prev / next`、来源页码、sheet 名称等 metadata 存在。

### 7.3 索引验收

- `documents.status = indexed`。
- 每个 chunk 都有 `vector_id`。
- PostgreSQL chunk 数和 Elasticsearch 文档数一致或差异可解释。
- Elasticsearch 文档包含 `organization_id` 和 `knowledge_base_id`。
- 同一个文件重复 index 不产生重复向量。

### 7.4 检索验收

企业黄金集至少要求：

```text
Recall@5 >= 0.80
MRR >= 0.60
answerable query 引用正确率 >= 0.80
无答案问题不得直接生成确定性答案
越权 query 不得返回禁止组织证据
```

这些阈值是本项目的验收门槛，不是通用行业标准；最终报告必须同时保存每条 query 的命中结果，不能只保存平均分。

## 8. OSS 与测试环境要求

完整人工验收必须覆盖真实 OSS：

```text
客户端
  -> OSS multipart upload
  -> complete
  -> documents
  -> parse / split / embed / index
```

但 CI 不应强制真实 OSS、真实 Qwen API 或真实阿里云密钥。CI 使用 fake storage 和 fake embedding；真实 OSS 作为受控手工验收场景。

## 9. 可重复性要求

- 文件内容固定，不使用 `random`、当前时间或随机 ID 生成内容。
- 每个文件保存 SHA-256。
- query 顺序固定。
- 公开来源、版本、下载地址和许可证写入 manifest。
- 原始文件和测试密钥不得提交到 Git。
- 测试脚本必须支持从空知识库重复执行。
- 测试失败时输出 document_id、chunk_id、vector_id、query_id 和 request_id。

## 10. 明确不属于第一版

- 不追求覆盖所有 Office 版本。
- 不使用 80,000 页级别的大型版面数据集。
- 不把 OCR 训练集当作 RAG 检索黄金集。
- 不用 LLM 自动生成标准答案后再把它当作唯一真值。
- 不用单次人工演示结果代替可重复的自动化报告。

## 11. 与 U10 的关系

本要求先于测试文件生成和 E2E 脚本实现。U10 必须完成：

```text
测试文件
  + manifest
  + queries
  + expected outputs
  + upload/index/search/chat E2E 脚本
  + 最终报告
```

只有当这些内容能够在干净环境重复执行，才算完成企业多格式 E2E 验收。
