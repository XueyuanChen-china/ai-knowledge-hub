# Day 3：知识库 CRUD API

## 今天完成了什么

Day 3 的目标是给 `knowledge_bases` 表加上第一组可用 API。

已完成接口：

```text
POST   /knowledge-bases
GET    /knowledge-bases
GET    /knowledge-bases/{id}
PUT    /knowledge-bases/{id}
DELETE /knowledge-bases/{id}
```

对应代码：

```text
backend/app/api/knowledge_base.py
backend/app/schemas/knowledge_base.py
backend/app/main.py
```

## 新增目录说明

Day 3 开始把代码拆成更清晰的层次：

```text
backend/app/api/
backend/app/schemas/
```

### `api`

`api` 目录放接口代码。

比如：

```text
backend/app/api/knowledge_base.py
```

它负责处理 HTTP 请求：

```text
接收请求
读取数据库
返回响应
处理 404 等错误
```

### `schemas`

`schemas` 目录放请求和响应的数据格式。

比如：

```text
KnowledgeBaseCreate
KnowledgeBaseUpdate
KnowledgeBaseRead
```

这样做的原因是：数据库模型和 API 输入输出不要完全绑死。

例如创建知识库时，用户只需要传：

```json
{
  "name": "公司制度知识库",
  "description": "保存公司制度、流程和规范"
}
```

不应该让用户传：

```text
id
created_at
updated_at
```

这些字段应该由数据库和后端自动生成。

## 接口说明

### 1. 创建知识库

```text
POST /knowledge-bases
```

请求体：

```json
{
  "name": "公司制度知识库",
  "description": "保存公司制度、流程和规范"
}
```

成功状态码：

```text
201 Created
```

返回示例：

```json
{
  "id": 1,
  "name": "公司制度知识库",
  "description": "保存公司制度、流程和规范",
  "created_at": "2026-06-10T10:00:00",
  "updated_at": "2026-06-10T10:00:00"
}
```

### 2. 查询知识库列表

```text
GET /knowledge-bases
```

返回示例：

```json
[
  {
    "id": 1,
    "name": "公司制度知识库",
    "description": "保存公司制度、流程和规范",
    "created_at": "2026-06-10T10:00:00",
    "updated_at": "2026-06-10T10:00:00"
  }
]
```

当前按 `created_at` 倒序返回。

### 3. 查询单个知识库

```text
GET /knowledge-bases/{id}
```

例如：

```text
GET /knowledge-bases/1
```

如果存在，返回知识库详情。

如果不存在，返回：

```text
404 Not Found
```

### 4. 更新知识库

```text
PUT /knowledge-bases/{id}
```

请求体：

```json
{
  "name": "公司制度与流程知识库",
  "description": "保存公司制度、流程、报销和审批规范"
}
```

说明：

```text
PUT 表示提交完整的新数据
```

当前更新时会刷新：

```text
updated_at
```

### 5. 删除知识库

```text
DELETE /knowledge-bases/{id}
```

成功状态码：

```text
204 No Content
```

如果知识库不存在，返回：

```text
404 Not Found
```

## Swagger 验收

启动服务：

```bash
cd backend
uvicorn app.main:app --reload
```

打开 Swagger：

```text
http://127.0.0.1:8000/docs
```

在页面里找到：

```text
knowledge-bases
```

按顺序测试：

```text
1. POST /knowledge-bases 创建知识库
2. GET /knowledge-bases 查看列表
3. GET /knowledge-bases/{id} 查看详情
4. PUT /knowledge-bases/{id} 修改知识库
5. DELETE /knowledge-bases/{id} 删除知识库
6. GET /knowledge-bases/{id} 再查一次，应该返回 404
```

## curl 验收

创建：

```bash
curl -X POST http://127.0.0.1:8000/knowledge-bases \
  -H "Content-Type: application/json" \
  -d '{"name":"公司制度知识库","description":"保存公司制度、流程和规范"}'
```

查询列表：

```bash
curl http://127.0.0.1:8000/knowledge-bases
```

查询详情：

```bash
curl http://127.0.0.1:8000/knowledge-bases/1
```

更新：

```bash
curl -X PUT http://127.0.0.1:8000/knowledge-bases/1 \
  -H "Content-Type: application/json" \
  -d '{"name":"公司制度与流程知识库","description":"保存公司制度、流程、报销和审批规范"}'
```

删除：

```bash
curl -X DELETE http://127.0.0.1:8000/knowledge-bases/1
```

## 今天的关键点

### `APIRouter`

`APIRouter` 用来拆分接口文件。

如果所有接口都写在 `main.py`，项目很快会变乱。

现在写法是：

```python
router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])
```

含义：

```text
这个文件里的接口都以 /knowledge-bases 开头
Swagger 里分组名叫 knowledge-bases
```

然后在 `main.py` 注册：

```python
app.include_router(knowledge_base_router)
```

### `Depends(get_session)`

每个接口都需要操作数据库，所以通过：

```python
session: Session = Depends(get_session)
```

拿到数据库会话。

接口执行结束后，`get_session` 里的 `with Session(engine)` 会自动关闭连接。

### `HTTPException`

当查询不到知识库时，用：

```python
raise HTTPException(status_code=404, detail="Knowledge base not found")
```

FastAPI 会自动把它转换成标准 HTTP 错误响应。

## Day 4 建议

下一步可以做知识条目 CRUD：

```text
POST /knowledge-items
GET /knowledge-items
GET /knowledge-items/{id}
PUT /knowledge-items/{id}
DELETE /knowledge-items/{id}
```

也可以先做：

```text
POST /documents
```

也就是上传文档入口。
