---
name: test-quality
description: When writing tests, enforce isolation, determinism, and meaningful assertions. Defines the 10 rules every test in this repo must follow — proof of behavior, AAA structure, 3-layer coverage, edge cases, no mock-only tests, realistic data, naming, one-assertion-concept, mandatory TDD, no coverage gaming.
---

# Test Quality Skill

When writing tests, follow these rules. This is NOT advisory.

---

## Rule 1: Tests Must Prove Behavior

Every test must make at least one assertion about observable behavior.
Tests that only check existence (file exists, import works) are
insufficient unless they are explicitly plumbing/smoke tests.

### BEFORE (bad) → AFTER (good)

#### Example 1A: Existence check → Behavior verification

```python
# BAD — proves the object was created, not that it works
def test_router_exists():
    router = ModelRouter()
    assert router is not None
```

```python
# GOOD — verifies the router produces correct routing decisions
def test_router_routes_to_correct_model_for_task_type():
    router = ModelRouter()
    decision = router.route(task_type="code-review")
    assert decision.model_id == "sonnet"
    assert decision.max_tokens == 4096
```

#### Example 1B: File-exists test → Behavior test

```python
# BAD — proves a file was created, not that it has correct content
def test_config_file_created(tmp_path):
    config_path = tmp_path / "gludd.yml"
    write_default_config(config_path)
    assert config_path.exists()
```

```python
# GOOD — verifies the config file content is correct
def test_config_file_contains_required_keys(tmp_path):
    config_path = tmp_path / "gludd.yml"
    write_default_config(config_path)
    content = config_path.read_text()
    assert "agent_floor: 10" in content
    assert "default_model: sonnet" in content
```

#### Example 1C: Import-works test → Integration test

```python
# BAD — proves Python can import the module, not that it functions
def test_event_loop_imports():
    from general_ludd.loop import EventLoop  # noqa: F811
    assert EventLoop is not None
```

```python
# GOOD — verifies the event loop can process a todo and produce a result
def test_event_loop_dispatches_todo_and_records_result():
    loop = EventLoop(db=in_memory_db())
    loop.enqueue(TodoModel(task="fix login bug", agent_id="agent-1"))
    loop.tick()
    result = loop.get_result("agent-1")
    assert result is not None
    assert result.status in ("completed", "in_progress")
```

#### Example 1D: Mock-only assertion → System behavior assertion

```python
# BAD — only checks that a mock was called, not that the result is correct
def test_email_sender_calls_smtp():
    mock_smtp = MagicMock()
    sender = EmailSender(smtp=mock_smtp)
    sender.send(to="user@example.com", subject="Hi", body="Hello")
    mock_smtp.sendmail.assert_called_once()
```

```python
# GOOD — verifies the email was sent with correct content
def test_email_sender_produces_correct_message():
    mock_smtp = MagicMock()
    sender = EmailSender(smtp=mock_smtp)
    sender.send(to="user@example.com", subject="Hi", body="Hello")

    call_args = mock_smtp.sendmail.call_args
    assert call_args is not None
    _from, to, msg = call_args[0]
    assert to == "user@example.com"
    assert "Subject: Hi" in msg
    assert "Hello" in msg
    mock_smtp.sendmail.assert_called_once()  # secondary — strengthens the proof
```

---

## Rule 2: Three-Act Structure (AAA)

Every test must follow **Arrange-Act-Assert**. Skip none. Combine none.

```python
# GOOD — clear AAA separation
def test_agent_worktree_creation_produces_valid_path():
    # === Arrange ===
    repo = create_temp_git_repo()          # isolated git repo
    branch_name = "agent-fix-login"

    # === Act ===
    worktree_path = create_agent_worktree(
        repo_path=repo.path,
        branch=branch_name,
    )

    # === Assert ===
    assert worktree_path.is_dir()
    assert (worktree_path / ".git").exists()
    assert worktree_path.name == branch_name
```

