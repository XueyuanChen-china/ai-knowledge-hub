# 多格式 E2E 固定测试集

该目录保存 U10 端到端验收使用的合成企业制度数据。五个源文件共享同一业务背景，但事实分布在不同格式中，可同时验证解析、切片、索引、混合检索、引用和权限边界。

## 目录

```text
source/       TXT、Markdown、PDF、DOCX、XLSX 原始输入
manifest.json 文件哈希、解析器和结构预期
queries.json  40 条固定检索问题及人工标注
expected/     parser、chunk 和 retrieval 分层验收合同
```

## 重新生成

XLSX 使用 `@oai/artifact-tool` 构建，其余文件及 JSON 合同由 Python 生成：

```bash
node backend/scripts/generate_multiformat_e2e_workbook.mjs
cd backend
./.venv/bin/python scripts/generate_multiformat_e2e_fixtures.py
```

工作簿生成脚本需要能够解析 `@oai/artifact-tool`。仓库提交的是固定生成物和 SHA-256；普通测试执行不需要安装该构建工具。

## 数据边界

- 全部内容均为项目自建的虚构测试数据，不包含真实员工、客户或供应商信息。
- 文件内容和 query 顺序固定，不使用当前时间、随机 ID 或在线生成内容。
- `manifest.json` 中的 SHA-256 用于发现输入文件是否被意外修改。
- 真实 OSS、Qwen 和 Elasticsearch 的完整验收由 U10 E2E 脚本负责，本目录本身不包含密钥。
