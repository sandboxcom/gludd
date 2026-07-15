"""``gludd language`` CLI subcommand — Language Expert operations.

Provides four subcommands:
  detect-encoding     Detect character encoding of a file
  scan-homoglyphs     Scan text for confusable/homoglyph characters
  detect-bom          Detect and handle Byte Order Marks
  phonetic-transcribe Convert text to phonetic representations

Each handler calls into the corresponding knowledge module under
``general_ludd.language.*`` and prints a JSON artifact.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from typing import Any


def _cmd_language_detect_encoding(args: argparse.Namespace) -> None:
    from general_ludd.language.charset_map import (
        ALL_ENCODINGS,
        CHARDET_CONFIDENCE_THRESHOLDS,
        MOJIBAKE_SIGNATURES,
    )

    if not args.file:
        print("Error: detect-encoding requires a FILE argument", file=sys.stderr)
        sys.exit(1)

    filepath = args.file
    if not os.path.isfile(filepath):
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    result: dict[str, object] = {"file": filepath}
    data: bytes | None = None

    try:
        with open(filepath, "rb") as f:
            data = f.read()
    except OSError as exc:
        result["error"] = str(exc)
        print(json.dumps(result, indent=2))
        return

    result["file_size"] = len(data)

    detected_encoding = "unknown"
    confidence = 0.0
    try:
        import chardet
        detection = chardet.detect(data)
        detected_encoding = detection.get("encoding", "unknown") or "unknown"
        confidence = detection.get("confidence", 0.0) or 0.0
    except ImportError:
        detected_encoding = "unknown"
        confidence = 0.0

    result["detected_encoding"] = detected_encoding
    result["confidence"] = confidence

    def _confidence_level(conf: float) -> str:
        if conf >= CHARDET_CONFIDENCE_THRESHOLDS.get("trusted", 0.95):
            return "trusted"
        if conf >= CHARDET_CONFIDENCE_THRESHOLDS.get("reliable", 0.80):
            return "reliable"
        if conf >= CHARDET_CONFIDENCE_THRESHOLDS.get("usable", 0.50):
            return "usable"
        return "entry"

    result["confidence_level"] = _confidence_level(confidence)
    result["converted_preview"] = ""
    try:
        preview = data[:200].decode(detected_encoding if detected_encoding != "unknown" else "utf-8", errors="replace")
        result["converted_preview"] = preview
    except (LookupError, UnicodeDecodeError):
        pass

    if getattr(args, "detect_mojibake", False):
        try:
            text = data.decode("utf-8", errors="replace")
            found_pat: str | None = None
            for sig_name, sig_patterns_array in MOJIBAKE_SIGNATURES.items():
                if isinstance(sig_patterns_array, list):
                    for pat_str in sig_patterns_array:
                        if isinstance(pat_str, str) and pat_str in text:
                            found_pat = sig_name
                            break
                    if found_pat:
                        break
            result["mojibake_detected"] = found_pat is not None
            result["mojibake_pattern"] = found_pat
        except (UnicodeDecodeError, LookupError):
            result["mojibake_detected"] = True
            result["mojibake_pattern"] = "decode_error"

    if getattr(args, "list_encodings", False):
        result["supported_encodings"] = [e["name"] for e in ALL_ENCODINGS]

    print(json.dumps(result, indent=2))


def _cmd_language_scan_homoglyphs(args: argparse.Namespace) -> None:
    from general_ludd.language.homoglyph_data import (
        ATTACK_VECTORS,
        HOMOGLYPH_GROUPS,
        INVISIBLE_CHARACTERS,
    )

    text = args.text or ""
    if not text:
        result: dict[str, object] = {
            "input_length": 0,
            "findings": [],
            "attack_vectors": [],
            "safe": True,
            "total_findings": 0,
            "severity_counts": {},
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    result = {"input_length": len(text)}
    min_sev_str: str = getattr(args, "min_severity", "low") or "low"
    SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    min_sev = SEVERITY_RANK.get(min_sev_str, 0)

    cp_to_skeleton: dict[int, str] = {}
    for group in HOMOGLYPH_GROUPS:
        for cp, _name in group["characters"]:
            cp_to_skeleton[cp] = group["skeleton"]

    invisible_set: set[int] = {inv["codepoint"] for inv in INVISIBLE_CHARACTERS}
    invisible_map: dict[int, dict[str, object]] = {
        inv["codepoint"]: dict(inv) for inv in INVISIBLE_CHARACTERS
    }

    findings: list[dict[str, object]] = []
    for idx, ch in enumerate(text):
        cp = ord(ch)
        hex_cp = f"U+{cp:04X}"

        if cp in invisible_set:
            inv_info = invisible_map.get(cp, {})
            sev = "high" if inv_info.get("category") == "bidi-control" else "medium"
            findings.append({
                "type": "invisible",
                "severity": sev,
                "character": ch,
                "codepoint": hex_cp,
                "position": idx,
                "name": inv_info.get("short_name", ""),
                "description": str(inv_info.get("risk", "")),
            })
            continue

        if cp in cp_to_skeleton:
            skeleton = cp_to_skeleton[cp]
            if skeleton and skeleton != ch:
                try:
                    import unicodedata
                    name = unicodedata.name(ch, "")
                except (ImportError, ValueError):
                    name = ""
                findings.append({
                    "type": "confusable",
                    "severity": "medium",
                    "character": ch,
                    "codepoint": hex_cp,
                    "position": idx,
                    "skeleton": skeleton,
                    "name": name,
                    "description": f"Looks like '{skeleton}' but is {name or 'unknown'}",
                })

    filtered = [f for f in findings if SEVERITY_RANK.get(str(f.get("severity", "low")), 0) >= min_sev]
    result["findings"] = filtered

    attack_vectors_out: list[dict[str, str]] = []
    text_lower = text.lower()
    for vec_name, vec_desc in ATTACK_VECTORS.items():
        for keyword in vec_desc.lower().split():
            if keyword in text_lower:
                attack_vectors_out.append({"vector": vec_name, "description": vec_desc})
                break
    result["attack_vectors"] = attack_vectors_out

    total = len(filtered)
    result["total_findings"] = total
    sev_counts: dict[str, int] = {}
    for f in filtered:
        sev = str(f["severity"])
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
    result["severity_counts"] = sev_counts
    result["safe"] = total == 0

    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_language_detect_bom(args: argparse.Namespace) -> None:
    from general_ludd.language.charset_map import (
        BOM_OPTIONAL_BY_RFC,
        BOM_REQUIRED_BY_RFC,
        BOM_SIGNATURES,
    )

    filepath = args.file
    if not os.path.isfile(filepath):
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    result: dict[str, object] = {"file": filepath}
    data: bytes | None = None

    try:
        with open(filepath, "rb") as f:
            data = f.read()
    except OSError as exc:
        result["error"] = str(exc)
        print(json.dumps(result, indent=2))
        return

    result["file_size"] = len(data)

    bom_found: str | None = None
    for name, sig in BOM_SIGNATURES.items():
        if data[: len(sig)] == sig:
            bom_found = name
            break

    if bom_found:
        result["bom_found"] = True
        result["bom_type"] = bom_found
        result["bom_encoding"] = bom_found.replace("_BOM", "").replace("_", "-")
        bom_size = len(BOM_SIGNATURES[bom_found])
        result["bom_size_bytes"] = bom_size
        result["bom_hex"] = " ".join(f"{b:02X}" for b in data[:bom_size])

        rfc_base = bom_found.replace("_BOM", "").replace("_", "-")
        if rfc_base in BOM_REQUIRED_BY_RFC:
            result["rfc_compliance"] = "required"
        elif rfc_base in BOM_OPTIONAL_BY_RFC:
            result["rfc_compliance"] = "optional"
        else:
            result["rfc_compliance"] = "none"

        if getattr(args, "strip", False):
            stripped = data[len(BOM_SIGNATURES[bom_found]):]
            result["stripped_preview"] = str(stripped[:100])
            with contextlib.suppress(LookupError, UnicodeDecodeError):
                result["stripped_text_preview"] = (
                    stripped[:200]
                    .decode(bom_found.replace("_BOM", "").replace("_", "-"),
                            errors="replace")
                )
    else:
        result["bom_found"] = False
        result["bom_type"] = None

    audit_dir: str = getattr(args, "audit_directory", "") or ""
    if audit_dir:
        audit_results: list[dict[str, object]] = []
        for dir_root, _dirs, files in os.walk(audit_dir):
            for fname in files:
                fpath = os.path.join(dir_root, fname)
                try:
                    with open(fpath, "rb") as f:
                        head = f.read(4)
                    found: str | None = None
                    for name, sig in BOM_SIGNATURES.items():
                        if head[: len(sig)] == sig:
                            found = name
                            break
                    audit_results.append({"file": fpath, "bom": found})
                except OSError:
                    pass
        result["audit_directory"] = audit_dir
        result["audit_file_count"] = len(audit_results)
        result["audit_results"] = audit_results

    print(json.dumps(result, indent=2))


_PHONETIC_METHODS = frozenset({"arpabet", "ipa", "soundex", "metaphone", "double_metaphone"})


def _cmd_language_phonetic_transcribe(args: argparse.Namespace) -> None:
    from general_ludd.language.phonetic_data import (
        ARPABET_TO_IPA,
        CMU_DICT_SUBSET,
        DOUBLE_METAPHONE,
        IPA_CONSONANTS,
        IPA_VOWELS,
        METAPHONE_EXCEPTIONS,
        SOUNDEX_MAPPING,
    )

    text = args.text or ""
    method = getattr(args, "method", "arpabet") or "arpabet"
    if method not in _PHONETIC_METHODS:
        print(f"Error: unknown method '{method}'", file=sys.stderr)
        sys.exit(1)

    if not text:
        result: dict[str, object] = {"input_text": "", "method": method, "words": []}
        print(json.dumps(result, indent=2))
        return

    import re
    WORD_RE = re.compile(r"[A-Za-z]+")
    words = WORD_RE.findall(text)

    def _arpabet_transcribe(word: str) -> list[str]:
        if word in CMU_DICT_SUBSET:
            return [CMU_DICT_SUBSET[word][0]]
        sounds = []
        for ch in word:
            for phoneme in IPA_CONSONANTS + IPA_VOWELS:
                if isinstance(phoneme, dict) and ch.lower() == phoneme.get("ipa", ""):
                    arpa = phoneme.get("arpabet", "")
                    if arpa and arpa not in sounds:
                        sounds.append(arpa)
        return [" ".join(sounds)] if sounds else [word]

    def _soundex(word: str) -> str:
        w = word.upper()
        if not w:
            return ""
        first = w[0]
        code = first
        prev = ""
        for ch in w[1:]:
            mapped = SOUNDEX_MAPPING.get(ch, "")
            if mapped and mapped != prev:
                code += mapped
                prev = mapped
        code = (code + "000")[:4]
        return code

    def _metaphone(word: str, double: bool = False) -> str | dict[str, str]:
        w = word.upper()
        if w in METAPHONE_EXCEPTIONS:
            exc = METAPHONE_EXCEPTIONS[w]
            if isinstance(exc, str):
                return exc
            if isinstance(exc, dict) and double:
                return exc
            return ""
        code = ""
        i = 0
        while i < len(w) and len(code) < 4:
            ch = w[i]
            if i == 0 and w.startswith(("KN", "GN", "PN", "AE", "WR")):
                i += 1
                continue
            if ch in "AEIOU":
                if i == 0:
                    code += ch
            elif ch == "B":
                code += "B"
            elif ch == "C":
                if i + 1 < len(w) and w[i + 1] in "IA":
                    code += "X"
                    i += 1
                elif i + 1 < len(w) and w[i + 1] == "H":
                    code += "X"
                elif i + 1 < len(w) and w[i + 1] == "K":
                    i += 1
                else:
                    code += "K"
            elif ch == "D":
                if i + 1 < len(w) and w[i + 1] in "GJ":
                    code += "J"
                    i += 1
                else:
                    code += "T"
            elif ch in "GJ":
                code += "J"
            elif ch in "PH":
                code += "F"
            elif ch == "Q":
                code += "K"
            elif ch in "SZ":
                if double:
                    code += "S"
                else:
                    code += "S"
            elif ch == "T":
                if i + 1 < len(w) and w[i + 1] == "H":
                    code += "0"
                    i += 1
                elif i + 1 < len(w) and w[i + 1] == "I" and i + 2 < len(w) and w[i + 2] in "AO":
                    code += "X"
                else:
                    code += "T"
            elif ch in "VW":
                code += ch
            elif ch == "X":
                code += "KS"
            elif ch in "LMRNF":
                code += ch
            i += 1
        return code[:4]

    phonetic_result: dict[str, object] = {
        "input_text": text,
        "method": method,
        "word_count": len(words),
        "words": [],
    }
    words_out: list[dict[str, object]] = phonetic_result["words"]  # type: ignore[assignment]

    for word in words:
        entry: dict[str, object] = {"word": word}
        if method == "arpabet":
            entry["transcription"] = _arpabet_transcribe(word)
        elif method == "ipa":
            arpa = _arpabet_transcribe(word)
            ipa_result = []
            for a in arpa:
                tokens = a.split()
                ipa_tokens = [ARPABET_TO_IPA.get(t, t) for t in tokens if t in ARPABET_TO_IPA]
                ipa_result.append("".join(ipa_tokens))
            entry["transcription"] = ipa_result
        elif method == "soundex":
            entry["transcription"] = _soundex(word)
            entry["soundex_code"] = entry["transcription"]
        elif method in ("metaphone", "double_metaphone"):
            mc = DOUBLE_METAPHONE.get(word, _metaphone(word, double=True))
            if method == "double_metaphone":
                entry["metaphone_codes"] = mc
                entry["transcription"] = mc.get("primary", "") if isinstance(mc, dict) else mc
            else:
                entry["transcription"] = mc.get("primary", "") if isinstance(mc, dict) else mc

        words_out.append(entry)

    print(json.dumps(phonetic_result, indent=2, ensure_ascii=False))


def add_language_subparser(subparsers: Any) -> argparse.ArgumentParser:
    language_parser: argparse.ArgumentParser = subparsers.add_parser(
        "language",
        help="Language Expert operations (encoding, homoglyphs, BOM, phonetics)",
    )
    language_parser.set_defaults(func=None)
    lang_sub = language_parser.add_subparsers(dest="language_command")

    detect_enc = lang_sub.add_parser("detect-encoding", help="Detect character encoding of a file")
    detect_enc.add_argument("file", help="File to detect encoding for")
    detect_enc.add_argument("--detect-mojibake", action="store_true", default=False,
                            help="Check for mojibake patterns")
    detect_enc.add_argument("--list-encodings", action="store_true", default=False,
                            help="List all supported encodings")
    detect_enc.set_defaults(func=_cmd_language_detect_encoding)

    scan_homoglyphs = lang_sub.add_parser("scan-homoglyphs", help="Scan text for confusable/homoglyph characters")
    scan_homoglyphs.add_argument("text", help="Text to scan for homoglyphs")
    scan_homoglyphs.add_argument("--min-severity", default="low",
                                  choices=["low", "medium", "high", "critical"],
                                  help="Minimum severity threshold (default: low)")
    scan_homoglyphs.set_defaults(func=_cmd_language_scan_homoglyphs)

    detect_bom = lang_sub.add_parser("detect-bom", help="Detect and handle Byte Order Marks")
    detect_bom.add_argument("file", help="File to detect BOM for")
    detect_bom.add_argument("--strip", action="store_true", default=False,
                            help="Strip BOM from file (preview)")
    detect_bom.add_argument("--audit-directory", default="",
                            help="Directory to audit for BOMs across all files")
    detect_bom.set_defaults(func=_cmd_language_detect_bom)

    phonetic = lang_sub.add_parser("phonetic-transcribe", help="Convert text to phonetic representations")
    phonetic.add_argument("text", help="Text to transcribe phonetically")
    phonetic.add_argument("--method", default="arpabet",
                           choices=["arpabet", "ipa", "soundex", "metaphone", "double_metaphone"],
                           help="Transcription method (default: arpabet)")
    phonetic.set_defaults(func=_cmd_language_phonetic_transcribe)

    return language_parser