```python
# BAD — no Arrange: depends on global state, Act and Assert interleaved
def test_worktree_creation():
    # Arrange is missing — assumes repo exists at a hardcoded path
    wt = create_agent_worktree("/Users/shawnwilson/gludd", "agent-x")
    if wt.is_dir():
        assert wt.name == "agent-x"
        assert (wt / ".git").exists()  # Assert then Act then Assert — no structure
    else:
        assert False  # passive-aggressive assert
```

```python
# BAD — multiple Acts in one test
def test_worktree_lifecycle():
    wt = create_agent_worktree(repo, "agent-x")     # Act 1
    assert wt.is_dir()
    merge_agent_worktree(repo, "agent-x")            # Act 2
    assert branch_is_merged(repo, "agent-x")
    remove_agent_worktree(repo, "agent-x")           # Act 3
    assert not wt.exists()
    # Three tests crammed into one — split into test_create, test_merge, test_remove
```

---

## Rule 3: Coverage Layers

Tests must exist at all three levels for user-facing features.

| Level | Directory | Tests What | Scope |
|-------|-----------|------------|-------|
| Unit | `tests/unit/` | Individual functions/classes in isolation | Single function or class |
| Integration | `tests/integration/` | 2+ subsystems together | Multiple modules, real DB |
| E2E | `tests/e2e/` | Through daemon API as a user would | Full stack via HTTP |

### Concrete example — "Create Todo" feature across all 3 layers

```python
# ===== tests/unit/test_todo_validator.py =====
"""Unit: tests TodoValidator in isolation — no DB, no HTTP."""

class TestTodoValidator:
    def test_valid_todo_passes_validation(self):
        todo = TodoInput(task="fix the login bug", agent_id="agent-1")
        result = TodoValidator().validate(todo)
        assert result.is_valid is True
        assert result.errors == []

    def test_empty_task_fails_validation(self):
        todo = TodoInput(task="", agent_id="agent-1")
        result = TodoValidator().validate(todo)
        assert result.is_valid is False
        assert "task must not be empty" in result.errors
```

```python
# ===== tests/integration/test_todo_creation_flow.py =====
"""Integration: tests TodoRepository + TodoValidator + DB together."""

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

class TestTodoCreationFlow:
    def test_create_todo_persists_and_validates(self, db_session):
        repo = TodoRepository(db_session)
        validator = TodoValidator()

        todo_input = TodoInput(task="fix the login bug", agent_id="agent-1")
        assert validator.validate(todo_input).is_valid  # validator passes

        todo = repo.create(todo_input)                   # DB persists
        assert todo.id is not None

        fetched = repo.get_by_id(todo.id)                # read back
        assert fetched.task == "fix the login bug"
        assert fetched.status == "pending"

    def test_create_todo_with_invalid_input_rolls_back(self, db_session):
        repo = TodoRepository(db_session)
        validator = TodoValidator()

        todo_input = TodoInput(task="", agent_id="agent-1")
        assert not validator.validate(todo_input).is_valid

        with pytest.raises(ValueError, match="Validation failed"):
            repo.create(todo_input)  # repo should reject invalid input

        todos = repo.list_all()
        assert len(todos) == 0  # nothing persisted
```

```python
# ===== tests/e2e/test_todo_api.py =====
"""E2E: tests through the actual FastAPI daemon via HTTP."""

@pytest.fixture
def daemon_client():
    """Start the daemon in a test process and return a TestClient."""
    from general_ludd.daemon import app
    from fastapi.testclient import TestClient
    return TestClient(app)

class TestTodoAPI:
    def test_create_todo_via_api(self, daemon_client):
        response = daemon_client.post("/api/todos", json={
            "task": "fix the login bug",
            "agent_id": "agent-1",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["task"] == "fix the login bug"
        assert data["status"] == "pending"
        assert "id" in data

    def test_create_todo_empty_task_returns_422(self, daemon_client):
        response = daemon_client.post("/api/todos", json={
            "task": "",
            "agent_id": "agent-1",
        })
        assert response.status_code == 422
        assert "task must not be empty" in response.text

    def test_list_todos_returns_all(self, daemon_client):
        daemon_client.post("/api/todos", json={"task": "task A", "agent_id": "a1"})
        daemon_client.post("/api/todos", json={"task": "task B", "agent_id": "a1"})

        response = daemon_client.get("/api/todos")
        assert response.status_code == 200
        todos = response.json()
        assert len(todos) == 2
```

