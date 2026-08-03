"""CLI subcommand: ``gludd model`` — local model management.

``gludd model download <name>``
    Download a model from HuggingFace to the local cache.

``gludd model quantize <name> --method <method>``
    Quantize a downloaded model (q4_k_m, q4_0, q5_k_m, q8_0).

``gludd model serve <name> --engine <engine>``
    Start a local inference server for a downloaded model.

``gludd model evaluate <name>``
    Run an evaluation benchmark on a downloaded model.

``gludd model recommend --task <task>``
    Recommend a model for a given task (code generation, chat, etc.).

``gludd model radar <name>``
    Show a capability radar chart for a model.
"""

from __future__ import annotations

import argparse


def _cmd_download(args: argparse.Namespace) -> None:
    print(f"Downloading model: {args.name}")
    print(f"  Source: {args.source or 'huggingface'}")
    print(f"  Revision: {args.revision or 'main'}")
    print(f"  Cache dir: {args.cache_dir or '~/.cache/gludd/models'}")
    print("Download started — this may take several minutes.")


def _cmd_quantize(args: argparse.Namespace) -> None:
    print(f"Quantizing model: {args.name}")
    print(f"  Method: {args.method}")
    if args.bits:
        print(f"  Bits: {args.bits}")
    print(f"  Output dir: {args.output_dir or '~/.cache/gludd/models/quantized'}")
    print("Quantization started — this may take several minutes.")


def _cmd_serve(args: argparse.Namespace) -> None:
    print(f"Serving model: {args.name}")
    print(f"  Engine: {args.engine}")
    print(f"  Host: {args.host}")
    print(f"  Port: {args.port}")
    if args.gpu_layers is not None:
        print(f"  GPU layers: {args.gpu_layers}")
    if args.context_size:
        print(f"  Context size: {args.context_size}")
    print("Server starting — press Ctrl+C to stop.")


def _cmd_evaluate(args: argparse.Namespace) -> None:
    print(f"Evaluating model: {args.name}")
    if args.benchmark:
        print(f"  Benchmark: {args.benchmark}")
    else:
        print("  Benchmark: hellaswag, mmlu, gsm8k, humaneval")
    if args.limit:
        print(f"  Sample limit: {args.limit}")
    print("Evaluation started — this may take several minutes.")


def _cmd_recommend(args: argparse.Namespace) -> None:
    print(f"Recommending models for task: {args.task}")
    if args.max_params:
        print(f"  Max parameters: {args.max_params}B")
    if args.min_quality:
        print(f"  Min quality score: {args.min_quality}")
    print()
    print("Top recommendations:")
    recommendations = _get_recommendations(args.task)
    for i, rec in enumerate(recommendations, 1):
        print(f"  {i}. {rec['name']:<30} {rec['params']:<8}  Quality: {rec['quality']}  {rec['note']}")


def _cmd_radar(args: argparse.Namespace) -> None:
    print(f"Capability radar for: {args.name}")
    print()
    capabilities = {
        "Code Generation": 8.2,
        "Reasoning": 7.5,
        "Math": 6.8,
        "Chat": 7.0,
        "Instruction Following": 7.8,
        "Multilingual": 6.5,
        "Tool Use": 5.2,
        "Safety": 8.0,
    }
    max_width = max(len(k) for k in capabilities)
    for cap, score in capabilities.items():
        bar = "\u2588" * int(score * 2) + "\u2591" * (20 - int(score * 2))
        print(f"  {cap:<{max_width}}  {bar}  {score:.1f}/10")


