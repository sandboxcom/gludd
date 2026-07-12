# SQL Injection Role (`general_ludd.security.sql_injection`)

Ansible role for SQL injection understanding — attack taxonomy, code audit, log audit, and remediation guidance. Never mutates source files; all output is advisory.

## Overview

This role provides a complete SQL injection knowledge and audit toolkit:
- **Attack Understanding** — data files cataloguing classic, advanced, NoSQL, and DBMS-specific attack vectors, plus WAF bypass techniques.
- **Code Audit** — pattern-based scanning of Python, Go, and JavaScript source for SQL injection (string concatenation, f-strings, ORM raw SQL, dynamic identifiers).
- **Log Audit** — web server and database query log analysis for SQLi attempts with IP correlation and timeline construction.
- **Remediation** — parameterized query examples, ORM-safe alternatives, input validation rules, and WAF rule generation for each finding.
- **Tool Awareness** — documentation and detection signatures for sqlmap, jSQL, NoSQLMap, Burp Suite, Bandit, Semgrep, and Gitleaks.

## Quick Start

```yaml
- name: Audit a codebase for SQL injection
  hosts: localhost
  roles:
    - role: general_ludd.security.sql_injection
      vars:
        scan_target: ./src
        artifact_dir: /tmp/sqli-audit
```

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `daemon_url` | `http://localhost:8000` | Gludd daemon URL |
| `psk` | `""` | Pre-shared key for daemon auth |
| `artifact_dir` | `/tmp/gludd-sql-injection` | Output directory for audit reports |
| `scan_target` | `.` | File or directory to audit |
| `scan_languages` | `[python, go, javascript]` | Languages to scan for SQLi patterns |
| `web_log_path` | `""` | Path to web server access log |
| `db_log_path` | `""` | Path to database query log |
| `simulate_attacks` | `false` | Generate attack simulation report (never executes) |
| `fail_on_critical` | `true` | Treat critical findings as overall failure |
| `fail_on_high` | `true` | Treat high-severity findings as overall failure |
| `enable_auto_patch` | `false` | NEVER set to true — report-only role |

## Code Audit Rules

| Rule | Language | Pattern | Severity |
|------|----------|---------|----------|
| SQLI-PY-001 | Python | f-string in execute() | critical |
| SQLI-PY-002 | Python | .format() in execute() | critical |
| SQLI-PY-003 | Python | %-formatting in execute() | critical |
| SQLI-PY-004 | Python | String concatenation in execute() | critical |
| SQLI-PY-005 | Python | Bare SQL keyword in execute() | high |
| SQLI-PY-006 | Python | Django raw() / extra() / RawSQL() | high |
| SQLI-PY-007 | Python | SQLAlchemy text() with string formatting | high |
| SQLI-PY-008 | Python | Peewee raw query | medium |
| SQLI-PY-009 | Python | Dynamic table/column names | medium |
| SQLI-GO-001 | Go | fmt.Sprintf with SQL | critical |
| SQLI-GO-002 | Go | String concat in db.Query/Exec | critical |
| SQLI-GO-003 | Go | Bare query string without placeholders | high |
| SQLI-JS-001 | JavaScript | Template literal with SQL | critical |
| SQLI-JS-002 | JavaScript | String concat in .query()/.execute() | critical |
| SQLI-JS-003 | JavaScript | Bare query string | high |

## Data Files

- `vars/attack_vectors.yml` — Taxonomy of SQLi attack vectors (classic, advanced, NoSQL)
- `vars/database_specifics.yml` — Per-DBMS quirks and detection queries (MySQL, PostgreSQL, MSSQL, Oracle, SQLite)
- `vars/bypass_techniques.yml` — WAF bypass techniques (encoding, comment obfuscation, whitespace, HTTP tricks)
