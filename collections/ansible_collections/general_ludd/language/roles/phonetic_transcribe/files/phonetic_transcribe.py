#!/usr/bin/env python3
"""phonetic_transcribe — Convert text to phonetic representations.

Supports: ARPABET transcription, IPA conversion, Soundex encoding,
Metaphone / Double Metaphone hashing, and CMU Pronouncing Dictionary lookup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys


WORD_RE = re.compile(r"[A-Za-z]+")


def _add_src_to_path():
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "..", "..", "..", "..", "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def transcribe(args: argparse.Namespace) -> dict[str, object]:
    _add_src_to_path()
    from general_ludd.language.phonetic_data import (  # type: ignore[import-not-at-top-of-file]
        ARPABET_STRESS,
        ARPABET_TO_IPA,
        CMU_DICT_SUBSET,
        DOUBLE_METAPHONE,
        IPA_CONSONANTS,
        IPA_VOWELS,
        METAPHONE_EXCEPTIONS,
        SOUNDEX_IGNORE,
        SOUNDEX_MAPPING,
        SOUNDEX_VOWELS,
    )

    def _arpabet_transcribe(word: str) -> list[str]:
        if word in CMU_DICT_SUBSET:
            return [CMU_DICT_SUBSET[word][0]]
        sounds = []
        for ch in word:
            for phoneme in IPA_CONSONANTS + IPA_VOWELS:
                if ch.lower() == phoneme["ipa"] and phoneme["arpabet"] not in sounds:
                    sounds.append(phoneme["arpabet"])
        return [" ".join(sounds)] if sounds else [word]

    def _ipa_from_arpabet(arpa_phonemes: list[str]) -> str:
        result = []
        for p in arpa_phonemes:
            key = p.rstrip("012")
            result.append(ARPABET_TO_IPA.get(key, key))
        return "".join(result)

    def _soundex_code(word: str) -> str:
        w = word.lower()
        code = w[0].upper()
        prev = ""
        for ch in w[1:]:
            if ch in SOUNDEX_IGNORE:
                continue
            if ch in SOUNDEX_VOWELS:
                prev = ""
                continue
            digit = SOUNDEX_MAPPING.get(ch, "")
            if digit and digit != prev:
                code += digit
                prev = digit
        return (code + "000")[:4]

    def _metaphone_code(word: str, double: bool) -> dict[str, object]:
        w = word.lower()
        if w[:2] in METAPHONE_EXCEPTIONS:
            w = METAPHONE_EXCEPTIONS[w[:2]] + w[2:]
        primary = ""
        i = 0
        while i < len(w) and len(primary) < 4:
            ch = w[i]
            if i == 0 and ch in "aeiou":
                primary += ch
                i += 1
                continue
            pair = w[i:i + 2] if i + 1 < len(w) else ""
            if pair in DOUBLE_METAPHONE:
                primary += DOUBLE_METAPHONE[pair][0]
                i += len(pair) if len(pair) > 1 else 1
            elif ch in "aeiou":
                i += 1
            else:
                primary += ch
                i += 1
        if double:
            alternate = primary[:3] + ("K" if primary[-1:] == "X" else primary[-1:]) if len(primary) > 1 else primary
            return {"primary": primary[:4], "alternate": alternate[:4]}
        return {"primary": primary[:4]}

    text = args.input
    method = args.method

    result: dict[str, object] = {"input_text": text, "method": method, "words": []}
    words = WORD_RE.findall(text.upper())

    for word in words:
        entry: dict[str, object] = {"word": word}

        if args.cmu_dict_lookup and word in CMU_DICT_SUBSET:
            entry["cmu_transcription"] = CMU_DICT_SUBSET[word]

        if method == "arpabet":
            arpa = _arpabet_transcribe(word)
            entry["transcription"] = arpa[0] if arpa else word
            stress = []
            for ph in (arpa[0].split() if arpa else []):
                digits = "".join(c for c in ph if c.isdigit())
                if digits:
                    stress.append({
                        "phoneme": ph,
                        "stress": ARPABET_STRESS.get(digits, "unknown"),
                    })
            entry["stress_pattern"] = stress

        elif method == "ipa":
            if word in CMU_DICT_SUBSET:
                arpa_phonemes = CMU_DICT_SUBSET[word][0].split()
                entry["transcription"] = _ipa_from_arpabet(arpa_phonemes)
            else:
                entry["transcription"] = word.lower()

        elif method == "soundex":
            code = _soundex_code(word)
            entry["soundex_code"] = code
            entry["transcription"] = code

        elif method in ("metaphone", "double_metaphone"):
            mc = _metaphone_code(word, double=(method == "double_metaphone"))
            if method == "double_metaphone":
                entry["metaphone_codes"] = mc
            else:
                entry["metaphone_code"] = mc.get("primary", "")
            entry["transcription"] = entry.get(
                "metaphone_code", ""
            )

        result["words"].append(entry)  # type: ignore[attr-defined]

    if args.cmu_dict_lookup and len(words) > 1:
        homophones: dict[str, list[str]] = {}
        for w in words:
            if w in CMU_DICT_SUBSET:
                key = CMU_DICT_SUBSET[w][0]
                homophones.setdefault(key, []).append(w)
        result["homophones"] = {
            k: v for k, v in homophones.items() if len(v) > 1
        }

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe text to phonetic representations"
    )
    parser.add_argument("--input", default="", help="Text to transcribe")
    parser.add_argument("--output", default="-", help="Output JSON path (default: stdout)")
    parser.add_argument("--format", default="json", choices=["json"], help="Output format")
    parser.add_argument("--method", default="arpabet",
                        choices=["arpabet", "ipa", "soundex", "metaphone", "double_metaphone"])
    parser.add_argument("--cmu-dict-lookup", action="store_true", default=True)

    args = parser.parse_args()

    try:
        result = transcribe(args)
    except Exception as exc:
        result = {"input_text": args.input, "error": str(exc)}

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output == "-":
        print(output)
    else:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output: {args.output}")

    sys.exit(0)


if __name__ == "__main__":
    main()
