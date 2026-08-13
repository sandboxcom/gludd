from pathlib import Path

LIVE_OPENCODE_E2E_FILES = (
    Path("tests/e2e/test_opencode_binary_boot.py"),
    Path("tests/e2e/test_opencode_boot_e2e.py"),
    Path("tests/e2e/test_opencode_enforce_make.py"),
    Path("tests/e2e/test_opencode_resolved_permissions.py"),
    Path("tests/e2e/test_opencode_tui_permissions.py"),
)


def test_live_opencode_e2e_files_share_one_xdist_group() -> None:
    marker = 'pytest.mark.xdist_group("opencode-live")'

    missing = [
        str(path)
        for path in LIVE_OPENCODE_E2E_FILES
        if marker not in path.read_text(encoding="utf-8")
    ]

    assert missing == [], (
        "real OpenCode subprocess/TUI E2Es must serialize under loadgroup; "
        f"missing marker: {missing}"
    )