---

## Rule 4: Edge Cases Required

For every function, at minimum test:

- Happy path (normal input, expected output)
- Empty input (`None`, `""`, `[]`, `{}`)
- Invalid input (wrong type, out of range)
- Error path (exception raised, fallback triggered)
- Boundary (max/min values, edge of range)

### Full example: 5 edge cases for `parse_worktree_name()`

```python
# src/general_ludd/git_automation/worktree.py
def parse_worktree_name(raw: str) -> str:
    """Extract the branch name from a git-worktree list line.
    Input looks like: '/path/to/worktree  abc123 [branch-name]'
    Returns the branch name without brackets.
    Raises ValueError if the line doesn't contain a bracketed branch name.
    """
    import re
    match = re.search(r"\[(.+?)\]", raw)
    if match is None:
        raise ValueError(f"No branch name found in: {raw!r}")
    return match.group(1)
```

```python
# tests/unit/test_worktree_parse.py
import pytest
from general_ludd.git_automation.worktree import parse_worktree_name

class TestParseWorktreeName:

    # --- Happy path ---
    def test_extracts_branch_from_valid_line(self):
        result = parse_worktree_name(
            "/tmp/worktrees/agent-fix  abc123def [agent-fix-login]"
        )
        assert result == "agent-fix-login"

    def test_extracts_branch_with_special_chars(self):
        result = parse_worktree_name(
            "/path  deadbeef [feature/JIRA-123_fix-auth]"
        )
        assert result == "feature/JIRA-123_fix-auth"

    # --- Empty input ---
    def test_empty_string_raises_valueerror(self):
        with pytest.raises(ValueError, match="No branch name found"):
            parse_worktree_name("")

    def test_line_without_brackets_raises_valueerror(self):
        with pytest.raises(ValueError, match="No branch name found"):
            parse_worktree_name("/path  abc123 main")

    # --- Invalid input ---
    def test_none_input_raises_typeerror(self):
        with pytest.raises(TypeError):
            parse_worktree_name(None)  # type: ignore[arg-type]

    def test_non_string_input_raises_typeerror(self):
        with pytest.raises(TypeError):
            parse_worktree_name(42)  # type: ignore[arg-type]

    # --- Error path ---
    def test_empty_brackets_returns_empty_string(self):
        result = parse_worktree_name("/path  abc123 []")
        assert result == ""

    # --- Boundary ---
    def test_very_long_branch_name(self):
        long_name = "a" * 200
        line = f"/path  abc123 [{long_name}]"
        result = parse_worktree_name(line)
        assert result == long_name

    def test_branch_name_with_nested_brackets(self):
        # Bracket matching is greedy — first ] terminates
        result = parse_worktree_name("/path  abc123 [outer [inner]]")
        assert result == "outer [inner"
```

---

## Rule 5: No Mock-Only Tests

Tests that ONLY test mocks are worthless. A test must verify that the SYSTEM
produces the right output.

