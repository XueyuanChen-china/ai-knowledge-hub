# Day 16：LLM Router

## 今日目标

把 Day 15 的规则 router 升级成：

```text
优先走 LLM Router
失败时走规则兜底
输出 direct / rag / tool
```

这样后面的图工作流就不再只靠死规则判断，而是先有一个可扩展的路由入口。

---

## 本次新增内容

### 1. Router 配置

[backend/app/config.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/config.py:1)

这次新增了 4 个配置项：

- `llm_router_base_url`
- `llm_router_api_key`
- `llm_router_model`
- `llm_router_timeout_seconds`

它们只服务于 Router，不直接影响 embedding、Elasticsearch 和 RAG 检索。

这样拆开的好处是：

- Router 和 Answer Node 后面可以分别接不同模型
- 本地没配 Key 时，Router 也不会把整个图跑挂
- 你后面换 Qwen、DeepSeek、GPT 或其他 OpenAI 兼容模型时，只改 `.env`

---

### 2. `llm_router_service.py` 在做什么

[backend/app/services/llm_router_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/services/llm_router_service.py:1)

这个文件负责三件事：

#### 第一件：拼 Router Prompt

`build_router_messages()`

它把问题组织成统一输入，告诉模型：

- 什么叫 `direct`
- 什么叫 `rag`
- 复杂总结和对比问题统一进入 `rag`
- 输出必须是 JSON

也就是说，这里不是直接“问模型要答案”，而是在“问模型这道题该走哪条链路”。

#### 第二件：调 OpenAI 兼容接口

当前默认示例使用 `gpt-5.6-luna`，通过项目内网提供的 `/v1/chat/completions` 兼容接口调用。配置中的 `*_BASE_URL` 只填写到 `/v1`，代码会自动拼接 `/chat/completions`；API Key 由本地环境变量提供，不写入仓库。

`call_openai_compatible_chat()`

这里直接调用：

```text
{base_url}/chat/completions
```

请求体里带：

- `model`
- `messages`
- `temperature=0`
- `max_tokens=64`
- `response_format={"type":"json_object"}`

这说明它是一个很轻量的分类调用，不是大段生成。

这里已经显式改成了 JSON Mode。

也就是说，现在不是只靠 prompt 说“请输出 JSON”，而是：

```text
prompt 里明确要求 JSON
  +
response_format={"type":"json_object"}
```

这样做有两个直接好处：

1. 模型更稳定地返回标准 JSON 字符串
2. 下游 `parse_router_output()` 的解析成本更低

同时要注意一个兼容要求：

- Qwen 的 JSON Mode 要求消息里必须包含 `JSON` 关键词

所以 `build_router_messages()` 里保留了：

```text
你只能输出 JSON，不要输出额外解释。
```

#### 第三件：把模型输出收口成标准 route

`parse_router_output()`

模型有时会返回：

```json
{"route":"rag","reason":"..."}
```

有时也可能只返回：

```text
rag
```

甚至外面包一层：

```json
{"route":"rag","reason":"..."}
```

所以这里做了统一解析和 `normalize_route()` 归一化，最后只接受：

- `direct`
- `rag`
- `tool`

别的值一律当解析失败处理，然后回退到规则 Router。

---

### 3. graph 节点怎么变了

[backend/app/graph/nodes.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/nodes.py:1)

这次 `router_node()` 的顺序变成：

```text
先尝试 llm_router_service.route_question_with_llm()
  -> 成功就用 LLM 结果
  -> 失败就走 route_question() 规则兜底
```

这样做的关键点是：LLM 是增强层，不是单点依赖。

所以就算你：

- 没配 Key
- Base URL 配错
- 模型接口超时
- 返回内容格式不稳定

这条工作流还是能继续跑。

---

### 4. 为什么复杂问题暂时统一走 `rag`

[backend/app/graph/workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/app/graph/workflow.py:1)

当前项目没有单独的多文档总结工作流。为了避免出现“Router 识别出来但没有可用回答”的占位分支，复杂的总结、归纳和对比问题暂时统一进入已有 RAG 链路。

原因很直接：

后续如果要增强，可以在 RAG 之上增加子问题拆解和多路检索，但不作为当前收尾范围。

---

## 当前链路

现在图工作流已经是：

```text
START
  -> router
      -> direct
      -> rag
      -> tool
  -> END
```

其中：

- `direct`：不查知识库
- `rag`：进入检索
- `tool`：直接调用上一轮上下文相关的只读工具

---

## 本次测试

新增测试：

[backend/tests/test_llm_router_service.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_llm_router_service.py:1)

测试了：

- `normalize_route()` 的归一化
- JSON / 纯文本两种 Router 输出解析
- Router prompt 组装
- JSON Mode 请求体是否正确带上 `response_format`

同时更新了：

[backend/tests/test_graph_workflow.py](/Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend/tests/test_graph_workflow.py:1)

现在会验证：

- `你好 -> direct`
- `采购复核的触发条件是什么 -> rag`
- `总结这个知识库的重点 -> rag`
- 如果 LLM Router 有结果，优先使用 LLM 结果

并且测试里显式 mock 掉了真实外部调用，避免本地配了 Key 后单测误打线上接口。
