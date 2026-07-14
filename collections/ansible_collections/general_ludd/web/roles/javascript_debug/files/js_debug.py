#!/usr/bin/env python3
"""javascript_debug — JS syntax checking, error analysis, source map verification."""
import argparse
import json
import os
import re
import sys


JS_ERROR_PATTERNS = {
    "unhandled_promise": re.compile(
        r"Unhandled\s*Promise\s*Rejection|unhandled\s*rejection", re.IGNORECASE
    ),
    "syntax_error": re.compile(r"Syntax\s*Error", re.IGNORECASE),
    "type_error": re.compile(r"Type\s*Error", re.IGNORECASE),
    "reference_error": re.compile(r"Reference\s*Error", re.IGNORECASE),
    "range_error": re.compile(r"Range\s*Error", re.IGNORECASE),
    "network_error": re.compile(r"Network\s*Error|fetch\s*failed|ECONNREFUSED", re.IGNORECASE),
    "cors_error": re.compile(r"Cross-Origin|CORS|blocked by CORS", re.IGNORECASE),
    "null_ref": re.compile(r"(?:Cannot\s*read\s*prop|undefined\s*is\s*not\s*an\s*object|null\s*reference)", re.IGNORECASE),
    "event_loop": re.compile(r"Maximum\s*call\s*stack|too\s*much\s*recursion", re.IGNORECASE),
    "memory": re.compile(r"Out\s*of\s*memory|heap\s*limit|allocation\s*failed", re.IGNORECASE),
}

TRY_CATCH_PATTERN = re.compile(r"try\s*\{", re.MULTILINE)
CATCH_PATTERN = re.compile(r"catch\s*\([^)]*\)\s*\{?", re.MULTILINE)
CONSOLE_STMT = re.compile(r"console\.\s*(log|warn|error|info|debug|table|group|groupEnd|trace|assert)\(", re.MULTILINE)
DEBUGGER_STMT = re.compile(r"\bdebugger\b", re.MULTILINE)
PROMISE_THEN = re.compile(r"\.\s*then\(", re.MULTILINE)
PROMISE_CATCH = re.compile(r"\.\s*catch\(", re.MULTILINE)
ASYNC_AWAIT = re.compile(r"\basync\s+(?:function|\(|\w+\s*\()", re.MULTILINE)


def check_syntax(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    errors = []
    warnings = []

    brackets = {"{": "}", "(": ")", "[": "]"}
    stack = []
    for i, ch in enumerate(content):
        if ch in brackets:
            stack.append((brackets[ch], i))
        elif ch in brackets.values():
            if not stack or stack[-1][0] != ch:
                errors.append(f"Unmatched closing '{ch}' at position {i}")
            else:
                stack.pop()
    for expected, pos in reversed(stack):
        errors.append(f"Unclosed '{expected}' from position {pos}")

    if content.count('"') % 2 != 0:
        warnings.append("Mismatched double quotes: possible unterminated string")
    if content.count("'") % 2 != 0:
        warnings.append("Mismatched single quotes: possible unterminated string")
    if content.count("`") % 2 != 0:
        warnings.append("Mismatched backticks: possible unterminated template literal")

    console_count = len(CONSOLE_STMT.findall(content))
    debugger_count = len(DEBUGGER_STMT.findall(content))
    if debugger_count > 0:
        warnings.append(f"Found {debugger_count} debugger statement(s): should be removed in production")
    if console_count > 10:
        warnings.append(f"Found {console_count} console.* calls: consider reducing in production")

    try_catches = len(TRY_CATCH_PATTERN.findall(content))
    catch_blocks = len(CATCH_PATTERN.findall(content))
    promise_thens = len(PROMISE_THEN.findall(content))
    promise_catches = len(PROMISE_CATCH.findall(content))
    async_awaits = len(ASYNC_AWAIT.findall(content))

    if promise_thens > promise_catches * 2:
        warnings.append(
            f"Many .then() chains ({promise_thens}) vs .catch() blocks ({promise_catches}): "
            "unhandled promise rejections possible"
        )

    return {
        "file": filepath,
        "valid_syntax": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "console_calls": console_count,
            "debugger_statements": debugger_count,
            "try_catch_blocks": try_catches,
            "catch_blocks": catch_blocks,
            "promise_then": promise_thens,
            "promise_catch": promise_catches,
            "async_await": async_awaits,
        },
    }


