"""Confined actual-Make and native-Ansible apt-input controls."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TARGET = "presentation-browser-apt-source"
MODE = "PRESENTATION_BROWSER_APT_VALIDATE_ONLY"


def _environment(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEOVERRIDES",
        MODE,
        "GLUDD_PRESENTATION_BROWSER_APT_VALIDATE_ONLY",
        "GITHUB_ACTIONS",
        "RUNNER_ENVIRONMENT",
        "RUNNER_OS",
        "ImageOS",
        "ANSIBLE_CONFIG",
        "ANSIBLE_COLLECTIONS_PATH",
        "ANSIBLE_COLLECTIONS_PATHS",
    ):
        env.pop(name, None)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update(
        HOME=str(home),
        ANSIBLE_HOME=str(home / "ansible"),
        ANSIBLE_LOCAL_TEMP=str(home / "ansible" / "local"),
        ANSIBLE_REMOTE_TEMP=str(home / "ansible" / "remote"),
        ANSIBLE_NOCOLOR="1",
        ANSIBLE_FORCE_COLOR="0",
        UV_NO_SYNC="1",
        UV_PYTHON_DOWNLOADS="never",
    )
    return env


def _spy(tmp_path: Path) -> tuple[Path, Path]:
    recorder = tmp_path / "native-argv.jsonl"
    spy = tmp_path / "uv-spy"
    spy.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        "with open(os.environ['APT_TEST_ARGV'], 'a', encoding='utf-8') as out:\n"
        "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "with open(os.environ['APT_TEST_HOME'], 'w', encoding='utf-8') as out:\n"
        "    out.write(os.environ.get('ANSIBLE_HOME', ''))\n"
        "raise SystemExit(int(os.environ.get('APT_TEST_STATUS', '0')))\n",
        encoding="utf-8",
    )
    spy.chmod(0o700)
    return spy, recorder


def _make(
    tmp_path: Path,
    *,
    mode: str | None = None,
    origin: str = "command",
    hosted: bool = False,
    child_status: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    env = _environment(tmp_path)
    spy, recorder = _spy(tmp_path)
    env.update(
        APT_TEST_ARGV=str(recorder),
        APT_TEST_STATUS=str(child_status),
        APT_TEST_HOME=str(tmp_path / "owned-ansible-home"),
    )
    if hosted:
        env.update(
            GITHUB_ACTIONS="true",
            RUNNER_ENVIRONMENT="github-hosted",
            RUNNER_OS="Linux",
            ImageOS="ubuntu24",
        )
    command = ["make", "--no-print-directory", TARGET, "UV=" + str(spy)]
    if mode is not None:
        if origin == "command":
            command.append(MODE + "=" + mode)
        else:
            env[MODE] = mode
            if origin == "environment-override":
                command.insert(1, "-e")
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    ), recorder


def _calls(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    calls: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value: object = json.loads(line)
        assert isinstance(value, list)
        assert all(isinstance(arg, str) for arg in value)
        calls.append([arg for arg in value if isinstance(arg, str)])
    return calls


def test_apt_default_is_an_offline_plan(tmp_path: Path) -> None:
    result, recorder = _make(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(recorder) == []
    assert "outcome=planned" in result.stderr
    assert "system_mutation=false" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr


@pytest.mark.parametrize("origin", ["command", "environment", "environment-override"])
def test_apt_mode_is_literal_at_real_make_startup(tmp_path: Path, origin: str) -> None:
    marker = tmp_path / "must-not-exist"
    payload = "$(shell printf unsafe > " + str(marker) + ")"
    result, recorder = _make(tmp_path, mode=payload, origin=origin)
    assert result.returncode == 2
    assert not marker.exists()
    assert _calls(recorder) == []
    assert "outcome=refused" in result.stderr
    assert "literal 0 or 1" in result.stderr


def test_apt_apply_refuses_nonhosted_before_child(tmp_path: Path) -> None:
    result, recorder = _make(tmp_path, mode="0")
    assert result.returncode == 2
    assert _calls(recorder) == []
    assert "outcome=refused" in result.stderr
    assert "github-hosted" in result.stderr


@pytest.mark.parametrize("child_status", [0, 17])
def test_apt_apply_preserves_native_child_and_exact_argv(
    tmp_path: Path,
    child_status: int,
) -> None:
    result, recorder = _make(
        tmp_path,
        mode="0",
        hosted=True,
        child_status=child_status,
    )
    assert result.returncode == (0 if child_status == 0 else 2), result.stdout + result.stderr
    calls = _calls(recorder)
    assert len(calls) == 1
    owned_home = Path((tmp_path / "owned-ansible-home").read_text(encoding="utf-8"))
    assert owned_home.name.startswith("gludd-presentation-apt-")
    assert owned_home.parent.resolve() == Path("/tmp").resolve()
    assert not owned_home.exists()
    assert calls[0][:2] == ["run", "ansible-playbook"]
    assert calls[0][2:] == [
        "--inventory",
        "localhost,",
        "--connection",
        "local",
        "--forks",
        "1",
        "--become",
        "playbooks/presentation_browser_apt_source.yml",
    ]
    assert ("outcome=returned" if child_status == 0 else "outcome=failed") in result.stderr
    if child_status:
        assert "native_status=17" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr


def test_pages_prepares_apt_before_unchanged_installer() -> None:
    text = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
    assert text.index("make " + TARGET) < text.index("make presentation-browser-install-deps")
    assert text.count("make " + TARGET) == 2
    assert text.count("name: Prepare the reviewed Ubuntu archive input") == 2
    assert "authenticated native Ubuntu archive input" not in text
    assert MODE + "=0" in text
    assert "PRESENTATION_BROWSER_INSTALL_TIMEOUT=600" in text
    assert "timeout-minutes: 20" in text
    assert "browser: [chromium, webkit]" in text
    assert "playbooks/presentation_browser_apt_source.yml" in text
    assert "tests/unit/test_presentation_browser_apt_source.py" in text


def test_apt_owner_has_safe_documented_plan() -> None:
    value: object = json.loads((ROOT / "config/make_target_contract.json").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    targets = value.get("targets")
    assert isinstance(targets, list)
    selected = [entry for entry in targets if isinstance(entry, dict) and entry.get("name") == TARGET]
    assert selected == [
        {
            "name": TARGET,
            "make_variables": [MODE],
            "behavior": "make " + TARGET + " " + MODE + "=1",
        }
    ]


ORIGINAL_MIRRORS = (
    "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n"
    "https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
    "https://security.ubuntu.com/ubuntu/\tpriority:3\n"
)
ORIGINAL_MIRRORS_HTTPS_AZURE = (
    "https://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n"
    "https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
    "https://security.ubuntu.com/ubuntu/\tpriority:3\n"
)
PREFERRED_MIRRORS = ORIGINAL_MIRRORS.split("\n", 1)[1]
NATIVE_SOURCE = (
    "# Retain the native source byte for byte.\n"
    "Types: deb deb-src\n"
    "URIs: mirror+file:/etc/apt/apt-mirrors.txt\n"
    "Suites: noble noble-updates noble-backports noble-security\n"
    "Components: main restricted universe multiverse\n"
    "Architectures: amd64\n"
    "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    "Check-Valid-Until: yes\n"
)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    result: dict[str, object] = {}
    for key, item in value.items():
        assert isinstance(key, str)
        result[key] = item
    return result


def _native_fixture(
    tmp_path: Path,
    *,
    mirrors: str = ORIGINAL_MIRRORS,
    source: str = NATIVE_SOURCE,
) -> tuple[Path, Path, Path]:
    apt_root = tmp_path / "apt"
    sources = apt_root / "sources.list.d"
    sources.mkdir(parents=True)
    mirror = apt_root / "apt-mirrors.txt"
    mirror.write_text(mirrors, encoding="utf-8")
    mirror.chmod(0o640)
    native_source = sources / "ubuntu.sources"
    native_source.write_text(source, encoding="utf-8")
    native_source.chmod(0o644)
    unrelated = sources / "unrelated.list"
    unrelated.write_bytes(b"deb [signed-by=/fixture/key.gpg] https://unrelated.invalid/ stable main\n")
    return apt_root, mirror, native_source


def _native_ansible(
    tmp_path: Path,
    apt_root: Path,
    *,
    check: bool = False,
    architecture: str = "x86_64",
    override_interpreter: bool = True,
) -> subprocess.CompletedProcess[str]:
    # Retain every production task, with fixture-only resource/platform inputs.
    # Additional fixture post-tasks prove the actual managed-module interpreter.
    # The optional switch omits only the existing interpreter extra-vars binding.
    value: object = yaml.load(
        (ROOT / "playbooks/presentation_browser_apt_source.yml").read_text(encoding="utf-8"),
        Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader),
    )
    assert isinstance(value, list) and len(value) == 1
    play = _mapping(value[0])
    assert play["gather_facts"] is True
    assert play["gather_subset"] == ["min"]
    variables = _mapping(play["vars"])
    assert variables["presentation_apt_root"] == "/etc/apt"
    variables["presentation_apt_root"] = str(apt_root)
    variables["ansible_facts"] = {
        "distribution": "Ubuntu",
        "distribution_version": "24.04",
        "distribution_release": "noble",
        "architecture": architecture,
    }
    play["vars"] = variables
    play["gather_facts"] = False
    play["become"] = False
    play["post_tasks"] = [
        {
            "name": "Collect only the actual module Python interpreter",
            "ansible.builtin.setup": {
                "gather_subset": ["!all", "!min", "python"],
                "filter": "ansible_python",
            },
            "register": "presentation_fixture_python",
        },
        {
            "name": "Assert the actual module interpreter matches the controller",
            "ansible.builtin.assert": {
                "that": [
                    "presentation_fixture_python.ansible_facts.ansible_python.executable == ansible_playbook_python",
                ],
                "quiet": True,
            },
        },
    ]
    fixture_play = tmp_path / "fixture-play.yml"
    fixture_play.write_text(yaml.safe_dump([play], sort_keys=False), encoding="utf-8")
    env = _environment(tmp_path)
    for name in tuple(env):
        if name.startswith("ANSIBLE_"):
            env.pop(name)
    ansible_home = tmp_path / "ansible-home"
    env.update(
        ANSIBLE_HOME=str(ansible_home),
        ANSIBLE_LOCAL_TEMP=str(ansible_home / "local"),
        ANSIBLE_REMOTE_TEMP=str(ansible_home / "remote"),
        ANSIBLE_NOCOLOR="1",
        ANSIBLE_FORCE_COLOR="0",
        GITHUB_ACTIONS="true",
        RUNNER_ENVIRONMENT="github-hosted",
        RUNNER_OS="Linux",
        ImageOS="ubuntu24",
    )
    configuration = tmp_path / "ansible.cfg"
    configuration.write_text("[defaults]\nhost_key_checking = True\n", encoding="utf-8")
    env["ANSIBLE_CONFIG"] = str(configuration)
    command = [
        sys.executable,
        "-m",
        "ansible.cli.playbook",
        "--inventory",
        "localhost,",
        "--connection",
        "local",
        "--forks",
        "1",
    ]
    if override_interpreter:
        command.extend(["--extra-vars", json.dumps({"ansible_python_interpreter": sys.executable})])
    if check:
        command.append("--check")
    command.append(str(fixture_play))
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert not list(ansible_home.rglob("AnsiballZ_*"))
    assert not list(ansible_home.rglob("ansible-tmp-*"))
    assert "[WARNING]" not in result.stdout + result.stderr, result.stdout + result.stderr
    return result


def test_native_ansible_repairs_only_mirror_input_and_is_idempotent(tmp_path: Path) -> None:
    apt_root, mirror, source = _native_fixture(tmp_path)
    original_source = source.read_bytes()
    original_ownership = (mirror.stat().st_uid, mirror.stat().st_gid)
    unrelated = source.parent / "unrelated.list"
    original_unrelated = unrelated.read_bytes()
    result = _native_ansible(tmp_path, apt_root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert mirror.read_text(encoding="utf-8") == PREFERRED_MIRRORS
    assert stat.S_IMODE(mirror.stat().st_mode) == 0o640
    assert (mirror.stat().st_uid, mirror.stat().st_gid) == original_ownership
    assert source.read_bytes() == original_source
    assert unrelated.read_bytes() == original_unrelated
    assert "outcome=prepared" in result.stdout
    assert "installer_run=false gate_evidence=false release_ready=false" in result.stdout
    identity = (mirror.stat().st_ino, mirror.stat().st_mtime_ns)
    repeated = _native_ansible(tmp_path, apt_root)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert "changed=0" in repeated.stdout
    assert (mirror.stat().st_ino, mirror.stat().st_mtime_ns) == identity
    assert mirror.read_text(encoding="utf-8") == PREFERRED_MIRRORS
    assert source.read_bytes() == original_source


def test_native_ansible_rewrites_direct_azure_binding_to_mirror_list(tmp_path: Path) -> None:
    azure_source = (
        "# Direct Azure archive binding observed on some hosted runners.\n"
        "Types: deb deb-src\n"
        "URIs: http://azure.archive.ubuntu.com/ubuntu/\n"
        "Suites: noble noble-updates noble-backports noble-security\n"
        "Components: main restricted universe multiverse\n"
        "Architectures: amd64\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        "Check-Valid-Until: yes\n"
    )
    apt_root, mirror, source = _native_fixture(tmp_path, source=azure_source)
    original_unrelated = (source.parent / "unrelated.list").read_bytes()
    result = _native_ansible(tmp_path, apt_root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert mirror.read_text(encoding="utf-8") == PREFERRED_MIRRORS
    assert "URIs: mirror+file:/etc/apt/apt-mirrors.txt" in source.read_text(encoding="utf-8")
    assert "azure.archive.ubuntu.com" not in source.read_text(encoding="utf-8")
    assert (source.parent / "unrelated.list").read_bytes() == original_unrelated
    assert "outcome=prepared" in result.stdout
    assert "source_rewritten=true" in result.stdout
    assert "installer_run=false gate_evidence=false release_ready=false" in result.stdout


def test_native_ansible_rewrites_https_azure_binding_to_mirror_list(tmp_path: Path) -> None:
    azure_source = (
        "# Direct HTTPS Azure archive binding observed on some hosted runners.\n"
        "Types: deb deb-src\n"
        "URIs: https://azure.archive.ubuntu.com/ubuntu/\n"
        "Suites: noble noble-updates noble-backports noble-security\n"
        "Components: main restricted universe multiverse\n"
        "Architectures: amd64\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        "Check-Valid-Until: yes\n"
    )
    apt_root, mirror, source = _native_fixture(tmp_path, source=azure_source, mirrors=ORIGINAL_MIRRORS_HTTPS_AZURE)
    result = _native_ansible(tmp_path, apt_root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert mirror.read_text(encoding="utf-8") == PREFERRED_MIRRORS
    assert "URIs: mirror+file:/etc/apt/apt-mirrors.txt" in source.read_text(encoding="utf-8")
    assert "azure.archive.ubuntu.com" not in source.read_text(encoding="utf-8")
    assert "source_rewritten=true" in result.stdout


def test_native_ansible_accepts_duplicate_azure_bindings_and_rewrites_all(
    tmp_path: Path,
) -> None:
    # Some hosted images split the same Azure URI across multiple Deb822 stanzas.
    azure_source = (
        "# Duplicate Azure archive bindings.\n"
        "Types: deb\n"
        "URIs: http://azure.archive.ubuntu.com/ubuntu/\n"
        "Suites: noble\n"
        "Components: main\n"
        "Architectures: amd64\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        "\n"
        "Types: deb-src\n"
        "URIs: http://azure.archive.ubuntu.com/ubuntu/\n"
        "Suites: noble-updates\n"
        "Components: main\n"
        "Architectures: amd64\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    )
    apt_root, mirror, source = _native_fixture(tmp_path, source=azure_source)
    result = _native_ansible(tmp_path, apt_root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert mirror.read_text(encoding="utf-8") == PREFERRED_MIRRORS
    source_text = source.read_text(encoding="utf-8")
    assert source_text.count("mirror+file:/etc/apt/apt-mirrors.txt") == 2
    assert "azure.archive.ubuntu.com" not in source_text
    assert "source_rewritten=true" in result.stdout


def test_native_ansible_accepts_mirror_plus_file_duplicates_idempotently(
    tmp_path: Path,
) -> None:
    duplicate_source = NATIVE_SOURCE + "URIs: mirror+file:/etc/apt/apt-mirrors.txt\n"
    apt_root, mirror, _source = _native_fixture(tmp_path, source=duplicate_source)
    result = _native_ansible(tmp_path, apt_root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert mirror.read_text(encoding="utf-8") == PREFERRED_MIRRORS
    assert "source_rewritten=false" in result.stdout


def test_native_ansible_check_mode_predicts_without_writing(tmp_path: Path) -> None:
    apt_root, mirror, source = _native_fixture(tmp_path)
    original = (mirror.read_bytes(), source.read_bytes(), mirror.stat().st_mtime_ns)
    result = _native_ansible(tmp_path, apt_root, check=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "changed=1" in result.stdout
    assert "outcome=planned" in result.stdout
    assert (mirror.read_bytes(), source.read_bytes(), mirror.stat().st_mtime_ns) == original


@pytest.mark.parametrize(
    "refusal",
    [
        "unknown-mirror",
        "duplicate-mirror",
        "unknown-source",
        "missing-mirror",
        "linked-mirror",
        "linked-source",
        "wrong-architecture",
        "case-variant-unknown-uri",
        "folded-uri",
        "unspaced-unknown-uri",
        "empty-uri",
    ],
)
def test_native_ansible_refuses_unknown_inputs_without_mutation(
    tmp_path: Path,
    refusal: str,
) -> None:
    apt_root, mirror, source = _native_fixture(tmp_path)
    if refusal == "unknown-mirror":
        mirror.write_text(ORIGINAL_MIRRORS + "https://unknown.invalid/ priority:0\n", encoding="utf-8")
    elif refusal == "duplicate-mirror":
        mirror.write_text(ORIGINAL_MIRRORS + ORIGINAL_MIRRORS.splitlines(keepends=True)[0], encoding="utf-8")
    elif refusal == "unknown-source":
        source.write_text(
            NATIVE_SOURCE.replace("mirror+file:/etc/apt/apt-mirrors.txt", "http://unknown.invalid/ubuntu/"),
            encoding="utf-8",
        )
    elif refusal == "case-variant-unknown-uri":
        source.write_text(NATIVE_SOURCE + "uris: https://unknown.invalid/\n", encoding="utf-8")
    elif refusal == "folded-uri":
        source.write_text(
            NATIVE_SOURCE.replace("Suites:", " https://unknown.invalid/\nSuites:"),
            encoding="utf-8",
        )
    elif refusal == "unspaced-unknown-uri":
        source.write_text(NATIVE_SOURCE + "URIs:https://unknown.invalid/\n", encoding="utf-8")
    elif refusal == "empty-uri":
        source.write_text(NATIVE_SOURCE + "URIs:\n", encoding="utf-8")
    elif refusal == "missing-mirror":
        mirror.unlink()
    elif refusal in {"linked-mirror", "linked-source"}:
        selected = mirror if refusal == "linked-mirror" else source
        target = tmp_path / "protected-target"
        target.write_bytes(selected.read_bytes())
        selected.unlink()
        selected.symlink_to(target)
    before = {
        path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) for path in (mirror, source) if path.exists()
    }
    result = _native_ansible(
        tmp_path,
        apt_root,
        architecture="aarch64" if refusal == "wrong-architecture" else "x86_64",
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "outcome=prepared" not in result.stdout
    for path, invariant in before.items():
        assert (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) == invariant
    if refusal == "missing-mirror":
        assert not mirror.exists()
    elif refusal in {"linked-mirror", "linked-source"}:
        assert (mirror if refusal == "linked-mirror" else source).is_symlink()


def test_native_ansible_binds_controller_python_without_fixture_override(tmp_path: Path) -> None:
    apt_root, mirror, source = _native_fixture(tmp_path)
    original_source = source.read_bytes()
    result = _native_ansible(tmp_path, apt_root, override_interpreter=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert mirror.read_text(encoding="utf-8") == PREFERRED_MIRRORS
    assert source.read_bytes() == original_source
    assert "Assert the actual module interpreter matches the controller" in result.stdout
    assert "outcome=prepared" in result.stdout


def test_native_ansible_atomic_write_failure_preserves_inputs(tmp_path: Path) -> None:
    assert os.geteuid() != 0, "This real permission-control owner requires an unprivileged fixture user"
    apt_root, mirror, source = _native_fixture(tmp_path)
    before = (mirror.read_bytes(), source.read_bytes(), stat.S_IMODE(mirror.stat().st_mode))
    apt_root.chmod(0o500)
    try:
        result = _native_ansible(tmp_path, apt_root)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "outcome=prepared" not in result.stdout
        assert (mirror.read_bytes(), source.read_bytes(), stat.S_IMODE(mirror.stat().st_mode)) == before
        assert sorted(path.name for path in apt_root.iterdir()) == ["apt-mirrors.txt", "sources.list.d"]
    finally:
        apt_root.chmod(0o700)
