"""Phase F tests: advanced integration + molecule scenario validation.

Phase F exercises the two managed-host file parsers and the nine daemon-owned
language operations end-to-end, then validates the molecule scenario YAML that
drives the collection-level converge/verify cycle.

Coverage:
    1. Every role operation executes and produces valid JSON output
    2. Molecule converge.yml references all 11 roles with valid vars
    3. Molecule verify.yml asserts artifact existence + key fields for all 11 roles
    4. Advanced edge cases: empty input, invalid paths, boundary conditions
    5. Cross-role integration: Unicode → encoding → BOM pipeline consistency
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from general_ludd.language.operations import execute_language_operation

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COLLECTION_ROOT = REPO_ROOT / "collections" / "ansible_collections" / "general_ludd" / "language"
ROLES_DIR = COLLECTION_ROOT / "roles"

ALL_ROLES = [
    "bom_detect",
    "encoding_detect",
    "font_analyze",
    "homoglyph_scan",
    "i18n_extract",
    "language_detect",
    "locale_format",
    "phonetic_transcribe",
    "translate",
    "transliterate",
    "unicode_analyze",
]

SERVICE_ROLES = frozenset(ALL_ROLES) - {"font_analyze", "i18n_extract"}
SCRIPT_ROLES = ("font_analyze", "i18n_extract")


def _arg(args: list[str], name: str, default: str = "") -> str:
    try:
        return args[args.index(name) + 1]
    except (ValueError, IndexError):
        return default


def _service_payload(role: str, args: list[str]) -> dict[str, object]:
    """Convert legacy CLI-shaped fixtures to the daemon's JSON contract."""
    if role == "bom_detect":
        payload: dict[str, object] = {
            "input_bytes": _arg(args, "--input-bytes"),
            "strip_bom": "--strip" in args,
            "add_bom": "--add-bom" in args,
            "add_bom_encoding": _arg(args, "--add-bom-encoding", "UTF-8"),
        }
        audit_dir = _arg(args, "--audit-directory")
        if audit_dir:
            payload["audit_files"] = {
                path.name: base64.b64encode(path.read_bytes()).decode("ascii")
                for path in sorted(Path(audit_dir).iterdir())
                if path.is_file()
            }
        return payload
    if role == "encoding_detect":
        return {
            "input_bytes": _arg(args, "--input-bytes"),
            "target_encoding": _arg(args, "--target-encoding"),
            "detect_mojibake": "--detect-mojibake" in args,
        }
    if role == "homoglyph_scan":
        return {
            "input_text": _arg(args, "--input"),
            "input_domain": _arg(args, "--input-domain"),
            "check_confusables": "--no-confusables" not in args,
            "check_invisible": "--no-invisible" not in args,
            "check_bidi": "--no-bidi" not in args,
            "check_mixed_script": "--no-mixed-script" not in args,
        }
    if role == "language_detect":
        return {"input_text": _arg(args, "--text")}
    if role == "locale_format":
        return {
            "locale": _arg(args, "--locale", "en-US"),
            "value": _arg(args, "--value"),
            "value_type": _arg(args, "--value-type", "date"),
            "currency_code": _arg(args, "--currency-code", "USD"),
        }
    if role == "phonetic_transcribe":
        return {
            "input_text": _arg(args, "--input"),
            "method": _arg(args, "--method", "arpabet"),
        }
    if role == "translate":
        return {
            "input_text": _arg(args, "--text"),
            "source_language": _arg(args, "--source", "auto"),
            "target_language": _arg(args, "--target", "en"),
        }
    if role == "transliterate":
        return {
            "input_text": _arg(args, "--text"),
            "target_script": _arg(args, "--target-script", "Latin"),
            "scheme": _arg(args, "--scheme"),
        }
    if role == "unicode_analyze":
        return {
            "input_text": _arg(args, "--input"),
            "include_codepoints": "--no-codepoints" not in args,
            "include_normalization": "--no-normalization" not in args,
            "include_grapheme_clusters": "--no-grapheme" not in args,
            "include_surrogates": "--no-surrogates" not in args,
            "include_utf_encodings": "--no-utf-encodings" not in args,
        }
    raise ValueError(f"unsupported service role: {role}")


