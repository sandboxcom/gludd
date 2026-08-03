"""Unit tests for cli_model.py."""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout

import general_ludd.cli_model as cli_model


class TestCmdDownload:
    def test_download_prints_name(self):
        args = argparse.Namespace(name="smollm2-135m", source="huggingface", revision=None, cache_dir=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_download(args)
        output = buf.getvalue()
        assert "smollm2-135m" in output
        assert "huggingface" in output

    def test_download_with_revision(self):
        args = argparse.Namespace(name="test-model", source="huggingface", revision="v1.0", cache_dir="/tmp/models")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_download(args)
        output = buf.getvalue()
        assert "test-model" in output
        assert "v1.0" in output
        assert "/tmp/models" in output


class TestCmdQuantize:
    def test_quantize_default_method(self):
        args = argparse.Namespace(name="smollm2-135m", method="q4_k_m", bits=None, output_dir=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_quantize(args)
        output = buf.getvalue()
        assert "smollm2-135m" in output
        assert "q4_k_m" in output

    def test_quantize_custom_method(self):
        args = argparse.Namespace(name="llama-7b", method="q8_0", bits=8, output_dir="/out")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_quantize(args)
        output = buf.getvalue()
        assert "llama-7b" in output
        assert "q8_0" in output
        assert "8" in output or "Bits" in output
        assert "/out" in output


class TestCmdServe:
    def test_serve_defaults(self):
        args = argparse.Namespace(
            name="smollm2-135m",
            engine="llamacpp",
            host="127.0.0.1",
            port=8080,
            gpu_layers=None,
            context_size=4096,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_serve(args)
        output = buf.getvalue()
        assert "smollm2-135m" in output
        assert "llamacpp" in output
        assert "127.0.0.1" in output
        assert "8080" in output

    def test_serve_with_gpu_layers(self):
        args = argparse.Namespace(
            name="model-x",
            engine="mlx",
            host="0.0.0.0",
            port=9090,
            gpu_layers=32,
            context_size=8192,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_serve(args)
        output = buf.getvalue()
        assert "model-x" in output
        assert "mlx" in output
        assert "0.0.0.0" in output
        assert "9090" in output
        assert "32" in output
        assert "8192" in output


class TestCmdEvaluate:
    def test_evaluate_all_benchmarks(self):
        args = argparse.Namespace(name="smollm2-135m", benchmark=None, limit=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_evaluate(args)
        output = buf.getvalue()
        assert "smollm2-135m" in output
        assert "hellaswag" in output

    def test_evaluate_single_benchmark(self):
        args = argparse.Namespace(name="my-model", benchmark="humaneval", limit=100)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_evaluate(args)
        output = buf.getvalue()
        assert "my-model" in output
        assert "humaneval" in output
        assert "100" in output


class TestCmdRecommend:
    def test_recommend_code_generation(self):
        args = argparse.Namespace(task="code generation", max_params=None, min_quality=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_recommend(args)
        output = buf.getvalue()
        assert "code generation" in output
        assert "deepseek-coder" in output

    def test_recommend_chat(self):
        args = argparse.Namespace(task="chat", max_params=None, min_quality=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_recommend(args)
        output = buf.getvalue()
        assert "chat" in output
        assert "llama" in output

    def test_recommend_reasoning(self):
        args = argparse.Namespace(task="reasoning", max_params=None, min_quality=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_recommend(args)
        output = buf.getvalue()
        assert "reasoning" in output
        assert "deepseek-r1" in output

    def test_recommend_unknown_task_falls_back(self):
        args = argparse.Namespace(task="gibberish task", max_params=None, min_quality=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_recommend(args)
        output = buf.getvalue()
        assert "gibberish task" in output
        assert "deepseek-coder-7b" in output  # falls back to default

    def test_recommend_with_max_params(self):
        args = argparse.Namespace(task="code generation", max_params=10.0, min_quality=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_recommend(args)
        output = buf.getvalue()
        assert "10.0" in output


class TestCmdRadar:
    def test_radar_prints_capabilities(self):
        args = argparse.Namespace(name="smollm2-135m")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_radar(args)
        output = buf.getvalue()
        assert "smollm2-135m" in output
        assert "Code Generation" in output
        assert "Reasoning" in output
        assert "Math" in output
        assert "Chat" in output
        assert "Safety" in output

    def test_radar_different_model_name(self):
        args = argparse.Namespace(name="llama-3.1-8b")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_model._cmd_radar(args)
        output = buf.getvalue()
        assert "llama-3.1-8b" in output


class TestAddSubparser:
    def test_registers_download_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "download", "smollm2-135m"])
        assert ns.model_command == "download"
        assert ns.name == "smollm2-135m"
        assert ns.func == cli_model._cmd_download

    def test_download_defaults(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "download", "my-model"])
        assert ns.source == "huggingface"
        assert ns.revision is None
        assert ns.cache_dir is None

    def test_registers_quantize_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "quantize", "smollm2-135m", "--method", "q8_0"])
        assert ns.model_command == "quantize"
        assert ns.name == "smollm2-135m"
        assert ns.method == "q8_0"
        assert ns.func == cli_model._cmd_quantize

    def test_quantize_default_method(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "quantize", "my-model"])
        assert ns.method == "q4_k_m"
        assert ns.output_dir is None

    def test_registers_serve_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "serve", "smollm2-135m", "--engine", "vllm", "--port", "9000"])
        assert ns.model_command == "serve"
        assert ns.name == "smollm2-135m"
        assert ns.engine == "vllm"
        assert ns.port == 9000
        assert ns.func == cli_model._cmd_serve

    def test_serve_defaults(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "serve", "my-model"])
        assert ns.engine == "llamacpp"
        assert ns.host == "127.0.0.1"
        assert ns.port == 8080
        assert ns.context_size == 4096

    def test_registers_evaluate_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "evaluate", "smollm2-135m", "--benchmark", "mmlu"])
        assert ns.model_command == "evaluate"
        assert ns.name == "smollm2-135m"
        assert ns.benchmark == "mmlu"
        assert ns.func == cli_model._cmd_evaluate

    def test_evaluate_with_limit(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "evaluate", "x", "--limit", "50"])
        assert ns.limit == 50
        assert ns.benchmark is None

    def test_registers_recommend_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "recommend", "--task", "code generation"])
        assert ns.model_command == "recommend"
        assert ns.task == "code generation"
        assert ns.func == cli_model._cmd_recommend

    def test_recommend_requires_task(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        with __import__("pytest").raises(SystemExit):
            parser.parse_args(["model", "recommend"])

    def test_registers_radar_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model", "radar", "smollm2-135m"])
        assert ns.model_command == "radar"
        assert ns.name == "smollm2-135m"
        assert ns.func == cli_model._cmd_radar

    def test_model_top_level_help(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        cli_model.add_model_subparser(sub)
        ns = parser.parse_args(["model"])
        assert ns.model_command is None
        assert ns.func is None


class TestGetRecommendations:
    def test_code_generation_returns_code_models(self):
        recs = cli_model._get_recommendations("code generation")
        assert len(recs) >= 3
        names = [r["name"] for r in recs]
        assert "deepseek-coder-7b-instruct" in names
        assert "smollm2-135m" in names

    def test_chat_returns_chat_models(self):
        recs = cli_model._get_recommendations("chat")
        assert len(recs) >= 3
        names = [r["name"] for r in recs]
        assert "llama-3.1-8b-instruct" in names

    def test_reasoning_returns_reasoning_models(self):
        recs = cli_model._get_recommendations("reasoning")
        names = [r["name"] for r in recs]
        assert "deepseek-r1-distill-qwen-7b" in names

    def test_unknown_task_returns_default(self):
        recs = cli_model._get_recommendations("unknown task")
        names = [r["name"] for r in recs]
        assert "deepseek-coder-7b-instruct" in names

    def test_each_recommendation_has_required_keys(self):
        recs = cli_model._get_recommendations("code generation")
        for r in recs:
            assert "name" in r
            assert "params" in r
            assert "quality" in r
            assert "note" in r
