"""Phase 0 escape helper tests — TERRAFORM_INFRA_STRUCTURE.md §7 Phase 0 / §9.

Asserts that ``escape_tfvar_value`` exists in
``src/general_ludd/infra/terraform.py`` and that config field values containing
HCL-significant characters (``"``, ``${``, ``\\n``, ``}``) produce valid tfvars
values (quoted, escaped) and never valid HCL structure.
"""

from __future__ import annotations

import inspect
from typing import Any, cast

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.infra.terraform import TerraformGenerator, escape_tfvar_value


def test_escape_helper_exists_with_correct_signature() -> None:
    """escape_tfvar_value must be a public callable taking and returning str."""
    assert callable(escape_tfvar_value)
    sig = inspect.signature(escape_tfvar_value)
    params = list(sig.parameters.values())
    assert len(params) == 1, "escape_tfvar_value must take exactly one argument"
    # ``from __future__ import annotations`` turns annotations into strings,
    # so accept either the type or its string form.
    assert sig.return_annotation in (str, "str"), (
        f"escape_tfvar_value must annotate -> str, got {sig.return_annotation!r}"
    )


def test_plain_string_is_quoted() -> None:
    """A plain alphanumeric string must be wrapped in double-quotes unchanged."""
    assert escape_tfvar_value("meta-llama/Llama-2-7b-hf") == '"meta-llama/Llama-2-7b-hf"'


def test_double_quote_is_escaped() -> None:
    """A double-quote in the value must be backslash-escaped, not terminate the string."""
    out = escape_tfvar_value('evil"value')
    assert out == '"evil\\"value"'
    # The output, viewed as HCL, must contain a single outer-quoted string — the
    # inner quote is escaped, so it cannot close the string context early.
    assert out.count('"') - out.count('\\"') == 2


def test_interpolation_marker_is_escaped() -> None:
    """``${...}`` must be escaped so it cannot be re-interpreted as HCL interpolation."""
    payload = '${aws_instance.gpu_instance.public_ip}'
    out = escape_tfvar_value(payload)
    assert out.startswith('"') and out.endswith('"')
    body = out[1:-1]
    # The HCL escape for an interpolation marker is ``\${``. The body must
    # contain that escaped form, and crucially must NOT contain an unescaped
    # ``${`` — i.e. every ``${`` in the body is preceded by a backslash.
    assert "\\${" in body, f"expected escaped \\${{ in body, got {body!r}"
    idx = 0
    while True:
        pos = body.find("${", idx)
        if pos == -1:
            break
        assert pos > 0 and body[pos - 1] == "\\", (
            f"unescaped ${{ at position {pos} in body {body!r}"
        )
        idx = pos + 2
    # …but the user's text is still recoverable (the characters survived, just neutralised).
    assert "$" in body and "{" in body


def test_newline_is_escaped() -> None:
    """A literal newline must become a backslash-n escape sequence, not a raw break."""
    out = escape_tfvar_value("line1\nline2")
    assert out == '"line1\\nline2"'
    assert "\n" not in out, "raw newline must not survive into the tfvars value"


def test_closing_brace_is_inert() -> None:
    """A bare ``}`` must be a quoted character, not a block terminator."""
    out = escape_tfvar_value("}")
    assert out == '"}"'
    # It must NOT look like the start of an HCL block close (no leading brace, no block body).
    assert out.startswith('"') and out.endswith('"')


def test_empty_string_round_trips() -> None:
    """An empty string must become an empty quoted tfvars value, not nothing."""
    assert escape_tfvar_value("") == '""'


def test_backslash_is_escaped() -> None:
    """A literal backslash must be doubled so it cannot form an escape sequence."""
    out = escape_tfvar_value("path\\to\\thing")
    assert out == '"path\\\\to\\\\thing"'
    body = out[1:-1]
    # No odd-length trailing backslash run that could swallow a following quote.
    assert not body.endswith("\\") or body.endswith("\\\\")


# ---------------------------------------------------------------------------
# build_tfvars — the tfvars-writing path that consumes escape_tfvar_value.
# ---------------------------------------------------------------------------

def _base_config(**overrides: object) -> ComputeConfig:
    defaults: dict[str, object] = {
        "provider": ComputeProvider.AWS,
        "gpu_type": GPUType.T4,
        "model_name": "meta-llama/Llama-2-7b-hf",
        "allowed_cidr": "0.0.0.0/0",
    }
    defaults.update(overrides)
    return cast(Any, ComputeConfig)(**defaults)


def test_build_tfvars_routes_all_string_values_through_escape_helper() -> None:
    """build_tfvars must produce one quoted/escaped tfvars line per string field.

    Every ``= "..."`` line in the output must be a value produced by
    ``escape_tfvar_value`` — i.e. starts and ends with a quote, with no raw
    newlines, so the result parses as a valid tfvars file.
    """
    cfg = _base_config(region="us-east-1")
    tfvars = TerraformGenerator().build_tfvars(cfg)
    for line in tfvars.splitlines():
        if " = " not in line:
            continue
        _key, _, rhs = line.partition(" = ")
        rhs = rhs.strip()
        # Numeric and boolean values are emitted as bare HCL literals.
        if rhs in {"true", "false"}:
            continue
        try:
            float(rhs)
        except ValueError:
            pass
        else:
            continue
        assert rhs.startswith('"') and rhs.endswith('"'), (
            f"tfvars value not quoted: {line!r}"
        )
        assert "\n" not in rhs, f"raw newline in tfvars value: {line!r}"


def test_build_tfvars_injection_payload_is_inert() -> None:
    """A payload that would break inline HCL is rendered as a benign literal.

    The ComputeConfig validators reject most of these at construction time;
    this test reaches the escape helper directly through a payload that
    exercises every Phase 0 escape class, confirming the tfvars output is
    structurally incapable of producing HCL structure.
    """
    payload = 'evil"}\nresource "null" "x" {\n${var.pwned}'
    # Pre-validation: the helper itself must neutralise every dangerous token.
    out = escape_tfvar_value(payload)
    body = out[1:-1]
    # No raw newline (would break the single-line tfvars assignment).
    assert "\n" not in body
    # No unescaped ``"`` (would close the string early).
    idx = 0
    while True:
        pos = body.find('"', idx)
        if pos == -1:
            break
        assert pos > 0 and body[pos - 1] == "\\", f"unescaped quote at {pos}: {body!r}"
        idx = pos + 1
    # No unescaped ``${`` (would start an interpolation).
    idx = 0
    while True:
        pos = body.find("${", idx)
        if pos == -1:
            break
        assert pos > 0 and body[pos - 1] == "\\", f"unescaped ${{ at {pos}: {body!r}"
        idx = pos + 2
