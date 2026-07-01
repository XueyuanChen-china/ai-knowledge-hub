# 切分讲解：Phase 3

## Phase 3 做了什么

这一阶段的目标不是新增文件格式，而是把 `plain text fallback` 变得更稳。

重点有三件事：

1. TXT / OCR / PDF 文本 fallback 共用一套标题检测
2. 标题检测不再只看正则，还会看上下文置信度
3. 过滤目录、孤立编号、页码这类噪声

---

## 现在 plain text fallback 的思路

当前 `plain_text_parser` 不是“匹配到正则就当标题”，而是分两步：

```text
先找标题候选
  -> 再根据上下文打分
  -> 再看后面有没有正文承接
```

也就是说，一行文本要真正被认成标题，至少要满足三层条件：

1. 看起来像标题
2. 上下文支持它像标题
3. 后面确实接了正文，而不是目录/大纲

---

## 关键文件

### 1. plain_text_parser.py

文件：

[backend/app/services/document_splitter/parsers/plain_text_parser.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/parsers/plain_text_parser.py:1)

这里新增了几层能力：

- `PlainTextLineRecord`
  - 行级记录，统一承载 `text / line_index / page_number`
- `PlainTextHeadingCandidate`
  - 标题候选，带 `confidence / pattern`
- `detect_plain_text_heading_candidates`
  - 标题检测主入口
- `score_plain_text_heading_candidate`
  - 根据上下文打分
- `filter_heading_candidates_with_body`
  - 过滤后面没有正文承接的假标题

这样 TXT、OCR、PDF fallback 都能复用同一套判断逻辑。

#### 这条链路现在是怎么跑的

如果从主流程往下看，`plain_text_parser.py` 现在大致是这条链路：

```text
原始文本
  -> build_line_records_from_text() / build_line_records_from_pages()
  -> detect_plain_text_heading_candidates()
       -> normalize_possible_heading_text()
       -> match_heading_pattern()
       -> score_plain_text_heading_candidate()
       -> filter_heading_candidates_with_body()
  -> build_plain_text_elements_from_records()
       -> 标题足够可靠：build_heading_based_plain_text_elements_from_records()
       -> 否则：build_paragraph_only_plain_text_elements_from_records()
  -> DocumentElement[]
```

也就是说，它现在不是“一次正则匹配完事”，而是：

1. 先把输入统一整理成行记录
2. 再找标题候选
3. 再给候选打分
4. 再过滤没有正文承接的假标题
5. 最后才决定走“按标题切”还是“按段落退回”

#### 1）`PlainTextLineRecord`

这一层是最底层输入。

它的作用不是增加业务语义，而是把不同来源的文本先统一成一套行级视图。

例如：

- TXT 来的文本，只有 `text + line_index`
- PDF fallback 来的文本，除了 `text + line_index`，还会带 `page_number`

这样后面标题检测逻辑就不用分两套写。

你可以把它理解成：

```text
plain text parser 的统一输入单元
```

#### 2）`detect_plain_text_heading_candidates()`

这是标题检测主入口。

它做的事情不是直接输出“最终标题”，而是先产生一批候选：

```text
这几行看起来像标题，但还要继续审
```

它内部会串下面几步：

- `normalize_possible_heading_text()`
  - 先做轻量清洗
  - 修 OCR 风格空格、`1 . 1` 这种编号空格
- `match_heading_pattern()`
  - 看这一行是否命中标题模式
  - 同时给一个基础置信度
- `score_plain_text_heading_candidate()`
  - 结合上下文加减分
- `filter_heading_candidates_with_body()`
  - 过滤掉后面没有正文的假标题

所以这个函数的真正职责是：

```text
从“像标题的行”里，筛出“足够可靠的标题候选”
```

#### 3）`score_plain_text_heading_candidate()`

这个函数解决的是：

```text
同样都长得像标题，哪一个更可信？
```

它现在主要看这些信号：

- 前一行是不是空行
- 后一行是不是空行
- 下一条非空行是不是更像正文
- 前一条非空行是否以句号结尾
- 当前标题是否过长
- 当前标题里空格是否异常多

