# U2：用 Alembic 管理 PostgreSQL Schema

U1 固定了测试基线，U2 解决的是数据库结构如何持续演进。以前应用启动时会调用 `create_all()`，并通过 `ensure_*_columns()` 执行 `ALTER TABLE` 补列；这在开发期方便，但生产环境无法追踪是谁、什么时候、以什么顺序改了表。

## 这次改造后的链路

```text
SQLModel models.py
        -> SQLModel.metadata
        -> Alembic migration files
        -> alembic upgrade head
        -> PostgreSQL schema
        -> FastAPI 启动时只检查 revision
```

应用启动不再负责建表。发布流程先执行迁移，应用发现数据库 revision 落后时直接报清晰错误，避免多个服务实例同时偷偷改 schema。

## 重点代码

- `backend/alembic.ini`
  - Alembic 的入口配置和 migration 脚本位置。
- `backend/alembic/env.py`
  - 导入全部 SQLModel 模型，提供 `target_metadata`。
  - online 模式连接 PostgreSQL 执行 migration，offline 模式生成 SQL。
- `backend/alembic/versions/20260723_c544b5601674_baseline_schema.py`
  - 当前 12 张业务表的 baseline，包括字段、外键、唯一约束和索引。
- `backend/alembic/versions/20260723_7a3d2e1f4c88_align_existing_schema.py`
  - 兼容历史开发库，幂等补齐旧运行时 DDL 没有创建的索引和外键。
- `backend/app/db/database.py`
  - `check_database_ready()` 只执行 `SELECT 1` 和 revision 校验。
- `backend/tests/postgres_test_utils.py`
  - 每个测试创建空 PostgreSQL 数据库后执行 `alembic upgrade head`，不再调用 `SQLModel.metadata.create_all()`。

## 必须区分的三个动作

### `upgrade`

真正执行 migration 的 `upgrade()`，会创建或修改数据库结构：

```bash
./.venv/bin/alembic upgrade head
```

### `stamp`

只向 `alembic_version` 写入版本号，不执行任何表结构操作。它只适用于已经人工核对过结构的存量库：

```bash
CONFIRM_EXISTING_SCHEMA=yes bash scripts/migrate_database.sh stamp-existing
bash scripts/migrate_database.sh upgrade
```

### `downgrade`

执行 migration 的回退逻辑。当前 baseline 的回退会删除业务表，因此只在临时空库测试，不能拿来当生产回滚方案。真实生产回滚通常需要先设计数据兼容策略和备份恢复方案。

## 新库和旧库

新 PostgreSQL 数据库：

```text
空库 -> upgrade head -> 全部表和索引
```

已有开发库：

```text
pg_dump 备份 -> 对照 baseline 检查 -> stamp baseline -> upgrade head
```

不能对结构未知的数据库直接 `stamp`，因为 Alembic 会相信你声明的版本，之后不会补齐 baseline 中已经被跳过的建表操作。

## 如何验证

```bash
cd backend
./.venv/bin/python -m unittest tests.test_database_migrations
./.venv/bin/alembic current
```

迁移测试验证：

1. 空库升级后包含全部业务表和 `alembic_version`。
2. 数据库 revision 等于当前代码 head。
3. baseline 在空测试库中可以 downgrade，再重新 upgrade。

## 面试学习重点

- migration 是“数据库 schema 的版本控制”，和 Git 控制代码版本类似。
- `alembic_version` 是数据库当前版本指针，不是业务表。
- `stamp` 不等于迁移；它只是声明“这个库已经处于某版本”。
- 多个应用副本不应该同时负责迁移，应该由发布 job 或 CI/CD 执行一次。
- 新增非空字段时，通常要拆成“先允许为空 -> 回填数据 -> 再改成非空”多个步骤。
- 删除字段、降级和数据迁移要分别考虑 schema 可回退性与业务数据可恢复性。