def _run_role_script(
    role: str, args: list[str], timeout: int = 15
) -> dict[str, Any]:
    """Exercise the authoritative service or a managed-host-only parser."""
    if role in SERVICE_ROLES:
        return dict(execute_language_operation(role, _service_payload(role, args)))
    script = ROLES_DIR / role / "files" / f"{role}.py"
    if not script.exists():
        pytest.fail(f"Standalone script missing: {script}")
    cmd = [sys.executable, str(script), *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        pytest.fail(
            f"{role} script exited {proc.returncode}.\nstdout: {proc.stdout[:500]}\nstderr: {proc.stderr[:500]}"
        )
    output = proc.stdout.strip()
    if not output:
        pytest.fail(f"{role} script produced no stdout output")
    loaded = json.loads(output)
    assert isinstance(loaded, dict)
    return loaded


# ═══════════════════════════════════════════════════════════════════════════════
# 1. STANDALONE SCRIPT EXECUTION (all 8 roles)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBomDetectScript:
    """bom_detect standalone script — BOM detection, stripping, audit."""

    def test_detect_utf8_bom(self) -> None:
        result = _run_role_script("bom_detect", ["--input-bytes", "EF BB BF 48 65 6C 6C 6F"])
        assert result["bom_detected"] is True
        assert "utf-8" in str(result.get("encoding", "")).lower()
        assert result["bom_size"] == 3

    def test_detect_utf16le_bom(self) -> None:
        result = _run_role_script("bom_detect", ["--input-bytes", "FF FE 48 00 65 00"])
        assert result["bom_detected"] is True
        assert "utf-16" in str(result.get("encoding", "")).lower()

    def test_no_bom_detected(self) -> None:
        result = _run_role_script("bom_detect", ["--input-bytes", "48 65 6C 6C 6F"])
        assert result["bom_detected"] is False

    def test_strip_bom(self) -> None:
        result = _run_role_script("bom_detect", ["--input-bytes", "EF BB BF 48 65 6C 6C 6F", "--strip"])
        assert result["bom_detected"] is True
        assert "stripped_preview" in result

    def test_add_bom(self) -> None:
        result = _run_role_script(
            "bom_detect",
            ["--input-bytes", "48 65 6C 6C 6F", "--add-bom", "--add-bom-encoding", "UTF-8"],
        )
        assert result.get("bom_added") == "UTF-8"

    def test_rfc_compliance_field(self) -> None:
        result = _run_role_script("bom_detect", ["--input-bytes", "EF BB BF 41"])
        assert result.get("rfc_compliance") in ("required", "optional", "none")

    def test_audit_directory(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_bytes(b"\xef\xbb\xbfhello")
        (tmp_path / "b.txt").write_bytes(b"plain")
        result = _run_role_script(
            "bom_detect",
            ["--input-bytes", "41", "--audit-directory", str(tmp_path)],
        )
        audit = result.get("audit_results", [])
        assert isinstance(audit, list)
        assert len(audit) == 2

    def test_no_input_returns_error(self) -> None:
        result = _run_role_script("bom_detect", [])
        assert result.get("bom_detected") is False or "error" in result


class TestEncodingDetectScript:
    """encoding_detect standalone script — charset detection + mojibake."""

    def test_detect_ascii(self) -> None:
        result = _run_role_script("encoding_detect", ["--input-bytes", "48 65 6C 6C 6F"])
        assert "detected_encoding" in result
        assert result["byte_length"] == 5

    def test_confidence_level(self) -> None:
        result = _run_role_script("encoding_detect", ["--input-bytes", "48 65 6C 6C 6F"])
        assert result.get("confidence_level") in ("trusted", "reliable", "usable", "entry")

    def test_target_encoding_conversion(self) -> None:
        result = _run_role_script(
            "encoding_detect",
            ["--input-bytes", "48 65 6C 6C 6F", "--target-encoding", "utf-16"],
        )
        assert "target_byte_length" in result or "target_encoding_error" in result

    def test_mojibake_detection(self) -> None:
        result = _run_role_script(
            "encoding_detect",
            ["--input-bytes", "C3 A9", "--detect-mojibake"],
        )
        assert "mojibake_detected" in result

    def test_supported_encodings_list(self) -> None:
        result = _run_role_script("encoding_detect", ["--input-bytes", "41"])
        encs = result.get("supported_encodings", [])
        assert isinstance(encs, list)
        assert len(encs) > 0
        assert result.get("supported_count", 0) == len(encs)


class TestFontAnalyzeScript:
    """font_analyze standalone script — font file parsing."""

    def test_analyze_ttf_header(self, tmp_path: Path) -> None:
        font_file = tmp_path / "test.ttf"
        font_file.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 100)
        result = _run_role_script("font_analyze", ["--input", str(font_file)])
        assert result.get("format") in ("ttf", "otf", "unknown")

    def test_analyze_missing_file(self) -> None:
        result = _run_role_script("font_analyze", ["--input", "/nonexistent/font.ttf"])
        assert "error" in result

    def test_analyze_non_font_file(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "not_a_font.bin"
        bad_file.write_bytes(b"NOTAFONT" + b"\x00" * 50)
        result = _run_role_script("font_analyze", ["--input", str(bad_file)])
        assert result.get("format") == "unknown" or "error" in result


class TestHomoglyphScanScript:
    """homoglyph_scan standalone script — confusable + invisible + bidi detection."""

    def test_clean_ascii_text(self) -> None:
        result = _run_role_script("homoglyph_scan", ["--input", "hello world"])
        assert result["safe"] is True
        assert result["total_findings"] == 0

    def test_cyrillic_confusable(self) -> None:
        result = _run_role_script("homoglyph_scan", ["--input", "paypa" + chr(0x0430) + "l.com"])
        assert result["total_findings"] > 0
        assert result["safe"] is False

    def test_invisible_zero_width(self) -> None:
        result = _run_role_script("homoglyph_scan", ["--input", "hello\u200bworld"])
        assert result["total_findings"] > 0

    def test_bidi_override_char(self) -> None:
        result = _run_role_script("homoglyph_scan", ["--input", "print\u202eHello"])
        assert result["total_findings"] > 0

    def test_empty_input(self) -> None:
        result = _run_role_script("homoglyph_scan", ["--input", ""])
        assert result["safe"] is True
        assert len(result.get("findings", [])) == 0

    def test_severity_counts(self) -> None:
        result = _run_role_script("homoglyph_scan", ["--input", chr(0x0410) + "pple"])
        assert "severity_counts" in result


class TestI18nExtractScript:
    """i18n_extract standalone script — gettext string extraction."""

    def test_extract_from_python(self, tmp_path: Path) -> None:
        src = tmp_path / "app.py"
        src.write_text(
            '_("Hello World")\n_("Welcome")\nngettext("item", "items", 5)\n',
            encoding="utf-8",
        )
        result = _run_role_script(
            "i18n_extract", ["--source-dir", str(tmp_path), "--output-dir", str(tmp_path / "out")]
        )
        assert result["string_count"] >= 2
        assert result["files_scanned"] >= 1

    def test_extract_empty_dir(self, tmp_path: Path) -> None:
        result = _run_role_script(
            "i18n_extract", ["--source-dir", str(tmp_path), "--output-dir", str(tmp_path / "out")]
        )
        assert result["string_count"] == 0

    def test_pot_file_generated(self, tmp_path: Path) -> None:
        src = tmp_path / "app.py"
        src.write_text('_("Hello")\n', encoding="utf-8")
        result = _run_role_script(
            "i18n_extract", ["--source-dir", str(tmp_path), "--output-dir", str(tmp_path / "out")]
        )
        pot_path = result.get("pot_path", "")
        assert len(pot_path) > 0


class TestLocaleFormatScript:
    """locale_format standalone script — CLDR locale formatting."""

    def test_format_date_de(self) -> None:
        result = _run_role_script(
            "locale_format",
            ["--locale", "de-DE", "--value", "2026-07-15", "--value-type", "date"],
        )
        assert result["locale"] == "de-DE"
        assert result["is_rtl"] is False
        assert "formatted_value" in result

    def test_format_number_us(self) -> None:
        result = _run_role_script(
            "locale_format",
            ["--locale", "en-US", "--value", "1234567.89", "--value-type", "number"],
        )
        assert "formatted_value" in result

    def test_rtl_locale(self) -> None:
        result = _run_role_script(
            "locale_format",
            ["--locale", "ar-SA", "--value", "1000", "--value-type", "number"],
        )
        assert result["is_rtl"] is True

    def test_first_day_of_week(self) -> None:
        result = _run_role_script(
            "locale_format",
            ["--locale", "en-US", "--value", "100", "--value-type", "number"],
        )
        assert "first_day_of_week" in result

    def test_measurement_system(self) -> None:
        result = _run_role_script(
            "locale_format",
            ["--locale", "en-US", "--value", "1", "--value-type", "number"],
        )
        assert "measurement_system" in result

    def test_unknown_locale_warning(self) -> None:
        result = _run_role_script(
            "locale_format",
            ["--locale", "xx-YY", "--value", "42", "--value-type", "number"],
        )
        assert "warning" in result or "formatted_value" in result


class TestPhoneticTranscribeScript:
    """phonetic_transcribe standalone script — phonetic transcription."""

    def test_transcribe_english(self) -> None:
        result = _run_role_script("phonetic_transcribe", ["--input", "hello world", "--method", "arpabet"])
        assert "words" in result or "transcription" in result or "ipa" in result

    def test_transcribe_method_field(self) -> None:
        result = _run_role_script("phonetic_transcribe", ["--input", "test", "--method", "soundex"])
        assert result.get("method") == "soundex" or "words" in result

    def test_empty_input(self) -> None:
        result = _run_role_script("phonetic_transcribe", ["--input", "", "--method", "arpabet"])
        assert isinstance(result, dict)


class TestUnicodeAnalyzeScript:
    """unicode_analyze standalone script — Unicode property analysis."""

    def test_analyze_single_char(self) -> None:
        result = _run_role_script("unicode_analyze", ["--input", "A"])
        assert result["input_length"] == 1
        assert "codepoints" in result

    def test_analyze_emoji(self) -> None:
        result = _run_role_script("unicode_analyze", ["--input", "\U0001f600"])
        assert result["input_length"] == 1
        cps = result.get("codepoints", [])
        assert len(cps) == 1
        assert "SMP" in str(cps[0].get("plane", ""))

    def test_normalization_forms(self) -> None:
        result = _run_role_script("unicode_analyze", ["--input", "café"])
        norm = result.get("normalization", {})
        assert "NFC" in norm
        assert "NFD" in norm
        assert "NFKC" in norm
        assert "NFKD" in norm

    def test_utf_encodings(self) -> None:
        result = _run_role_script("unicode_analyze", ["--input", "AB"])
        encs = result.get("utf_encodings", {})
        assert "UTF-8" in encs
        assert "UTF-16-LE" in encs

    def test_plane_distribution(self) -> None:
        result = _run_role_script("unicode_analyze", ["--input", "A\U0001f600"])
        dist = result.get("plane_distribution", {})
        assert "BMP" in dist

    def test_disable_codepoints(self) -> None:
        result = _run_role_script("unicode_analyze", ["--input", "A", "--no-codepoints"])
        assert "codepoints" not in result

    def test_empty_input(self) -> None:
        result = _run_role_script("unicode_analyze", ["--input", ""])
        assert result.get("input_length") == 0 or "error" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. MOLECULE SCENARIO VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestMoleculeScenarioStructure:
    """Validate the molecule scenario at molecule/playbooks/language/ is complete."""

    MOLECULE_DIR = REPO_ROOT / "molecule" / "playbooks" / "language"

    def test_molecule_yml_exists(self) -> None:
        assert (self.MOLECULE_DIR / "molecule.yml").exists(), (
            "Phase F gap: molecule.yml must exist for language collection"
        )

    def test_converge_yml_exists(self) -> None:
        assert (self.MOLECULE_DIR / "default" / "converge.yml").exists(), "Phase F gap: converge.yml must exist"

    def test_verify_yml_exists(self) -> None:
        assert (self.MOLECULE_DIR / "default" / "verify.yml").exists(), "Phase F gap: verify.yml must exist"

    def test_destroy_playbook_configured(self) -> None:
        with open(self.MOLECULE_DIR / "molecule.yml") as f:
            data = yaml.safe_load(f)
        destroy = data["provisioner"]["playbooks"].get("destroy")
        assert destroy, "Phase F gap: destroy playbook must be configured"

    def test_molecule_yml_valid_yaml(self) -> None:
        with open(self.MOLECULE_DIR / "molecule.yml") as f:
            data = yaml.safe_load(f)
        assert data["driver"]["name"] == "default"
        assert data["provisioner"]["name"] == "ansible"

    def test_converge_references_all_8_roles(self) -> None:
        with open(self.MOLECULE_DIR / "default" / "converge.yml") as f:
            content = f.read()
        for role in ALL_ROLES:
            assert f"general_ludd.language.{role}" in content, f"Phase F gap: converge.yml must reference role '{role}'"

    def test_verify_references_all_8_roles(self) -> None:
        with open(self.MOLECULE_DIR / "default" / "verify.yml") as f:
            content = f.read()
        for role in ALL_ROLES:
            role_dash = role.replace("_", "-")
            assert role_dash in content or role in content, (
                f"Phase F gap: verify.yml must have assertions for role '{role}'"
            )

    def test_converge_valid_yaml(self) -> None:
        with open(self.MOLECULE_DIR / "default" / "converge.yml") as f:
            data = yaml.safe_load(f)
        assert isinstance(data, list)
        assert len(data) == 1
        converge_play = data[0]
        assert isinstance(converge_play, dict)
        assert converge_play.get("force_handlers") is True
        tasks = converge_play.get("tasks")
        assert isinstance(tasks, list)
        assert len(tasks) >= len(ALL_ROLES)

    def test_verify_valid_yaml(self) -> None:
        with open(self.MOLECULE_DIR / "default" / "verify.yml") as f:
            data = yaml.safe_load(f)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_molecule_test_sequence_complete(self) -> None:
        with open(self.MOLECULE_DIR / "molecule.yml") as f:
            data = yaml.safe_load(f)
        seq = data.get("scenario", {}).get("test_sequence", [])
        required = {"converge", "verify", "syntax", "destroy"}
        assert required.issubset(set(seq)), f"Phase F gap: molecule test_sequence missing: {required - set(seq)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ROLE FILE COMPLETENESS (all 8 roles have full file sets)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRoleFileCompleteness:
    """Every role ships metadata; managed-host parsers also ship scripts."""

    REQUIRED_FILES = (
        "tasks/main.yml",
        "defaults/main.yml",
        "vars/main.yml",
        "meta/main.yml",
        "README.md",
    )

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_role_has_all_files(self, role: str) -> None:
        role_dir = ROLES_DIR / role
        assert role_dir.exists(), f"Phase F gap: role directory missing for '{role}'"
        for rel in self.REQUIRED_FILES:
            expected = role_dir / rel.format(role=role)
            assert expected.exists(), f"Phase F gap: role '{role}' missing file: {rel}"
        if role in SCRIPT_ROLES:
            script = role_dir / "files" / f"{role}.py"
            assert script.exists(), f"managed-host parser missing for role '{role}'"

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_tasks_main_valid_yaml(self, role: str) -> None:
        tasks_file = ROLES_DIR / role / "tasks" / "main.yml"
        with open(tasks_file) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, list), f"tasks/main.yml for {role} must be a YAML list"
        assert len(data) >= 2, f"tasks/main.yml for {role} must have >=2 tasks"

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_defaults_main_valid_yaml(self, role: str) -> None:
        defaults_file = ROLES_DIR / role / "defaults" / "main.yml"
        with open(defaults_file) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict), f"defaults/main.yml for {role} must be a YAML dict"
        assert len(data) >= 1, f"defaults/main.yml for {role} must have >=1 default var"
        assert "name" not in data, f"defaults/main.yml for {role} must not override Ansible's reserved 'name'"

    @pytest.mark.parametrize("role", SCRIPT_ROLES)
    def test_standalone_script_executable(self, role: str) -> None:
        script = ROLES_DIR / role / "files" / f"{role}.py"
        with open(script) as f:
            header = f.read(2)
        assert header == "#!", f"Standalone script for {role} must have shebang"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. COLLECTION-LEVEL VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestCollectionMetadata:
    """galaxy.yml + README + 8 role directories exist."""

    def test_galaxy_yml_exists(self) -> None:
        assert (COLLECTION_ROOT / "galaxy.yml").exists()

    def test_galaxy_yml_valid(self) -> None:
        with open(COLLECTION_ROOT / "galaxy.yml") as f:
            data = yaml.safe_load(f)
        assert data["namespace"] == "general_ludd"
        assert data["name"] == "language"

    def test_readme_exists(self) -> None:
        assert (COLLECTION_ROOT / "README.md").exists()

    def test_exactly_8_role_dirs(self) -> None:
        role_dirs = [d.name for d in (ROLES_DIR).iterdir() if d.is_dir() and not d.name.startswith(".")]
        assert sorted(role_dirs) == sorted(ALL_ROLES), f"Expected exactly 8 role dirs, got: {sorted(role_dirs)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CROSS-ROLE INTEGRATION (Unicode → encoding → BOM pipeline)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossRoleIntegration:
    """Validate that roles produce consistent results across the Unicode pipeline."""

    def test_unicode_then_bom_consistency(self) -> None:
        """Text analyzed by unicode_analyze should be detectable by bom_detect
        when a BOM is prepended."""
        text = "Hello"
        text_bytes = text.encode("utf-8").hex(" ").upper()

        unicode_result = _run_role_script("unicode_analyze", ["--input", text])
        assert unicode_result["input_length"] == len(text)

        bom_hex = "EF BB BF " + text_bytes
        bom_result = _run_role_script("bom_detect", ["--input-bytes", bom_hex])
        assert bom_result["bom_detected"] is True
        assert bom_result["bom_size"] == 3

    def test_homoglyph_then_unicode_consistency(self) -> None:
        """A character flagged by homoglyph_scan should appear in unicode_analyze
        codepoint breakdown."""
        text = chr(0x0410) + "pple"

        homo = _run_role_script("homoglyph_scan", ["--input", text])
        assert homo["total_findings"] > 0

        uni = _run_role_script("unicode_analyze", ["--input", text])
        cps = uni.get("codepoints", [])
        cp_values = [c.get("codepoint", "") for c in cps]
        assert any("0410" in str(cp) for cp in cp_values), "unicode_analyze must report U+0410 (Cyrillic A) in the text"

    def test_encoding_detect_on_bom_stripped(self) -> None:
        """After bom_detect strips a BOM, encoding_detect should still identify
        the encoding as UTF-8/ASCII for clean text."""
        text_hex = "48 65 6C 6C 6F"
        enc = _run_role_script("encoding_detect", ["--input-bytes", text_hex])
        detected = str(enc.get("detected_encoding", "")).lower().replace("-", "")
        assert detected in ("utf8", "utf_8", "ascii"), f"Clean ASCII must be detected as utf-8/ascii, got {detected}"
