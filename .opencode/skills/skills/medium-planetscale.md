---
{
  "name": "medium-planetscale",
  "description": "Serverless MySQL/Postgres database skills with schema branching, index-aware queries, and migration workflows. Treat schema changes as reviewable, reversible code. Source: unicodeveloper/medium-2026.",
  "tags": [
    "database",
    "mysql",
    "planetscale",
    "schema",
    "migrations",
    "indexing",
    "medium-2026"
  ],
  "category": "database"
}
---

# PlanetScale Database Skills

Design schemas that scale from day one. Use branching workflows where every
schema change is reviewable, reversible, and mergeable.

## Principles

1. **Branch-based schema changes** — Create a database branch per feature,
   merge when done. Never modify production schema directly.
2. **Index-first design** — Design indexes alongside tables. Verify index
   covers expected query patterns before merging.
3. **SELECT discipline** — Never `SELECT *`. Only fetch columns needed.
4. **Scale testing** — Estimate query time at 10M rows. Add composite indexes
   for multi-column WHERE clauses.

## Workflow

```
User: Add user preferences to schema
Agent:
  1. Create branch: pscale branch create mydb add-user-prefs
  2. Design schema with appropriate indexes
  3. Verify index covers query patterns
  4. Create deploy request: pscale deploy-request create mydb add-user-prefs
  5. Report: schema ready for review with notes on constraints
```

## Query Analysis Pattern

Without skill:
```sql
SELECT * FROM orders WHERE status = 'pending' AND created_at > '2026-01-01';
-- Full table scan at scale
```

With skill:
```sql
SELECT id, user_id, total, created_at
FROM orders
WHERE status = 'pending' AND created_at > '2026-01-01';
-- Composite index: INDEX idx_status_created (status, created_at)
-- ~2ms at 10M rows vs ~8s without index
```

## Anti-patterns

- Writing schemas without considering query patterns
- Missing foreign key indexes
- Using FLOAT for monetary values (use DECIMAL)
- Adding indexes after production issues instead of during design

## Install reference

Original: `npx skills add planetscale/agent-skill`
