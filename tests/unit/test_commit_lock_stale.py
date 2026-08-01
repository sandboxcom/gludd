"""BP.19: verify commit lock stale threshold is 2 minutes (120000 ms)."""
import ast
import pathlib
import re

PLUGIN_PATH = pathlib.Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-commit-lock.ts"


def test_stale_threshold_is_120000() -> None:
    """The STALE_THRESHOLD_MS constant must be 2 * 60 * 1000 (120000 ms)."""
    src = PLUGIN_PATH.read_text()
    m = re.search(r"STALE_THRESHOLD_MS\s*=\s*(.+)", src)
    assert m is not None, (
        "STALE_THRESHOLD_MS declaration not found in enforce-commit-lock.ts"
    )
    expr = m.group(1).split("//", 1)[0].strip().rstrip(";").strip()
    tree = ast.parse(expr, mode="eval")
    assert isinstance(tree.body, ast.BinOp)
    assert isinstance(tree.body.left, ast.BinOp)
    left_op = tree.body.left
    assert isinstance(left_op.left, ast.Constant) and left_op.left.value == 2
    assert isinstance(left_op.right, ast.Constant) and left_op.right.value == 60
    assert isinstance(tree.body.op, ast.Mult)
    assert isinstance(tree.body.right, ast.Constant) and tree.body.right.value == 1000
    assert 2 * 60 * 1000 == 120000