def analyze_error_patterns(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    patterns_found = {}
    for name, pattern in JS_ERROR_PATTERNS.items():
        matches = pattern.findall(content)
        if matches:
            patterns_found[name] = len(matches)

    return {
        "file": filepath,
        "patterns_found": patterns_found,
        "error_type_count": len(patterns_found),
    }


def verify_source_maps(js_filepath):
    source_map_path = js_filepath + ".map"
    if not os.path.exists(source_map_path):
        return {
            "file": js_filepath,
            "source_map_found": False,
            "source_map_verified": False,
            "error": f"Source map not found: {source_map_path}",
        }

    try:
        with open(source_map_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        required_keys = ["version", "sources", "mappings"]
        missing = [k for k in required_keys if k not in data]

        return {
            "file": js_filepath,
            "source_map_found": True,
            "source_map_verified": len(missing) == 0,
            "version": data.get("version"),
            "source_count": len(data.get("sources", [])),
            "missing_keys": missing,
            "has_source_content": "sourcesContent" in data,
        }
    except (json.JSONDecodeError, IOError) as e:
        return {
            "file": js_filepath,
            "source_map_found": True,
            "source_map_verified": False,
            "error": str(e),
        }


def analyze_bundle(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")
    total_lines = len(lines)
    total_chars = len(content)

    import_count = len(re.findall(r"\bimport\s+", content))
    require_count = len(re.findall(r"\brequire\s*\(", content))
    export_count = len(re.findall(r"\bexport\s+", content))
    class_count = len(re.findall(r"\bclass\s+\w", content))
    function_count = len(re.findall(r"\bfunction\s+\w|\w+\s*=\s*(?:async\s+)?\([^)]*\)\s*=>", content))

    return {
        "file": filepath,
        "total_lines": total_lines,
        "total_chars": total_chars,
        "import_count": import_count,
        "require_count": require_count,
        "export_count": export_count,
        "class_count": class_count,
        "function_count": function_count,
    }


def detect_unhandled_errors(content):
    patterns = []
    for name, pattern in JS_ERROR_PATTERNS.items():
        if pattern.search(content):
            patterns.append(name)

    risky = []
    if PROMISE_THEN.search(content) and not PROMISE_CATCH.search(content):
        risky.append("promise_without_catch")
    if ASYNC_AWAIT.search(content) and not TRY_CATCH_PATTERN.search(content) and not PROMISE_CATCH.search(content):
        risky.append("async_without_error_handling")

    return patterns, risky


def main():
    parser = argparse.ArgumentParser(description="JavaScript debugging and analysis")
    parser.add_argument("--files", required=True, help="JSON list of JS file paths")
    parser.add_argument("--operation", default="check_syntax",
                        choices=["check_syntax", "lint", "analyze_errors", "verify_source_maps"])
    parser.add_argument("--output", default="/tmp/javascript_debug.json")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--source-maps", action="store_true")
    parser.add_argument("--bundle-analyze", action="store_true")
    parser.add_argument("--lint", action="store_true")
    args = parser.parse_args()

    file_list = json.loads(args.files)
    result = {
        "role": "javascript_debug",
        "operation": args.operation,
        "files_analyzed": len(file_list),
    }

    if args.operation == "check_syntax":
        syntax_results = []
        for f in file_list:
            syntax_results.append(check_syntax(f))
        result["results"] = syntax_results
        result["valid_syntax"] = all(r["valid_syntax"] for r in syntax_results)
        result["error_count"] = sum(len(r["errors"]) for r in syntax_results)
        result["warning_count"] = sum(len(r["warnings"]) for r in syntax_results)

    elif args.operation == "analyze_errors":
        error_results = []
        for f in file_list:
            error_results.append(analyze_error_patterns(f))
        result["results"] = error_results
        result["error_count"] = sum(r["error_type_count"] for r in error_results)

    elif args.operation == "verify_source_maps":
        sm_results = []
        for f in file_list:
            sm_results.append(verify_source_maps(f))
        result["results"] = sm_results
        result["source_maps_verified"] = all(
            r["source_map_verified"] for r in sm_results
        )

    if args.bundle_analyze:
        bundle_results = []
        for f in file_list:
            bundle_results.append(analyze_bundle(f))
        result["bundle_analysis"] = bundle_results

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result))
    all_valid = result.get("valid_syntax", True)
    sys.exit(0 if all_valid else 1)


if __name__ == "__main__":
    main()
