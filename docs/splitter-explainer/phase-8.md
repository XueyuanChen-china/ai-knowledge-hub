# 切分讲解：Phase 8

## Phase 8 做了什么

这一阶段开始真正引入 PDF layout 信息。

和前面的 PDF text fallback 不一样，这次不再只依赖：

- `page.extract_text()`
- 纯文本标题检测

而是直接读 PDF 页面里的版面信息：

- word bbox
- font size
- table 区域
- 页面宽高

然后基于这些信息做更细的结构恢复。

---

## 关键文件

文件：

[backend/app/services/document_splitter/parsers/pdf_layout_parser.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/parsers/pdf_layout_parser.py:1)

当前主链路是：

```text
pdf path
  -> pdfplumber.open()
  -> extract_words()
  -> find_tables()
  -> build_pdf_lines_from_words()
  -> detect_repeated_header_footer_texts()
  -> build_pdf_layout_elements_from_pages()
  -> heading / paragraph / table DocumentElement[]
```

也就是说，Phase 8 的重点不是“更复杂的 text split”，而是：

```text
先从页面版面里恢复结构，再进入统一 pipeline
```

---

## 1. bbox 是怎么来的

在 `extract_page_layout()` 里，先调：

```python
page.extract_words(...)
```

每个 word 都会带一组位置信息，例如：

- `x0`
- `x1`
- `top`
- `bottom`

这些就是最基础的 bbox 来源。

后面 `build_pdf_lines_from_words()` 会把同一行的多个 word 合并成一条 line，再把这些 word 的范围合并成 line bbox。

所以现在 parser 里的 `bbox` 不是凭空算的，而是：

```text
word bbox -> line bbox -> paragraph/table bbox
```

---

## 2. reading order 怎么做

PDF 难点之一是：

```text
页面里的视觉顺序，不一定等于文本提取顺序
```

尤其是双栏文档。

这次的基础做法是：

1. 先根据页面宽度判断更像单栏还是双栏
2. 双栏时，把 word 先分到左/右列
3. 每列内部再按 `top -> x0` 排序
4. 最终按列顺序组织 line / table item

对应代码：

- `detect_page_column_mode()`
- `assign_word_column()`
- `build_pdf_page_items()`

这还不是最终版的复杂阅读顺序引擎，但对基础双栏 PDF 已经够用。

---

## 3. 页眉页脚去重怎么做

这次的策略是：

1. 先看每页顶部和底部一定区域里的 line
2. 把这些 line 文本归一化
3. 如果同样的文本在多页重复出现，就标记为 header / footer
4. 后续构建正文元素时把它们过滤掉

对应代码：

- `detect_repeated_header_footer_texts()`
- `is_header_or_footer_line()`

所以这一步不是“按固定 y 坐标全删”，而是：

```text
位置 + 重复文本
```

两个条件一起成立才去掉。

这样比简单裁掉页顶页底更稳。

---

## 4. 表格抽取怎么做

这次表格不是靠纯文本猜，而是直接调：

```python
page.find_tables()
```

对应代码：

- `extract_pdf_tables()`

拿到表格区域后，会：

1. 提取表格二维数据
2. 标准化成矩阵
3. 判断是否有 header
4. 转成 Markdown table 文本
5. 生成 `table DocumentElement`

同时，正文 line 在进入 paragraph 构建前，会先排除掉落在 table bbox 内的 words / lines。

也就是说：

```text
表格内容不会再重复混进正文 paragraph
```

这一步非常关键。

---

## 5. heading 怎么比以前更稳

以前 PDF fallback 主要靠纯文本标题模式。

现在 `detect_pdf_heading_level()` 多了一层版面信号：

- 文本是否短
- font size 是否明显大于页面中位数
- 是否同时命中标题模式

所以它现在更像：

```text
文本模式 + 字体大小
```

而不是只看一行字长得像不像标题。

---

## 6. paragraph 怎么分

正文 line 不会一行一段直接变 paragraph。

现在 `should_start_new_pdf_paragraph()` 会看：

- 是否跨页
- 是否跨列
- 上下两行垂直间距是否明显变大

如果间距超过阈值，就开新 paragraph。

所以当前 paragraph 的形成逻辑是：

```text
同列 + 相邻行距合理 -> 归成同一段
```

---

## 一个小例子

假设 PDF 页面是这样：

```text
页眉：企业内部资料

左栏：
1. Overview
Left paragraph line 1
Left paragraph line 2

右栏：
Right paragraph line 1
Right paragraph line 2

下面有一个表格

页脚：Page 2
```

现在 parser 会尽量得到：

- 页眉被去掉
- 页脚被去掉
- `1. Overview` 识别成 heading
- 左栏正文按 reading order 先于右栏正文
- 表格单独抽成 table element
- 表格内容不再重复出现在 paragraph 里

---

## 接入方式

这一阶段也接进了主链路。

现在在：

[backend/app/services/document_splitter/splitter.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/document_splitter/splitter.py:45)

PDF 会这样走：

```text
有 pdf_path
  -> 先走 parse_pdf_layout_elements_from_document()
  -> 如果失败，再退回 parse_plain_text_elements_from_pages()
```

上传接口也会把真实 pdf 文件路径传给 splitter：

[backend/app/api/document.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/api/document.py:191)

所以这不是单独 demo，而是已经进了实际上传/切片链路。

---

## 当前边界

这一阶段已经完成：

- bbox
- 基础 reading order
- 基础双栏支持
- 重复页眉页脚去重
- 表格区域抽取
- layout parser 失败回退到 text fallback

还没做的是：

- 更复杂的多栏混排
- 图片、caption、footnote 关系
- 更强的表格结构恢复
- 跨页表格拼接
- 真正的 layout tree

这些属于下一层更重的 PDF 结构增强。

---

## 一句话理解

Phase 8 的本质是：

```text
让 PDF 不再只是“抽文本再切”，
而是先利用页面版面信息恢复出更可信的 heading / paragraph / table 结构。
```
