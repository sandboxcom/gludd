"""CLI subcommand: ``gludd language``.

Language detection, translation, and transliteration via capability dispatch.

Commands:
  detect <text>         Detect the language of input text
  translate <text> <target>  Translate text to target language
  transliterate <text>  Transliterate text to Latin script
  capabilities          List registered language capabilities
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _discover_and_route(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    from general_ludd.dispatch.capabilities import discover_capabilities
    from general_ludd.dispatch.router import CapabilityRouter

    colls_root = Path(__file__).resolve().parent.parent.parent / "collections" / "ansible_collections"
    registry = discover_capabilities(colls_root)
    router = CapabilityRouter(registry)
    route = router.route(capability, payload)
    return {
        "ok": route.ok,
        "capability": route.capability,
        "matches": [m.name for m in route.matches],
        "error": route.error,
        "payload": route.payload,
    }


def _cmd_detect(args: argparse.Namespace) -> None:
    text = args.text
    payload = {"text": text}
    route = _discover_and_route("language_detection", payload)

    from general_ludd.language.core import LanguageDetector

    detector = LanguageDetector()
    result = detector.detect(text)

    output = {
        "route": route,
        "result": {
            "language_code": result.language_code,
            "language_name": _lang_name(result.language_code),
            "confidence": result.confidence,
            "script": result.script,
            "region": result.region,
        },
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print(f"Language: {result.language_code} ({_lang_name(result.language_code)})")
        print(f"Confidence: {result.confidence:.2f}")
        if result.script:
            print(f"Script: {result.script}")
        if result.region:
            print(f"Region: {result.region}")
        print(f"Method: {result.detection_method}")
        if route["ok"]:
            print(f"Capability route: {', '.join(route['matches'])}")
        else:
            print(f"Capability route: {route.get('error', 'unknown')}")


def _cmd_translate(args: argparse.Namespace) -> None:
    text = args.text
    target = args.target
    source = args.source
    payload = {"text": text, "target_lang": target, "source_lang": source}
    route = _discover_and_route("translation", payload)

    from general_ludd.language.core import Translator

    translator = Translator()
    result = translator.translate(text, source_lang=source, target_lang=target)

    output = {
        "route": route,
        "result": {
            "source_lang": result.source_lang,
            "target_lang": result.target_lang,
            "translated_text": result.translated_text,
            "confidence": result.confidence,
        },
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print(f"Source ({source}): {text}")
        print(f"Target ({target}): {result.translated_text}")
        print(f"Confidence: {result.confidence}")


def _cmd_transliterate(args: argparse.Namespace) -> None:
    text = args.text
    target_script = args.target_script
    scheme = args.scheme
    payload = {"text": text, "target_script": target_script, "scheme": scheme}
    route = _discover_and_route("transliteration", payload)

    from general_ludd.language.core import Transliterator

    transliterator = Transliterator()
    result = transliterator.transliterate(text, target_script=target_script, scheme=scheme)

    output = {
        "route": route,
        "result": {
            "source_script": result.source_script,
            "target_script": result.target_script,
            "transliterated_text": result.transliterated_text,
            "scheme": result.scheme,
        },
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print(f"Source script: {result.source_script}")
        print(f"Target script: {result.target_script}")
        print(f"Original: {text}")
        print(f"Transliterated: {result.transliterated_text}")
        if result.scheme:
            print(f"Scheme: {result.scheme}")


def _cmd_capabilities(args: argparse.Namespace) -> None:
    route = _discover_and_route("language_detection", {"text": ""})
    router = None
    try:
        from general_ludd.dispatch.capabilities import discover_capabilities
        from general_ludd.dispatch.router import CapabilityRouter

        colls_root = Path(__file__).resolve().parent.parent.parent / "collections" / "ansible_collections"
        registry = discover_capabilities(colls_root)
        router = CapabilityRouter(registry)
    except Exception:
        pass

    caps = router.list_capabilities() if router else []
    language_caps = [
        c
        for c in caps
        if any(k in c for k in ("language", "l10n", "i18n", "translate", "detect", "transliterate", "script"))
    ]

    output = {
        "route": route,
        "capabilities": sorted(language_caps),
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print("Language capabilities:")
        for c in sorted(language_caps):
            print(f"  {c}")


def _lang_name(code: str) -> str:
    return {
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "pt": "Portuguese",
        "it": "Italian",
        "ru": "Russian",
        "ar": "Arabic",
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
        "hi": "Hindi",
    }.get(code, code.upper())


def add_language_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    lang_parser = sub.add_parser(
        "language",
        help="Language detection, translation, and transliteration",
    )
    lang_parser.set_defaults(func=None)
    lang_sub = lang_parser.add_subparsers(dest="language_command")

    detect_p = lang_sub.add_parser("detect", help="Detect the language of input text")
    detect_p.add_argument("text", help="Text to detect language for")
    detect_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    detect_p.set_defaults(func=_cmd_detect)

    translate_p = lang_sub.add_parser("translate", help="Translate text to another language")
    translate_p.add_argument("text", help="Text to translate")
    translate_p.add_argument("target", help="Target language code (e.g. en, es, fr)")
    translate_p.add_argument("--source", default="auto", help="Source language (default: auto detect)")
    translate_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    translate_p.set_defaults(func=_cmd_translate)

    transliterate_p = lang_sub.add_parser("transliterate", help="Transliterate text to Latin script")
    transliterate_p.add_argument("text", help="Text to transliterate")
    transliterate_p.add_argument("--target-script", default="Latin", help="Target script (default: Latin)")
    transliterate_p.add_argument("--scheme", default=None, help="Transliteration scheme")
    transliterate_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    transliterate_p.set_defaults(func=_cmd_transliterate)

    caps_p = lang_sub.add_parser("capabilities", help="List registered language capabilities")
    caps_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    caps_p.set_defaults(func=_cmd_capabilities)


__all__ = [
    "add_language_subparser",
]
