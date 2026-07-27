# PostgreSQL 数据库迁移

项目使用 PostgreSQL + SQLModel，表结构演进由 Alembic 管理。

## 核心原则

- `SQLModel` 模型描述业务表结构，并作为 Alembic autogenerate 的 metadata 来源。
- Alembic migration 是真正可执行、可审查的 schema 变更记录。
- FastAPI 启动只检查数据库连接和 revision，不调用 `create_all()`，也不执行运行时 `ALTER TABLE`。
- 迁移必须在发布步骤中显式执行，应用启动不会偷偷修改生产库。

## 常用命令

在 `backend` 目录执行：

```bash
./.venv/bin/alembic current
./.venv/bin/alembic upgrade head
./.venv/bin/python -m unittest tests.test_database_migrations
```

也可以使用项目脚本：

```bash
bash scripts/migrate_database.sh current
bash scripts/migrate_database.sh upgrade
```

启动应用前，数据库必须已经处于代码的最新 revision。否则 `/health` 之外的应用启动检查会给出类似错误：

```text
Database revision is out of date: current='...', expected='...'.
Run 'alembic upgrade head' before starting the application.
```

## 当前 baseline

`alembic/versions/20260723_c544b5601674_baseline_schema.py` 固化当前 SQLModel metadata 对应的 12 张业务表、外键、唯一约束和索引。

紧接着的 `7a3d2e1f4c88_align_existing_schema` 是兼容性补齐 migration：针对早期开发库中已经存在、但运行时补列逻辑没有创建的索引和外键做幂等补齐。空库从 baseline 执行时这些对象已经存在，因此会自动跳过；已有库 stamp baseline 后执行 `upgrade head` 时则会补上缺失对象。

这是一个基线 migration，不是把历史每次改表过程都重新补写出来。对于已经存在、且人工确认结构与当前 baseline 一致的开发库，迁移步骤是：

1. 先使用 `pg_dump` 备份 PostgreSQL。
2. 对照 migration 检查表、字段、约束和索引。
3. 使用 `CONFIRM_EXISTING_SCHEMA=yes bash scripts/migrate_database.sh stamp-existing` 标记 baseline。
4. 立即执行 `bash scripts/migrate_database.sh upgrade`，让兼容性 migration 补齐缺失索引和外键。
5. 后续新增 migration 再使用 `upgrade head` 正常升级。

`stamp` 只写入 `alembic_version`，不会修改表结构，因此不能替代 schema 检查，也不能用于结构不一致的数据库。

## 新环境与已有环境的区别

新建空库：

```text
空 PostgreSQL 数据库 -> alembic upgrade head -> 全部表和索引
```

已有开发库：

```text
备份 -> 核对当前 schema -> alembic stamp baseline -> 后续增量 upgrade
```

不要对包含重要数据的现有库直接执行 `downgrade base`。当前 baseline 的 downgrade 会删除整套业务表，测试中只在临时空库验证其可逆性。

## 如何添加下一次 migration

修改 SQLModel 模型后，先在独立数据库中生成候选 migration：

```bash
./.venv/bin/alembic revision --autogenerate -m "describe schema change"
```

然后人工检查：

- nullable 是否正确；
- PostgreSQL 类型和默认值是否符合预期；
- 外键删除策略是否安全；
- 索引和唯一约束是否齐全；
- 已有数据是否需要先回填；
- downgrade 是否会误删业务数据。

最后在临时空库和带样例数据的测试库分别执行 `upgrade`，再把 migration 纳入代码评审。

## 学习重点

- `revision`：一次 schema 变更的唯一版本号。
- `head`：当前迁移链最新版本。
- `stamp`：只记录数据库已经处于某个版本，不执行变更。
- `upgrade`：按 migration 的 `upgrade()` 正向执行。
- `downgrade`：按 migration 的 `downgrade()` 回退，生产环境必须谨慎。
- `alembic_version`：数据库中记录当前 revision 的控制表。
