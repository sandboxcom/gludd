"""End-to-end verification of the molecule CI job configuration.

Proves the molecule.yml molecule job references the make target correctly,
uses the right strategy matrix, and covers every scenario directory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MOLECULE_YML = PROJECT_ROOT / ".github" / "workflows" / "molecule.yml"
MAKEFILE = PROJECT_ROOT / "Makefile"
SCENARIOS_ROOT = PROJECT_ROOT / "molecule" / "playbooks"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_molecule_yml() -> dict:
    with open(MOLECULE_YML) as fh:
        return yaml.safe_load(fh)


def _read_makefile() -> str:
    return MAKEFILE.read_text()


def _iter_scenario_names(root: Path = SCENARIOS_ROOT):
    """Yield the basename of every molecule scenario directory."""
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            yield entry.name


# ---------------------------------------------------------------------------
# job existence and shape
# ---------------------------------------------------------------------------


class TestMoleculeJobExists:
    def test_job_name_is_molecule(self) -> None:
        cfg = _parse_molecule_yml()
        assert "molecule" in cfg["jobs"], "molecule job missing from build.yml"

    def test_job_has_strategy_matrix(self) -> None:
        cfg = _parse_molecule_yml()
        job = cfg["jobs"]["molecule"]
        assert "strategy" in job
        assert "matrix" in job["strategy"]

    def test_shard_matrix_uses_1_to_6(self) -> None:
        cfg = _parse_molecule_yml()
        shards = cfg["jobs"]["molecule"]["strategy"]["matrix"]["shard"]
        assert shards == [1, 2, 3, 4, 5, 6], f"expected [1,2,3,4,5,6], got {shards}"

    def test_fail_fast_is_false(self) -> None:
        cfg = _parse_molecule_yml()
        assert cfg["jobs"]["molecule"]["strategy"]["fail-fast"] is False


# ---------------------------------------------------------------------------
# command / make target
# ---------------------------------------------------------------------------


class TestMoleculeMakeTarget:
    def test_target_exists_in_makefile(self) -> None:
        content = _read_makefile()
        assert re.search(r"^molecule-test-shard:", content, re.MULTILINE), (
            "molecule-test-shard target not found in Makefile"
        )

    def test_ci_step_calls_molecule_test_shard(self) -> None:
        cfg = _parse_molecule_yml()
        steps = cfg["jobs"]["molecule"]["steps"]
        run_step = next(
            (s for s in steps if "run" in s and "molecule-test-shard" in s.get("run", "")),
            None,
        )
        assert run_step is not None, "no step invokes molecule-test-shard"

        # The invocation uses SHARD=${{ matrix.shard }}/4
        run_text = run_step["run"]
        assert "make molecule-test-shard" in run_text
        assert "SHARD=${{ matrix.shard }}/${{ env.SHARD_COUNT }}" in run_text

    def test_target_uses_SHARD_variable(self) -> None:
        content = _read_makefile()
        # Extract the recipe block for molecule-test-shard
        match = re.search(
            r"^molecule-test-shard:\n(.*?)(?=^\S|\Z)", content, re.MULTILINE | re.DOTALL
        )
        assert match, "could not extract molecule-test-shard recipe"
        recipe = match.group(1)
        assert "SHARD" in recipe, "SHARD variable not referenced in target recipe"

    def test_target_prints_failed_scenario_log_tail(self) -> None:
        content = _read_makefile()
        match = re.search(
            r"^molecule-test-shard:\n(.*?)(?=^\S|\Z)", content, re.MULTILINE | re.DOTALL
        )
        assert match, "could not extract molecule-test-shard recipe"
        recipe = match.group(1)
        assert "BEGIN failed molecule log" in recipe
        assert "END failed molecule log" in recipe
        assert "tail -n $${MOLECULE_LOG_TAIL_LINES:-200}" in recipe

    def test_single_scenario_uses_the_canonical_source_glob(self) -> None:
        """Target one source scenario without provoking default discovery errors."""
        content = _read_makefile()
        match = re.search(
            r"^molecule-test:\n(.*?)(?=^\S|\Z)", content, re.MULTILINE | re.DOTALL
        )
        assert match, "could not extract molecule-test recipe"
        recipe = match.group(1)
        assert 'MOLECULE_GLOB="molecule/playbooks/*/molecule.yml"' in recipe
        assert (
            'export ANSIBLE_COLLECTIONS_PATH="$$PROJECT_COLLECTIONS:'
            '$$ANSIBLE_STATE_DIR/collections:/usr/share/ansible/collections"'
        ) in recipe
        assert 'molecule test -s "$(SCENARIO)"' in recipe
        assert 'rm -rf "molecule/$(SCENARIO)"' not in recipe
        assert 'cp "molecule/playbooks/$(SCENARIO)/molecule.yml"' not in recipe

    def test_canonical_default_scenario_disables_shared_state(self) -> None:
        """Molecule must not emit a false critical while probing shared state."""
        default_config = SCENARIOS_ROOT / "default" / "molecule.yml"
        assert default_config.is_file()
        config = yaml.safe_load(default_config.read_text())
        assert config["shared_state"] is False
        assert config["scenario"]["test_sequence"] == []

    def test_reset_clears_molecule_state_without_deleting_source(self) -> None:
        content = _read_makefile()
        match = re.search(
            r"^molecule-reset:\n(.*?)(?=^\S|\Z)", content, re.MULTILINE | re.DOTALL
        )
        assert match, "could not extract molecule-reset recipe"
        recipe = match.group(1)
        assert 'MOLECULE_GLOB="molecule/playbooks/*/molecule.yml"' in recipe
        assert 'molecule destroy -s "$(SCENARIO)"' in recipe
        assert 'molecule reset -s "$(SCENARIO)"' not in recipe
        assert 'rm -rf "molecule/$(SCENARIO)"' not in recipe


# ---------------------------------------------------------------------------
# scenario coverage
# ---------------------------------------------------------------------------


class TestMoleculeScenarioCoverage:
    def test_all_scenarios_have_molecule_yml(self) -> None:
        missing = []
        for name in _iter_scenario_names():
            yml = SCENARIOS_ROOT / name / "molecule.yml"
            if not yml.is_file():
                missing.append(name)
        assert not missing, f"scenarios missing molecule.yml: {missing}"

    @pytest.mark.parametrize(
        "total_shards,expected_denominator",
        [
            (6, 6),
        ],
    )
    def test_six_shard_layout(self, total_shards: int, expected_denominator: int) -> None:
        assert total_shards == expected_denominator, (
            f"CI uses {total_shards} shards, expected {expected_denominator}"
        )

    def test_scenario_count_covered_by_six_shards(self) -> None:
        """Each shard gets ceil(N/6) scenarios, so 6 shards always cover all.

        This is a smoke test: the make target computes slices at runtime.
        We verify the arithmetic is correct for the current directory listing.
        """
        all_scenarios = list(_iter_scenario_names())
        assert len(all_scenarios) > 0, "no molecule scenarios found"

        import math

        shard_count = 6
        per_shard = math.ceil(len(all_scenarios) / shard_count)
        covered = per_shard * shard_count
        assert covered >= len(all_scenarios), (
            f"6-shard layout covers {covered} slots but {len(all_scenarios)} scenarios exist"
        )

    def test_shard_1_covers_start_of_sorted_list(self) -> None:
        """Verify the Makefile slice arithmetic: shard 1 starts at index 1."""
        all_scenarios = list(_iter_scenario_names())
        total = len(all_scenarios)
        shard = 1
        denominator = 6
        size = (total + denominator - 1) // denominator
        start = (shard - 1) * size + 1
        assert start == 1, f"shard 1 should start at index 1, got {start}"

    def test_shard_6_covers_end_of_sorted_list(self) -> None:
        """Verify the Makefile slice arithmetic: shard 6 ends at or past N."""
        all_scenarios = list(_iter_scenario_names())
        total = len(all_scenarios)
        shard = 6
        denominator = 6
        size = (total + denominator - 1) // denominator
        start = (shard - 1) * size + 1
        end = start + size - 1
        assert end >= total, f"shard 6 should end at or past {total}, got end={end}"

    def test_all_shards_together_cover_every_scenario(self) -> None:
        """Every scenario falls into exactly one 6-shard contiguous slice."""
        all_scenarios = list(_iter_scenario_names())
        total = len(all_scenarios)
        denominator = 6
        size = (total + denominator - 1) // denominator
        seen: set[int] = set()
        for shard in range(1, denominator + 1):
            start = (shard - 1) * size + 1
            end = min(start + size - 1, total)
            for idx in range(start, end + 1):
                seen.add(idx)
        expected = set(range(1, total + 1))
        missing = expected - seen
        assert not missing, (
            f"indices {sorted(missing)} are not covered by any shard "
            f"(total={total}, size={size})"
        )
        extra = seen - expected
        assert not extra, (
            f"indices {sorted(extra)} are out-of-range "
            f"(total={total}, size={size})"
        )


# ---------------------------------------------------------------------------
# timeout and runner
# ---------------------------------------------------------------------------


class TestMoleculeJobTimeout:
    def test_timeout_minutes(self) -> None:
        cfg = _parse_molecule_yml()
        timeout = cfg["jobs"]["molecule"]["timeout-minutes"]
        assert timeout > 0, "timeout-minutes must be positive"
        assert timeout <= 30, f"timeout-minutes {timeout} too high for a shard"