```python
# BAD — mock-only: proves the code CALLS the mock, not that it returns correct data
def test_dispatcher_calls_worker():
    mock_worker = MagicMock()
    dispatcher = Dispatcher(worker=mock_worker)

    dispatcher.dispatch(TodoModel(task="fix bug"))

    mock_worker.execute.assert_called_once()
    # Nothing asserts the result the dispatcher returns or the side effects — mock-only


# ACCEPTABLE — mock as secondary: primary assertion is on system output
def test_dispatcher_returns_correct_status_after_dispatch():
    mock_worker = MagicMock()
    mock_worker.execute.return_value = {"status": "running", "job_id": "j-123"}
    dispatcher = Dispatcher(worker=mock_worker)

    result = dispatcher.dispatch(TodoModel(task="fix bug"))

    assert result["status"] == "running"           # PRIMARY: system output
    assert result["job_id"] == "j-123"             # PRIMARY: system output
    mock_worker.execute.assert_called_once()        # SECONDARY: interaction check


# GOOD — no mocks: tests the real system with a real (in-memory) dependency
def test_dispatcher_with_real_worker():
    worker = InMemoryWorker()                       # real implementation, no mocks
    dispatcher = Dispatcher(worker=worker)

    result = dispatcher.dispatch(TodoModel(task="fix bug"))

    assert result["status"] == "running"
    assert worker.list_jobs()[0].task == "fix bug"   # verify side effect on real object
```

---

## Rule 6: Realistic Data

Use realistic test data, not placeholders.

```python
# BAD — placeholder data: doesn't match production shapes
@pytest.fixture
def agent_config_bad():
    return {
        "name": "test",
        "model": "foo",
        "x": 1,
        "y": 2,
    }
```

```python
# GOOD — realistic data: matches actual production config shape
@pytest.fixture
def agent_config():
    return {
        "agent_id": "agent-0a1b2c3d",
        "model": "sonnet",
        "provider": "anthropic",
        "max_tokens": 4096,
        "temperature": 0.3,
        "timeout_ms": 300_000,
        "worktree_path": "/tmp/gludd-worktrees/agent-code-review",
        "branch": "agent-code-review",
        "created_at": "2026-07-25T14:30:00Z",
    }

@pytest.fixture
def git_worktree_list_output():
    """Real output from `git worktree list --porcelain`."""
    return (
        "worktree /Users/shawnwilson/gludd\n"
        "HEAD abc123def456\n"
        "branch refs/heads/master\n"
        "\n"
        "worktree /tmp/gludd-worktrees/agent-fix-slurm\n"
        "HEAD 789abc012def\n"
        "branch refs/heads/agent-fix-slurm\n"
    )

@pytest.fixture
def realistic_agents_md_content():
    """Real markdown content as found in AGENTS.md files."""
    return """
## CRITICAL: Bash Command Policy

**You MUST only run `make <target>` commands in bash.**

- ALLOWED: `make test`, `make lint`, `make init`
- DENIED: `uv run ...`, `python3 ...`, `pip install ...`

This is enforced by the enforce-make.ts plugin.
"""
```

---

## Rule 7: Test Names Must Describe Behavior

Pattern: `test_<subject>_<condition>_<expected_result>`

### 10 BAD → GOOD name conversions

| BAD | GOOD |
|---|---|
| `test_worktree_1` | `test_create_worktree_returns_valid_path_when_repo_is_clean` |
| `test_worktree_2` | `test_create_worktree_raises_error_when_branch_already_exists` |
| `test_monitor` | `test_monitor_detects_abandoned_worktree_when_no_commits_for_24h` |
| `test_parse` | `test_parse_worktree_name_extracts_branch_from_valid_line` |
| `test_dispatch_works` | `test_dispatcher_returns_running_status_when_worker_accepts_job` |
| `test_error` | `test_email_sender_raises_connection_error_when_smtp_unreachable` |
| `test_edge` | `test_task_queue_rejects_empty_task_with_validation_error` |
| `test_feature_x` | `test_rate_limiter_allows_request_when_under_limit` |
| `test_fix_123` | `test_config_loader_merges_env_vars_over_file_defaults` |
| `test_new` | `test_gate_parser_extracts_phase_from_marker_line` |

### Name breakdown

```
test_<subject>_<condition>_<expected_result>
     │           │                 │
     │           │                 └── What should happen
     │           └── Under what circumstances
     └── What module/function/class is being tested
```

```
test_create_worktree_raises_error_when_branch_already_exists
│    │                │            │
│    │                └── result: error is raised
│    └── subject: create_worktree
└── test prefix
└── condition: branch already exists
```

---

