# Resource Authorization Reindex Runbook

U4 adds `organization_id` to Elasticsearch chunk documents. Existing indices do not contain this field and are intentionally excluded by the new kNN permission filter.

## Preconditions

1. PostgreSQL migration is at `f3a8d9e45c10` or later.
2. Existing business rows have been backfilled into the default organization.
3. Elasticsearch and the embedding model are reachable.

## Run

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/alembic upgrade head
./.venv/bin/python scripts/reindex_resource_ownership.py --batch-size 32
```

For one knowledge base:

```bash
./.venv/bin/python scripts/reindex_resource_ownership.py \
  --knowledge-base-id 7 \
  --batch-size 32
```

## Result

The script writes a versioned index named `knowledge_chunks_v2_{knowledge_base_id}`. After every chunk in one knowledge base is written successfully, it atomically moves `knowledge_chunks_{knowledge_base_id}_active` to that index.

Old indices remain untouched. To roll back a known-good alias, use the Elasticsearch alias API to point the alias at the prior index; do not delete the prior index until verification has completed.