也就是说，它不只看“这一行自身长什么样”，还看：

```text
这行出现在什么上下文里
```

这一步的价值很大，因为 OCR / PDF / 复制文本里，经常会有：

- 看起来像标题的目录项
- 看起来像标题的编号行
- 看起来像标题的页码残片

如果没有这一层打分，误判会明显变多。

#### 4）`filter_heading_candidates_with_body()`

这是第二道保守过滤。

它解决的问题是：

```text
即使某一行很像标题，也不代表它真的是正文 section 的开始
```

比如目录/提纲可能是这样：

```text
第一章 总则

第二章 范围

第三章 术语
```

这些行本身都很像标题，但如果每个标题后面都没有正文承接，那更像目录，不像真正的内容结构。

所以这个函数会检查：

```text
当前候选标题到下一个候选标题之间，
有没有像正文的内容
```

如果没有，就过滤掉。

#### 5）最终怎么落成 `DocumentElement[]`

当候选标题筛完后，会进入 `build_plain_text_elements_from_records()`。

这里做最终分流：

- 如果可靠标题 >= 2
  - 走 `build_heading_based_plain_text_elements_from_records()`
  - 生成 `heading + paragraph` 结构
- 如果不够可靠
  - 走 `build_paragraph_only_plain_text_elements_from_records()`
  - 整体退回普通段落模式

所以真正的最终判断点在这里：

```text
这份文本值不值得按章节结构来切
```

这一步是整个 plain text fallback 的收口位置。

---

### 2. PDF fallback

文件：

[backend/app/services/document_splitter/section_builder.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/section_builder.py:363)

`split_pdf_sections()` 现在的策略变成：

```text
先把 page text 打平成 line records
  -> 用 plain text heading detector 试一次
  -> 如果检测到多个可靠标题，就按标题切 section
  -> 否则继续退回原来的按页 fallback
```

这样改的好处是：

- 有章节结构的 PDF，不再只能“一页一个 section”
- 没有稳定标题的 PDF，仍然保留老的安全 fallback

---

## 标题检测比以前多了什么

### 1. OCR 风格空格容错

例如这种文本：

```text
第 一 章 总 则
1 . 1 适 用 范 围
```

现在会先做轻量归一化，再判断是不是标题。

注意这里不是无脑删空格，而是只对明显的 OCR 拆字场景做压缩，避免把正常文本也改坏。

---

### 2. 不再强依赖空行

以前更像：

```text
标题前后最好有空行
```

现在如果是强标题模式，并且下一行明显像正文，即使没有空行，也可以通过。

例如：

```text
第一章 总则
这里是第一章正文。
```

这对 OCR、复制出的 TXT、PDF 提取文本都更稳。

---

### 3. 目录 / 假标题过滤

现在会过滤这类情况：

- 页码
- 纯数字
- `Page 3`
- 只有标题没有正文承接的连续大纲/目录

比如：

```text
第一章 总则

第二章 范围

第三章 术语
```

如果后面没有正文，它不会被当成正式 section 结构，而会退回普通段落模式。

---

## 为什么这一步重要

Phase 2 解决的是：

```text
Markdown / TXT 先进入 DocumentElement 体系
```

Phase 3 解决的是：

```text
当输入本身结构差、噪声多时，
fallback 还能不能尽量识别出可靠章节
```

这个阶段本质上是在增强“差文本条件下的保守识别能力”。

---

## 当前边界

这一阶段已经完成：

- TXT 共用新版标题检测
- OCR 风格纯文本可复用新版检测
- PDF 文本 fallback 先走新版检测，再退回 page fallback

还没做的是：

- PDF layout 级别的真正结构解析
- 页眉页脚批量去重
- 多栏阅读顺序修正
- 更复杂的 OCR 行合并

这些会在更后面的 PDF 阶段继续做。

---

## 一句话理解

Phase 3 的本质是：

```text
让“普通文本 fallback”从简单正则匹配，
升级成一套带上下文判断和噪声容错的保守标题识别。
```