## Rule 8: One Assertion Concept Per Test

Each test should verify one logical concept. Multiple assertions for the SAME
concept (e.g., checking multiple fields on a result) are fine. Do not mix
unrelated concepts in one test.

```python
# BAD — mixes 3 different concepts in one test:
#   1. validation passes
#   2. DB write succeeds
#   3. logging happens
def test_create_todo_everything():
    validator = TodoValidator()
    repo = TodoRepository(in_memory_db())
    logger = InMemoryLogger()

    todo = TodoInput(task="fix bug", agent_id="a1")
    assert validator.validate(todo).is_valid                          # concept 1

    result = repo.create(todo)
    assert result.status == "pending"                                 # concept 2
    assert result.id is not None                                      # concept 2
    assert logger.has_message("Todo created: fix bug")                # concept 3

    fetched = repo.get_by_id(result.id)
    assert fetched.task == "fix bug"                                  # concept 2 again
```

```python
# GOOD — split into 3 focused tests:
def test_todo_validator_accepts_valid_input():
    todo = TodoInput(task="fix bug", agent_id="a1")
    result = TodoValidator().validate(todo)
    assert result.is_valid is True


def test_todo_repository_persists_and_returns_todo():
    repo = TodoRepository(in_memory_db())
    todo = TodoInput(task="fix bug", agent_id="a1")

    result = repo.create(todo)

    assert result.status == "pending"
    assert result.id is not None
    fetched = repo.get_by_id(result.id)
    assert fetched.task == "fix bug"


def test_todo_creation_logs_message():
    logger = InMemoryLogger()
    repo = TodoRepository(in_memory_db(), logger=logger)

    repo.create(TodoInput(task="fix bug", agent_id="a1"))

    assert logger.has_message("Todo created: fix bug")
```

---

## Rule 9: TDD Is Mandatory

You MUST write a failing test BEFORE writing implementation code.

### TDD play-by-play (terminal transcript)

```bash
# Step 1: Identify the behavior — "parse_worktree_name() extracts branch from git output"
# Step 2: Write the failing test

$ cat > tests/unit/test_worktree_parse.py << 'EOF'
import pytest
from general_ludd.git_automation.worktree import parse_worktree_name

def test_extracts_branch_from_valid_line():
    result = parse_worktree_name("/path  abc123 [agent-fix]")
    assert result == "agent-fix"
EOF

# Step 3: Run — confirm it FAILS (module doesn't exist yet)
$ make test-specific TESTFILE='tests/unit/test_worktree_parse.py::test_extracts_branch_from_valid_line'
...
E   ModuleNotFoundError: No module named 'general_ludd.git_automation.worktree'
...
FAILED  # RED — correct, the behavior doesn't exist

# Step 4: Write minimal implementation
$ cat > src/general_ludd/git_automation/worktree.py << 'EOF'
import re

def parse_worktree_name(raw: str) -> str:
    match = re.search(r"\[(.+?)\]", raw)
    if match is None:
        raise ValueError(f"No branch name found in: {raw!r}")
    return match.group(1)
EOF

# Step 5: Run — confirm it PASSES
$ make test-specific TESTFILE='tests/unit/test_worktree_parse.py::test_extracts_branch_from_valid_line'
...
1 passed
PASSED  # GREEN — behavior exists and is verified

# Step 6: Refactor (if needed), keeping tests green
# (no refactor needed — the implementation is already minimal)
```

---

## Rule 10: Coverage Not Gaming

Coverage metrics serve the tests, not the other way around.

```python
# BAD — coverage-gaming: hits the line without verifying behavior
def handle_error(code: int) -> str:
    if code == 404:
        return "Not Found"
    elif code == 500:
        return "Server Error"
    else:
        return "Unknown"
    # Coverage tool says line X is uncovered? Write this:
    # (never actually verify it returns "Unknown" for code=999)

def test_handle_error_coverage_game():
    assert handle_error(404) == "Not Found"
    assert handle_error(500) == "Server Error"
    assert handle_error(999) is not None  # <-- COVERAGE GAMING: hits the line but proves nothing
```

