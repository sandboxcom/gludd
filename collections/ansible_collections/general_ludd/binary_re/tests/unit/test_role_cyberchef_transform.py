"""Tests for cyberchef_transform role — YAML structure, local transforms, output format."""

from __future__ import annotations

import base64
import codecs
import json
import os
import subprocess
import sys
import urllib.parse
import yaml
from pathlib import Path


COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_DIR = COLLECTION_ROOT / "roles" / "cyberchef_transform"
CYBERCHEF_SCRIPT = ROLE_DIR / "files" / "cyberchef.py"
TASKS_YML = ROLE_DIR / "tasks" / "main.yml"
DEFAULTS_YML = ROLE_DIR / "defaults" / "main.yml"
VARS_YML = ROLE_DIR / "vars" / "main.yml"
META_YML = ROLE_DIR / "meta" / "main.yml"


class TestRoleStructure:
    def test_task_file_is_valid_yaml(self):
        content = TASKS_YML.read_text(encoding="utf-8")
        assert content.strip()
        docs = list(yaml.safe_load_all(content))
        assert len(docs) >= 1

    def test_defaults_is_valid_yaml(self):
        data = yaml.safe_load(DEFAULTS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_vars_is_valid_yaml(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_meta_is_valid_yaml(self):
        data = yaml.safe_load(META_YML.read_text(encoding="utf-8"))
        assert data["galaxy_info"]["role_name"] == "cyberchef_transform"

    def test_script_exists(self):
        assert CYBERCHEF_SCRIPT.is_file()

    def test_tasks_include_key_steps(self):
        content = TASKS_YML.read_text(encoding="utf-8")
        assert "Validate input parameters" in content
        assert "Create output directory" in content
        assert "Run CyberChef transform" in content

    def test_defaults_define_required_vars(self):
        data = yaml.safe_load(DEFAULTS_YML.read_text(encoding="utf-8"))
        assert "cyberchef_url" in data
        assert "output_dir" in data
        assert "input_data" in data
        assert "recipe" in data
        assert "enable_api" in data

    def test_vars_define_script_path(self):
        data = yaml.safe_load(VARS_YML.read_text(encoding="utf-8"))
        assert "transform_script" in data


class TestScriptInvocation:
    def test_help_output(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "--input" in result.stdout
        assert "--recipe" in result.stdout
        assert "--enable-api" in result.stdout

    def test_list_recipes(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "dummy", "--recipe", "rot13", "--list-recipes"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        recipes = json.loads(result.stdout)
        assert "base64_decode" in recipes
        assert "hex_decode" in recipes
        assert "rot13" in recipes
        assert "xor" in recipes
        assert "url_decode" in recipes

    def test_missing_recipe_fails_gracefully(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "hello", "--recipe", "nonexistent_recipe"],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert "error" in output
        assert "available" in output
        assert len(output["available"]) >= 10


class TestBase64Transforms:
    def test_base64_decode(self):
        encoded = base64.b64encode(b"Hello World").decode()
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", encoded, "--recipe", "base64_decode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["output"] == "Hello World"
        assert output["backend"] == "local"

    def test_base64_encode(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hello World", "--recipe", "base64_encode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        decoded = base64.b64decode(output["output"]).decode()
        assert decoded == "Hello World"

    def test_base64_decode_invalid(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "!!!not-valid-base64!!!", "--recipe", "base64_decode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert "error" in output


class TestHexTransforms:
    def test_hex_decode(self):
        encoded = b"Hello World".hex()
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", encoded, "--recipe", "hex_decode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["output"] == "Hello World"

    def test_hex_encode(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hello World", "--recipe", "hex_encode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        decoded = bytes.fromhex(output["output"]).decode()
        assert decoded == "Hello World"


class TestRot13Transform:
    def test_rot13(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hello World", "--recipe", "rot13"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["output"] == codecs.decode("Hello World", "rot_13")


class TestRot47Transform:
    def test_rot47_roundtrip(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hello World!", "--recipe", "rot47"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["output"] != "Hello World!"
        result2 = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", output["output"], "--recipe", "rot47"],
            capture_output=True, text=True, timeout=15,
        )
        output2 = json.loads(result2.stdout)
        assert output2["output"] == "Hello World!"


class TestFromCharcode:
    def test_from_charcode(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "72,101,108,108,111", "--recipe", "from_charcode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["output"] == "Hello"

    def test_to_charcode(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hello", "--recipe", "to_charcode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert "72" in output["output"]


class TestXOR:
    def test_xor_with_key(self):
        result = subprocess.run(
            [
                sys.executable, str(CYBERCHEF_SCRIPT),
                "--input", "test", "--recipe", "xor", "--key", "\x01",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert "error" not in output
        assert output["key_used"] == "\x01"

    def test_xor_no_key(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "test", "--recipe", "xor"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert "error" not in output
        assert "ERROR" in output["output"]

    def test_xor_bruteforce(self):
        plaintext = "secret message"
        key = 42
        ciphertext = "".join(chr(ord(c) ^ key) for c in plaintext)
        result = subprocess.run(
            [
                sys.executable, str(CYBERCHEF_SCRIPT),
                "--input", ciphertext, "--recipe", "xor_bruteforce",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        best = json.loads(output["output"])
        assert len(best) >= 1


class TestURLTransforms:
    def test_url_decode(self):
        encoded = urllib.parse.quote("Hello World")
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", encoded, "--recipe", "url_decode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["output"] == "Hello World"

    def test_url_encode(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hello World", "--recipe", "url_encode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        decoded = urllib.parse.unquote(output["output"])
        assert decoded == "Hello World"


class TestBinaryTransforms:
    def test_binary_decode(self):
        result = subprocess.run(
            [
                sys.executable, str(CYBERCHEF_SCRIPT),
                "--input", "01001000 01101001", "--recipe", "binary_decode",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert "Hi" in output["output"]

    def test_binary_encode(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hi", "--recipe", "binary_encode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert "01001000" in output["output"]


class TestBase32Transforms:
    def test_base32_decode(self):
        encoded = base64.b32encode(b"HELLO").decode()
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", encoded, "--recipe", "base32_decode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["output"] == "HELLO"

    def test_base32_encode(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "HELLO", "--recipe", "base32_encode"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        decoded = base64.b32decode(output["output"]).decode()
        assert decoded == "HELLO"


class TestUtilityTransforms:
    def test_reverse(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "abc123", "--recipe", "reverse"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["output"] == "321cba"

    def test_strip_html(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "<p>Hello <b>World</b></p>", "--recipe", "strip_html"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["output"] == "Hello World"


class TestArtifactFormat:
    def test_output_has_required_fields(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hello", "--recipe", "rot13"],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert "input" in output
        assert "recipe" in output
        assert "output" in output
        assert "output_length" in output
        assert "backend" in output
        assert output["recipe"] == "rot13"

    def test_output_length_is_int(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hello", "--recipe", "rot13"],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert isinstance(output["output_length"], int)


class TestAPIEnableFlag:
    def test_local_mode_default(self):
        result = subprocess.run(
            [sys.executable, str(CYBERCHEF_SCRIPT), "--input", "Hi", "--recipe", "rot13"],
            capture_output=True, text=True, timeout=15,
        )
        output = json.loads(result.stdout)
        assert output["backend"] == "local"

    def test_api_mode_handles_connection_error(self):
        result = subprocess.run(
            [
                sys.executable, str(CYBERCHEF_SCRIPT),
                "--input", "Hi", "--recipe", "rot13",
                "--enable-api", "--api-url", "http://127.0.0.1:19999",
            ],
            capture_output=True, text=True, timeout=10,
        )
        output = json.loads(result.stdout)
        assert "error" in output
