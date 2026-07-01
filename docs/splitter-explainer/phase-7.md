# 切分讲解：Phase 7

## Phase 7 做了什么

这一阶段专门补 PDF text fallback。

目标很明确：

1. 不再把“只有一个标题、后面正文跨多页”的 PDF 退回成“一页一个 section”
2. 只要能检测到可靠标题，就尽量按标题组织 section
3. 如果完全没有可靠标题，仍然保留按页 fallback

所以这一阶段的本质不是做 PDF layout 解析，而是：

```text
把 PDF 文本 fallback 从粗粒度 page split，
升级成更像文档章节结构的 section split
```

---

## 这次改了哪两层

### 1. plain_text_parser.py

文件：

[backend/app/services/document_splitter/parsers/plain_text_parser.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/parsers/plain_text_parser.py:41)

之前 `parse_plain_text_elements_from_pages()` 的规则更保守：

```text
至少要检测到 2 个可靠标题，才按标题切
```

现在改成了：

```text
只要检测到 1 个可靠标题，就可以进入 heading-based elements
```

这个变化非常关键。

因为很多 PDF 的真实情况是：

- 第一页有一个章节标题
- 后面正文连续跨两三页
- 中间没有下一个章节标题

旧逻辑会因为“标题数量不足 2 个”直接退回 page fallback。
新逻辑则会把它看成：

```text
一个标题 section + 多页正文
```

---

### 2. section_builder.py

文件：

[backend/app/services/document_splitter/section_builder.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:242)

以前 `build_plain_text_sections_from_elements()` 还有一个隐藏限制：

```text
heading 少于 2 个时，整个 elements 直接并成一个无标题 section
```

这会让“单标题 PDF”即使 parser 已经识别出了 heading，也仍然无法真正切成标题 section。

现在改完后，逻辑变成：

- 没有 heading：整段退回一个普通 section
- 只要有 heading：就按 heading 切 section
- 如果 heading 前面还有导语内容：导语先单独成一个 preface section

所以“一个标题 + 多页正文”现在能被正常组织出来。

---

## 现在 PDF fallback 的整体路径

可以把当前流程理解成：

```text
PDF pages
  -> build_line_records_from_pages()
  -> detect_plain_text_heading_candidates()
  -> 如果有可靠标题:
       build_heading_based_plain_text_elements_from_records()
       -> build_plain_text_sections_from_elements()
  -> 如果没有可靠标题:
       split_pdf_sections() 按页 fallback
```

也就是说：

- 标题模式成功时，优先走“文档章节结构”
- 标题模式失败时，保留“页级安全保底”

---

## 跨页 section 是怎么形成的

这里的“跨页 section”不是说把多页文本强行拼成一个大 paragraph。

真实做法是：

1. 先保留页内原始文本行
2. 在 page 间打平 line records
3. 根据 heading 候选切 section 范围
4. section 内的 paragraph 仍然保留各自 page 信息

所以结果更像：

```text
Section: 第一章 总则
  heading(page 1)
  paragraph(page 1)
  paragraph(page 2)
  paragraph(page 3)
```

而不是：

```text
把 page1~3 生硬拼成一个大段
```

这很重要，因为后面 chunk metadata 还要追踪页码。

---

## 现在 section metadata 多了什么

这次还补了一个小但很实用的点：

`build_plain_text_sections_from_elements()` 现在会给 plain text / PDF fallback section 补基础 metadata：

- `page_start`
- `page_end`

所以一个跨页 section 现在能直接表达：

```text
这个 section 覆盖了第几页到第几页
```

对后面调试、验收、追溯都更方便。

---

## 一个小例子

假设 PDF 提取出的页文本是：

### Page 1

```text
第一章 总则
第一页正文。
```

### Page 2

```text
第二页正文。
```

### Page 3

```text
第三页正文。
```

旧逻辑：

```text
因为只有 1 个标题
-> 标题模式失败
-> 退回 3 个 page section
```

新逻辑：

```text
检测到 1 个可靠标题
-> 按标题组织 section
-> 得到 1 个跨页 section
```

结果会更像：

```text
Section:
heading_path = ["第一章 总则"]
page_start = 1
page_end = 3
```

---

## 无标题为什么还保留按页 fallback

因为 PDF 文本层质量经常不稳定。

如果强行在“没有可靠标题”的情况下跨页拼 section，很容易出现：

- 把不相关页面硬拼到一起
- 页眉页脚噪声污染 section
- 错误推断章节边界

所以当前策略还是保守的：

```text
有可靠标题 -> 按标题切
没有可靠标题 -> 按页 fallback
```

这也是这一阶段“可用但不冒进”的核心。

---

## 当前边界

这一阶段已经完成：

- 可靠标题检测
- 单标题跨页 section
- 多标题跨页 section
- 无标题按页 fallback
- section 级别的 `page_start / page_end`

还没做的是：

- 页眉页脚去重
- 多栏阅读顺序
- 真正的 PDF layout 解析
- 表格区域识别

这些属于后续更重的 PDF 增强阶段。

---

## 一句话理解

Phase 7 的本质是：

```text
让 PDF text fallback 只要看见可靠标题，
就尽量按章节组织跨页 section；
看不见标题时，再退回按页保底。
```