```python
# GOOD — behavior-verifying: every branch asserts the actual output
def test_handle_error_known_codes():
    assert handle_error(404) == "Not Found"
    assert handle_error(500) == "Server Error"


def test_handle_error_unknown_code_returns_default():
    assert handle_error(999) == "Unknown"       # verifies the actual behavior
    assert handle_error(0) == "Unknown"         # verifies boundary
    assert handle_error(-1) == "Unknown"        # verifies negative
```

### What to do with uncovered lines

| Situation | Action |
|---|---|
| Line is dead code (never reached) | **Delete it.** Do not write a test for dead code. |
| Line is a trivial getter/setter | Verify it indirectly through behavior tests; do not write a dedicated test. |
| Line is hard to reach (deep error path) | Use dependency injection or monkeypatching to trigger the error condition. |
| Line is genuinely untestable (C-level signal handler) | Document with a comment `# pragma: no cover — C-level signal handler` and add to the coverage config. |
| Line is covered but behavior not asserted | **Add the assertion.** Coverage without assertion is not coverage. |

---

## pytest configuration patterns

### conftest.py — isolation and shared fixtures

```python
# tests/conftest.py
import pytest
import tempfile
import os
from pathlib import Path

@pytest.fixture
def tmp_workspace():
    """Create an isolated workspace that mimics the gludd project structure."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src" / "general_ludd").mkdir(parents=True)
        (root / "tests" / "unit").mkdir(parents=True)
        (root / ".opencode" / "plugin").mkdir(parents=True)
        yield root


@pytest.fixture
def tmp_git_repo(tmp_workspace):
    """Create an isolated git repository with one initial commit."""
    import subprocess
    repo = tmp_workspace / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    (repo / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        check=True, capture_output=True,
    )
    return repo


@pytest.fixture
def in_memory_db():
    """Create an in-memory SQLite database with all tables."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from general_ludd.db.models import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
```

### Mocking patterns

```python
# tests/unit/test_with_mocks.py
from unittest.mock import MagicMock, patch
import pytest

# Pattern A: Inject the mock via constructor — preferred, no monkeypatching
def test_dispatcher_with_injected_worker():
    mock_worker = MagicMock()
    mock_worker.execute.return_value = {"status": "ok"}
    dispatcher = Dispatcher(worker=mock_worker)

    result = dispatcher.dispatch(TodoModel(task="fix bug"))

    assert result["status"] == "ok"
    mock_worker.execute.assert_called_once()


# Pattern B: patch the dependency at import location — use for global singletons
def test_rate_limiter_uses_redis():
    with patch("general_ludd.limiter.redis_client") as mock_redis:
        mock_redis.incr.return_value = 1
        limiter = RateLimiter(max_per_minute=10)

        allowed = limiter.check("agent-1")

        assert allowed is True
        mock_redis.incr.assert_called_once_with("rate:agent-1")


# Pattern C: pytest-mock fixture — cleaner syntax
def test_cache_miss_triggers_recompute(mocker):
    mocker.patch("general_ludd.cache.redis_get", return_value=None)
    mock_recompute = mocker.patch("general_ludd.cache.recompute", return_value="fresh")

    result = get_or_compute("key-1")

    assert result == "fresh"
    mock_recompute.assert_called_once_with("key-1")
```

---

## Anti-Pattern Gallery

### AP-1: Testing randomness with no seed

```python
# BAD — non-deterministic: test may pass or fail randomly
def test_generate_id_is_unique():
    id1 = generate_id()
    id2 = generate_id()
    assert id1 != id2
```

```python
# GOOD — deterministic: seed controls randomness, test always passes/fails consistently
def test_generate_id_produces_different_values_with_different_seeds():
    id1 = generate_id(seed=42)
    id2 = generate_id(seed=43)
    assert id1 != id2
```

### AP-2: Testing time with real clock