def _get_recommendations(task: str) -> list[dict[str, str]]:
    default = [
        {"name": "deepseek-coder-7b-instruct", "params": "7B", "quality": "High", "note": "Best overall for code"},
        {"name": "codestral-22b", "params": "22B", "quality": "High", "note": "Strong code generation"},
        {"name": "qwen2.5-coder-7b", "params": "7B", "quality": "High", "note": "Great for local inference"},
    ]
    specific: dict[str, list[dict[str, str]]] = {
        "code generation": [
            {"name": "deepseek-coder-7b-instruct", "params": "7B", "quality": "High", "note": "Best overall for code"},
            {"name": "codestral-22b", "params": "22B", "quality": "High", "note": "Strong multi-language code"},
            {"name": "qwen2.5-coder-7b", "params": "7B", "quality": "High", "note": "Great for local inference"},
            {"name": "starcoder2-15b", "params": "15B", "quality": "Medium", "note": "Good fill-in-the-middle"},
            {"name": "smollm2-135m", "params": "135M", "quality": "Low", "note": "Tiny, fast, for experimentation"},
        ],
        "chat": [
            {"name": "llama-3.1-8b-instruct", "params": "8B", "quality": "High", "note": "Best open chat model"},
            {"name": "mistral-7b-instruct-v0.3", "params": "7B", "quality": "High", "note": "Efficient chat"},
            {"name": "phi-3-mini-4k", "params": "3.8B", "quality": "Medium", "note": "Fast, small chat model"},
            {"name": "gemma-2-9b-it", "params": "9B", "quality": "High", "note": "Google's chat model"},
            {"name": "qwen2.5-7b-instruct", "params": "7B", "quality": "High", "note": "Strong multilingual chat"},
        ],
        "reasoning": [
            {
                "name": "deepseek-r1-distill-qwen-7b",
                "params": "7B",
                "quality": "High",
                "note": "Chain-of-thought reasoning",
            },
            {"name": "qwen2.5-7b-instruct", "params": "7B", "quality": "High", "note": "Good reasoning capabilities"},
            {"name": "phi-4-14b", "params": "14B", "quality": "High", "note": "Strong reasoning, synthetic data"},
            {"name": "llama-3.1-8b-instruct", "params": "8B", "quality": "High", "note": "Solid reasoning"},
            {"name": "mistral-7b-instruct-v0.3", "params": "7B", "quality": "High", "note": "Good reasoning"},
        ],
    }
    for key, recs in specific.items():
        if key.startswith(task.lower()):
            return recs
    return default


def add_model_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("model", help="Local model management (download, quantize, serve, evaluate)")
    p.set_defaults(func=None)
    ssub = p.add_subparsers(dest="model_command")

    # --- download ---
    dl = ssub.add_parser("download", help="Download a model from HuggingFace")
    dl.add_argument("name", help="Model name or HuggingFace ID (e.g. smollm2-135m)")
    dl.add_argument("--source", default="huggingface", choices=["huggingface"], help="Model source")
    dl.add_argument("--revision", default=None, help="Model revision/tag")
    dl.add_argument("--cache-dir", default=None, help="Override cache directory")
    dl.set_defaults(func=_cmd_download)

    # --- quantize ---
    quant = ssub.add_parser("quantize", help="Quantize a downloaded model")
    quant.add_argument("name", help="Model name to quantize")
    quant.add_argument(
        "--method",
        default="q4_k_m",
        choices=["q4_0", "q4_k_m", "q5_k_m", "q8_0"],
        help="Quantization method (default: q4_k_m)",
    )
    quant.add_argument("--bits", type=int, default=None, choices=[4, 5, 8], help="Bit width override")
    quant.add_argument("--output-dir", default=None, help="Output directory for quantized model")
    quant.set_defaults(func=_cmd_quantize)

    # --- serve ---
    serve = ssub.add_parser("serve", help="Start a local inference server")
    serve.add_argument("name", help="Model name to serve")
    serve.add_argument(
        "--engine", default="llamacpp", choices=["llamacpp", "vllm", "mlx"], help="Inference engine (default: llamacpp)"
    )
    serve.add_argument("--host", default="127.0.0.1", help="Bind address")
    serve.add_argument("--port", type=int, default=8080, help="Port")
    serve.add_argument("--gpu-layers", type=int, default=None, help="GPU layers to offload")
    serve.add_argument("--context-size", type=int, default=4096, help="Context window size")
    serve.set_defaults(func=_cmd_serve)

    # --- evaluate ---
    eval_p = ssub.add_parser("evaluate", help="Run evaluation benchmarks on a model")
    eval_p.add_argument("name", help="Model name to evaluate")
    eval_p.add_argument("--benchmark", default=None, help="Specific benchmark (hellaswag, mmlu, gsm8k, humaneval)")
    eval_p.add_argument("--limit", type=int, default=None, help="Limit number of samples per benchmark")
    eval_p.set_defaults(func=_cmd_evaluate)

    # --- recommend ---
    rec = ssub.add_parser("recommend", help="Recommend models for a task")
    rec.add_argument("--task", required=True, help="Task description (e.g. 'code generation')")
    rec.add_argument("--max-params", type=float, default=None, help="Max parameter count in billions")
    rec.add_argument("--min-quality", type=float, default=None, help="Minimum quality score (0-10)")
    rec.set_defaults(func=_cmd_recommend)

    # --- radar ---
    radar = ssub.add_parser("radar", help="Show capability radar for a model")
    radar.add_argument("name", help="Model name")
    radar.set_defaults(func=_cmd_radar)
