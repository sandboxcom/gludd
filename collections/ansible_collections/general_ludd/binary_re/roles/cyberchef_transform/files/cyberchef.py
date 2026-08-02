#!/usr/bin/env python3
"""Local CyberChef-style transform engine — used by cyberchef_transform role.

Implements common recipes locally without an external API dependency.
Gate on --enable-api for live API calls.
"""
from __future__ import annotations

import argparse
import base64
import codecs
import json
import urllib.parse
import urllib.request
from pathlib import Path

RECIPES: dict[str, dict] = {
    "base64_decode": {
        "name": "Base64 Decode",
        "func": lambda x: base64.b64decode(x, validate=True).decode("utf-8", errors="replace"),
        "input_type": "text",
        "module": "encoding",
    },
    "base64_encode": {
        "name": "Base64 Encode",
        "func": lambda x: base64.b64encode(x.encode("utf-8", errors="replace")).decode("ascii"),
        "input_type": "text",
        "module": "encoding",
    },
    "hex_decode": {
        "name": "Hex Decode",
        "func": lambda x: bytes.fromhex(x.replace(" ", "").replace("0x", "")).decode("utf-8", errors="replace"),
        "input_type": "text",
        "module": "encoding",
    },
    "hex_encode": {
        "name": "Hex Encode",
        "func": lambda x: x.encode("utf-8", errors="replace").hex(),
        "input_type": "text",
        "module": "encoding",
    },
    "rot13": {
        "name": "ROT13",
        "func": lambda x: codecs.decode(x, "rot_13"),
        "input_type": "text",
        "module": "encoding",
    },
    "rot47": {
        "name": "ROT47",
        "func": lambda x: "".join(
            chr(33 + ((ord(c) - 33 + 47) % 94)) if 33 <= ord(c) <= 126 else c
            for c in x
        ),
        "input_type": "text",
        "module": "encoding",
    },
    "from_charcode": {
        "name": "From Charcode",
        "func": lambda x: "".join(
            chr(int(n.strip()))
            for n in x.replace(" ", "").split(",")
            if n.strip().isdigit()
        ),
        "input_type": "text",
        "module": "encoding",
    },
    "to_charcode": {
        "name": "To Charcode",
        "func": lambda x: ",".join(str(ord(c)) for c in x),
        "input_type": "text",
        "module": "encoding",
    },
    "xor": {
        "name": "XOR",
        "func": lambda x, key=None: _xor_transform(x, key),
        "input_type": "text+key",
        "module": "encryption",
    },
    "xor_bruteforce": {
        "name": "XOR Brute Force",
        "func": lambda x: _xor_bruteforce(x),
        "input_type": "text",
        "module": "encryption",
    },
    "url_decode": {
        "name": "URL Decode",
        "func": urllib.parse.unquote,
        "input_type": "text",
        "module": "encoding",
    },
    "url_encode": {
        "name": "URL Encode",
        "func": lambda x: urllib.parse.quote(x, safe=""),
        "input_type": "text",
        "module": "encoding",
    },
    "binary_decode": {
        "name": "Binary Decode",
        "func": lambda x: "".join(
            chr(int(b, 2)) for b in x.split()
        ),
        "input_type": "text",
        "module": "encoding",
    },
    "binary_encode": {
        "name": "Binary Encode",
        "func": lambda x: " ".join(format(ord(c), "08b") for c in x),
        "input_type": "text",
        "module": "encoding",
    },
    "base32_decode": {
        "name": "Base32 Decode",
        "func": lambda x: base64.b32decode(x, casefold=True).decode("utf-8", errors="replace"),
        "input_type": "text",
        "module": "encoding",
    },
    "base32_encode": {
        "name": "Base32 Encode",
        "func": lambda x: base64.b32encode(x.encode("utf-8", errors="replace")).decode("ascii"),
        "input_type": "text",
        "module": "encoding",
    },
    "reverse": {
        "name": "Reverse",
        "func": lambda x: x[::-1],
        "input_type": "text",
        "module": "utility",
    },
    "strip_html": {
        "name": "Strip HTML Tags",
        "func": lambda x: _strip_html_tags(x),
        "input_type": "text",
        "module": "utility",
    },
}


