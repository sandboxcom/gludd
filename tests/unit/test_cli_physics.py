"""Unit tests for ``gludd physics`` CLI subcommands."""

from __future__ import annotations

import argparse
import builtins
import json
import sys
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Protocol, cast

import pytest

from general_ludd.cli_physics import (
    _check_collection_import,
    _run_latex,
    _run_math,
    _run_particle,
    _run_quantum,
    _run_review,
    _run_spectroscopy,
    _run_synthesis,
    _run_thermo,
    add_physics_subparser,
)


class _SubparserChoices(Protocol):
    choices: Mapping[str, argparse.ArgumentParser]


class TestPhysicsSubparser:
    def test_registers_physics_parser(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        assert "physics" in sub.choices
        physics = sub.choices["physics"]
        assert physics.get_default("func") is None

    def test_quantum_subcommand_parsed(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(["physics", "quantum"])
        assert args.physics_command == "quantum"

    def test_quantum_custom_args(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(
            ["physics", "quantum", "--problem", "harmonic_oscillator",
             "--num-states", "10", "--output-dir", "/tmp/test-quantum"]
        )
        assert args.problem == "harmonic_oscillator"
        assert args.num_states == 10
        assert args.output_dir == "/tmp/test-quantum"

    def test_particle_subcommand_parsed(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(["physics", "particle", "--beam-energy-GeV", "14.0"])
        assert args.beam_energy_GeV == 14.0

    def test_spectroscopy_subcommand_parsed(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(
            ["physics", "spectroscopy", "--technique", "ir", "--wl-min", "4000"])
        assert args.technique == "ir"
        assert args.wl_min == 4000.0

    def test_thermo_subcommand_parsed(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(
            ["physics", "thermo", "--substance", "iron", "--mass", "2.0"])
        assert args.substance == "iron"
        assert args.mass == 2.0

    def test_synthesis_subcommand_parsed(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(
            ["physics", "synthesis", "--molecule", "paracetamol"])
        assert args.molecule == "paracetamol"

    def test_math_subcommand_parsed(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(
            ["physics", "math", "--rate-k", "0.75", "--time-steps", "200"])
        assert args.rate_k == 0.75
        assert args.time_steps == 200

    def test_latex_subcommand_parsed(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(
            ["physics", "latex", "--document-class", "beamer", "--title", "Slides"])
        assert args.document_class == "beamer"
        assert args.title == "Slides"

    def test_review_subcommand_parsed(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(
            ["physics", "review", "--depth", "deep", "--text", "test content"])
        assert args.depth == "deep"
        assert args.text == "test content"

    def test_review_missing_text_exits(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        args = parser.parse_args(
            ["physics", "review", "--depth", "quick"])
        with pytest.raises(SystemExit):
            _run_review(args)

    def test_all_eight_subcommands_registered(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        physics = sub.choices["physics"]
        assert physics._subparsers is not None
        phys_sub = cast(_SubparserChoices, physics._subparsers._group_actions[0])
        registered = sorted(str(choice) for choice in phys_sub.choices)
        expected = sorted([
            "quantum", "particle", "spectroscopy", "thermo",
            "synthesis", "math", "latex", "review",
        ])
        assert registered == expected


class TestQuantumRun:
    def test_run_quantum_basic(self) -> None:
        args = argparse.Namespace(
            problem="infinite_square_well",
            well_width_nm=1.0,
            particle="electron",
            potential="square_well",
            dimensions=1,
            num_states=5,
            solver="numpy",
            output_dir="/tmp/test-quantum",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_quantum(args)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        result = json.loads(output)
        assert result["status"] == "success"
        assert result["n_states"] == 5
        assert "ground_state_eV" in result

    def test_run_quantum_custom_states(self) -> None:
        args = argparse.Namespace(
            problem="infinite_square_well",
            well_width_nm=2.0,
            particle="electron",
            potential="square_well",
            dimensions=1,
            num_states=3,
            solver="numpy",
            output_dir="/tmp/test-quantum",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_quantum(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["n_states"] == 3


class TestParticleRun:
    def test_run_particle_basic(self) -> None:
        args = argparse.Namespace(
            beam_energy_GeV=13.6,
            target="proton",
            beam="proton",
            detector="generic_4pi",
            luminosity_inv_fb=139.0,
            channel="H_to_ZZ_to_4l",
            decay_particle="Higgs",
            decay_lifetime_s=1.56e-22,
            branching_ratios=None,
            output_dir="/tmp/test-particle",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_particle(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"

    def test_run_particle_with_branching(self) -> None:
        args = argparse.Namespace(
            beam_energy_GeV=13.6,
            target="proton",
            beam="proton",
            detector="atlas",
            luminosity_inv_fb=139.0,
            channel="H_to_gamma_gamma",
            decay_particle="Z_boson",
            decay_lifetime_s=2.64e-25,
            branching_ratios=json.dumps({"ee": 0.0336, "mumu": 0.0336, "tautau": 0.0336}),
            output_dir="/tmp/test-particle",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_particle(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"


class TestSpectroscopyRun:
    def test_run_spectroscopy_basic(self) -> None:
        args = argparse.Namespace(
            technique="uv_vis",
            wl_min=200.0,
            wl_max=800.0,
            resolution=1.0,
            solvent="water",
            temperature=25.0,
            peak_threshold=0.1,
            peaks=None,
            output_dir="/tmp/test-spectro",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_spectroscopy(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"

    def test_run_spectroscopy_custom_peaks(self) -> None:
        peaks = json.dumps([{"center_nm": 450, "amplitude": 0.8, "sigma_nm": 6.0}])
        args = argparse.Namespace(
            technique="fluorescence",
            wl_min=300.0,
            wl_max=700.0,
            resolution=0.5,
            solvent="ethanol",
            temperature=20.0,
            peak_threshold=0.05,
            peaks=peaks,
            output_dir="/tmp/test-spectro",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_spectroscopy(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"


class TestThermoRun:
    def test_run_thermo_basic(self) -> None:
        args = argparse.Namespace(
            substance="water",
            mass=1.0,
            initial_temp=25.0,
            final_temp=100.0,
            pressure=1.0,
            output_dir="/tmp/test-thermo",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_thermo(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"

    def test_run_thermo_iron(self) -> None:
        args = argparse.Namespace(
            substance="iron",
            mass=2.0,
            initial_temp=20.0,
            final_temp=200.0,
            pressure=1.0,
            output_dir="/tmp/test-thermo",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_thermo(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"


class TestSynthesisRun:
    def test_run_synthesis_aspirin(self) -> None:
        args = argparse.Namespace(
            molecule="aspirin",
            starting_material="salicylic_acid",
            solvent="acetic_anhydride",
            catalyst="sulfuric_acid",
            temperature=85.0,
            reaction_time=15.0,
            output_dir="/tmp/test-synth",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_synthesis(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"
        assert result["molecule"] == "aspirin"

    def test_run_synthesis_paracetamol(self) -> None:
        args = argparse.Namespace(
            molecule="paracetamol",
            starting_material="4-aminophenol",
            solvent="acetic_anhydride",
            catalyst="sulfuric_acid",
            temperature=60.0,
            reaction_time=30.0,
            output_dir="/tmp/test-synth",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_synthesis(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"
        assert result["molecule"] == "paracetamol"


class TestMathRun:
    def test_run_math_basic(self) -> None:
        args = argparse.Namespace(
            model_type="ode_first_order",
            equation="dy/dt = -k * y",
            y0=1.0,
            rate_k=0.5,
            time_start=0.0,
            time_end=10.0,
            time_steps=100,
            output_dir="/tmp/test-math",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_math(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"

    def test_run_math_fast_decay(self) -> None:
        args = argparse.Namespace(
            model_type="ode_first_order",
            equation="dy/dt = -k * y",
            y0=10.0,
            rate_k=2.0,
            time_start=0.0,
            time_end=5.0,
            time_steps=50,
            output_dir="/tmp/test-math",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_math(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"
        assert result["half_life"] < 0.5


class TestLatexRun:
    def test_run_latex_basic(self) -> None:
        args = argparse.Namespace(
            document_class="article",
            font_size="11pt",
            title="Test Paper",
            author="Test Author",
            equation=r"E = mc^2",
            output_dir="/tmp/test-latex",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_latex(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"
        assert Path(result["doc_path"]).exists()
        assert Path(result["eq_path"]).exists()

    def test_run_latex_beamer(self) -> None:
        args = argparse.Namespace(
            document_class="beamer",
            font_size="12pt",
            title="Presentation",
            author="Agent",
            equation=r"\nabla^2 \phi = 0",
            output_dir="/tmp/test-latex",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_latex(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"
        content = Path(result["doc_path"]).read_text()
        assert r"\documentclass[12pt]{beamer}" in content


class TestReviewRun:
    def test_run_review_with_text(self) -> None:
        test_text = (
            "Abstract\nThis is a test.\n\n"
            "Introduction\nWe present a novel method.\n\n"
            "Methods\nWe used deep learning.\n\n"
            "Results\nAccuracy 99%.\n\n"
            "Discussion\nIt works well.\n\n"
            "Conclusion\nWe succeeded."
        )
        args = argparse.Namespace(
            title="Test Paper",
            text=test_text,
            file=None,
            depth="standard",
            output_dir="/tmp/test-review",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_review(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"
        assert "rigor" in result

    def test_run_review_minimal_text(self) -> None:
        args = argparse.Namespace(
            title="",
            text="Finding: The algorithm converges. We show results. We demonstrate effectiveness.",
            file=None,
            depth="quick",
            output_dir="/tmp/test-review",
        )
        captured = StringIO()
        sys.stdout = captured
        _run_review(args)
        sys.stdout = sys.__stdout__
        result = json.loads(captured.getvalue())
        assert result["status"] == "success"

    def test_run_review_reads_owned_input_file(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        paper = tmp_path / "paper.txt"
        paper.write_text("Abstract\nA reproducible result.\nConclusion\nValidated.")

        _run_review(
            argparse.Namespace(
                title="File Paper",
                text="ignored",
                file=str(paper),
                depth="quick",
                output_dir=str(tmp_path / "review"),
            )
        )

        assert json.loads(capsys.readouterr().out)["status"] == "success"


class TestPhysicsCollectionBoundary:
    def test_required_collection_modules_import(self) -> None:
        _check_collection_import()

    def test_missing_collection_module_fails_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        original_import = builtins.__import__

        def _import(
            name: str,
            globals: dict[str, object] | None = None,
            locals: dict[str, object] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            if name.endswith(".latex_expert"):
                raise ImportError("missing physics collection")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _import)

        with pytest.raises(SystemExit) as exc_info:
            _check_collection_import()

        assert exc_info.value.code == 1
        assert "latex_expert" in capsys.readouterr().err


class TestInvalidCalls:
    def test_review_no_text_exits(self) -> None:
        args = argparse.Namespace(
            title="", text="", file=None, depth="standard",
            output_dir="/tmp/test-review",
        )
        with pytest.raises(SystemExit):
            _run_review(args)

    def test_invalid_physics_subcommand(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_physics_subparser(sub)
        with pytest.raises(SystemExit):
            parser.parse_args(["physics", "nonexistent"])
