"""Compute SHA-256 hashes for known model placeholder strings."""

import hashlib

PLACEHOLDERS = [
    b"smollm2-135m-model",
    b"smollm2-135m-config",
    b"smollm2-135m-tokenizer",
    b"smollm2-135m-tokcfg",
    b"smollm2-135m-gencfg",
    b"smollm2-135m-spec",
    b"qwen25-05b-model",
    b"qwen25-05b-config",
    b"qwen25-05b-tokenizer",
    b"qwen25-05b-tokcfg",
    b"qwen25-05b-gencfg",
    b"qwen25-05b-vocab",
    b"qwen25-05b-merges",
    b"tinyllama-11b-model",
    b"tinyllama-11b-config",
    b"tinyllama-11b-tokenizer",
    b"tinyllama-11b-tokcfg",
    b"tinyllama-11b-gencfg",
    b"phi2-model",
    b"phi2-config",
    b"phi2-tokenizer",
    b"phi2-tokcfg",
    b"phi2-added",
    b"qwen25-05b-gguf-q4_k_m",
    b"qwen25-05b-gguf-config",
    b"qwen25-05b-gguf-tokenizer",
]

for p in PLACEHOLDERS:
    h = hashlib.sha256(p).hexdigest()
    print(f"{p.decode():38s} => {h}")