def _xor_transform(data: str, key: str | None = None) -> str:
    if not key:
        return "ERROR: XOR requires a key (use --key parameter)"
    data_bytes = data.encode("utf-8", errors="replace")
    key_bytes = key.encode("utf-8", errors="replace")
    result = bytes(d ^ key_bytes[i % len(key_bytes)] for i, d in enumerate(data_bytes))
    return result.decode("utf-8", errors="replace")


def _xor_bruteforce(data: str) -> str:
    data_bytes = data.encode("utf-8", errors="replace")
    results: list[dict] = []
    for key in range(256):
        result = bytes(b ^ key for b in data_bytes)
        printable = sum(1 for b in result if 32 <= b < 127)
        ratio = printable / len(result) if result else 0
        if ratio > 0.7:
            decoded = result.decode("ascii", errors="replace")
            results.append({"key": key, "ratio": round(ratio, 3), "text": decoded[:200]})
    results.sort(key=lambda r: r["ratio"], reverse=True)
    return json.dumps(results[:10], indent=2)


def _strip_html_tags(text: str) -> str:
    import re
    return re.sub(r"<[^>]*>", "", text)


def _call_cyberchef_api(
    api_url: str, recipe: str, input_data: str, key: str | None = None
) -> dict:
    recipe_config = [
        {"op": recipe, "args": [key] if key else []}
    ]
    payload = json.dumps({
        "input": input_data,
        "recipe": recipe_config,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/bake",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": str(e), "api_url": api_url}
    except Exception as e:
        return {"error": str(e)}


def _transform_local(recipe_name: str, input_data: str, key: str | None = None) -> dict:
    recipe = RECIPES.get(recipe_name)
    if not recipe:
        return {
            "error": f"Unknown recipe: {recipe_name}",
            "available": sorted(RECIPES.keys()),
        }

    try:
        if recipe_name == "xor" and key:
            output = recipe["func"](input_data, key)
        elif recipe_name == "xor":
            output = recipe["func"](input_data)
        elif recipe["input_type"] == "text+key":
            output = recipe["func"](input_data, key)
        else:
            output = recipe["func"](input_data)

        return {
            "input": input_data[:500],
            "recipe": recipe_name,
            "recipe_display_name": recipe["name"],
            "module": recipe["module"],
            "output": output[:10000] if isinstance(output, str) else str(output)[:10000],
            "output_length": len(output) if isinstance(output, str) else len(str(output)),
            "key_used": key if key else None,
            "backend": "local",
        }
    except Exception as e:
        return {
            "input": input_data[:500],
            "recipe": recipe_name,
            "error": str(e),
            "backend": "local",
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CyberChef transform engine — local or API-backed"
    )
    parser.add_argument(
        "--input", type=str, required=True,
        help="Input data to transform"
    )
    parser.add_argument(
        "--recipe", type=str, required=True,
        help="Recipe name (base64_decode, rot13, xor, etc.)"
    )
    parser.add_argument(
        "--key", type=str, default="",
        help="Key for recipes that require one (e.g. XOR)"
    )
    parser.add_argument(
        "--output", type=str, default="-",
        help="Output file path (default: stdout)"
    )
    parser.add_argument(
        "--enable-api", action="store_true", default=False,
        help="Use live CyberChef API instead of local implementation"
    )
    parser.add_argument(
        "--api-url", type=str, default="http://localhost:3000",
        help="CyberChef API base URL"
    )
    parser.add_argument(
        "--list-recipes", action="store_true", default=False,
        help="List available recipes and exit"
    )
    args = parser.parse_args()

    if args.list_recipes:
        recipes_output = {
            recipe_name: {
                "display": cfg["name"],
                "module": cfg["module"],
                "input_type": cfg["input_type"],
            }
            for recipe_name, cfg in sorted(RECIPES.items())
        }
        print(json.dumps(recipes_output, indent=2))
        return

    if args.enable_api:
        result = _call_cyberchef_api(
            args.api_url, args.recipe, args.input, args.key or None
        )
    else:
        result = _transform_local(args.recipe, args.input, args.key or None)

    output = json.dumps(result, indent=2, default=str)

    if args.output == "-":
        print(output)
    else:
        Path(args.output).write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