```python
# BAD — flaky: test depends on wall-clock time, breaks near midnight
def test_is_expired():
    token = create_token(expires_at=datetime.now() + timedelta(seconds=1))
    time.sleep(1.1)
    assert is_expired(token) is True
```

```python
# GOOD — deterministic: freeze time, no sleep needed
from freezegun import freeze_time

@freeze_time("2026-07-25T12:00:00")
def test_is_expired_when_expiry_passed():
    token = create_token(expires_at=datetime(2026, 7, 25, 11, 59, 59))
    assert is_expired(token) is True

@freeze_time("2026-07-25T12:00:00")
def test_is_not_expired_when_expiry_in_future():
    token = create_token(expires_at=datetime(2026, 7, 25, 12, 0, 1))
    assert is_expired(token) is False
```

### AP-3: Shared mutable state between tests

```python
# BAD — test pollution: test B fails because test A modified global state
_global_cache: dict[str, str] = {}

def test_a_caches_value():
    _global_cache["key"] = "value"
    assert _global_cache["key"] == "value"

def test_b_expects_empty_cache():
    assert len(_global_cache) == 0  # FAILS — test_a left "key" in the cache
```

```python
# GOOD — isolation: each test gets its own cache instance
@pytest.fixture
def cache():
    return {}

def test_a_caches_value(cache):
    cache["key"] = "value"
    assert cache["key"] == "value"

def test_b_expects_empty_cache(cache):
    assert len(cache) == 0  # PASSES — fixture gives a fresh dict each time
```

### AP-4: Testing implementation detail, not behavior

```python
# BAD — asserts internal variable name, refactoring breaks the test
def test_parse_uses_correct_variable_name():
    # Don't do this. The test should not know about `_buffer` internals.
    assert "_buffer" in open("src/general_ludd/parse.py").read()
```

```python
# GOOD — asserts observable output, survives refactoring
def test_parse_handles_utf8_input():
    result = parse(b"caf\xc3\xa9")
    assert result == "café"
```

### AP-5: Over-mocking to the point of testing nothing

```python
# BAD — everything mocked: the test proves mock objects interact, nothing about the real system
def test_pipeline_over_mocked():
    mock_db = MagicMock()
    mock_validator = MagicMock()
    mock_dispatcher = MagicMock()
    mock_worker = MagicMock()
    mock_logger = MagicMock()

    pipeline = Pipeline(
        db=mock_db,
        validator=mock_validator,
        dispatcher=mock_dispatcher,
        worker=mock_worker,
        logger=mock_logger,
    )
    pipeline.run(TodoModel(task="fix bug"))

    mock_validator.validate.assert_called_once()
    mock_dispatcher.dispatch.assert_called_once()
    mock_worker.execute.assert_called_once()
    # Nothing verifies the pipeline output — just that mocks were poked
```

```python
# GOOD — only mock external boundaries, keep internal logic real
def test_pipeline_with_real_logic_and_mocked_boundaries():
    real_validator = TodoValidator()
    real_dispatcher = Dispatcher(worker=InMemoryWorker())
    mock_logger = MagicMock()

    pipeline = Pipeline(
        db=InMemoryDatabase(),
        validator=real_validator,
        dispatcher=real_dispatcher,
        logger=mock_logger,
    )

    result = pipeline.run(TodoModel(task="fix bug"))

    assert result.status == "running"
    assert mock_logger.info.call_count >= 1
```

### AP-6: No assertion at all

```python
# BAD — silently passes if code doesn't throw, proves nothing
def test_dispatcher_does_not_crash():
    dispatcher = Dispatcher(worker=InMemoryWorker())
    dispatcher.dispatch(TodoModel(task="fix bug"))
    # No assertion — if dispatch is a no-op, this test still passes
```

```python
# GOOD — asserts the actual output
def test_dispatcher_returns_job_id():
    dispatcher = Dispatcher(worker=InMemoryWorker())
    result = dispatcher.dispatch(TodoModel(task="fix bug"))
    assert result.job_id is not None
    assert len(result.job_id) > 0
```
