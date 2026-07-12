"""Validate Makefile syntax — catches TAB vs space errors and missing separators."""
import subprocess
import sys
from pathlib import Path

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def test_makefile_parses():
    """make -n on a no-op target must exit 0 (no syntax errors)."""
    result = subprocess.run(
        ["make", "-n", "-f", str(MAKEFILE), "help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Makefile has syntax errors:\n{result.stderr[-500:]}"
    )


def test_makefile_no_tabs_in_phony():
    """.PHONY continuation lines must use spaces, not tabs."""
    content = MAKEFILE.read_text()
    in_phony = False
    for i, line in enumerate(content.split("\n"), 1):
        if line.strip().startswith(".PHONY:"):
            in_phony = True
            continue
        if in_phony:
            if line.rstrip("\n").endswith("\\"):
                assert "\t" not in line.lstrip("\n"), (
                    f"Makefile:{i}: TAB in .PHONY continuation — use spaces only"
                )
            else:
                in_phony = False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
