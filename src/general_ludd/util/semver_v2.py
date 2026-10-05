"""Parse, compare, and match Semantic Versioning 2.0 values."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import total_ordering

_SEMVER_RE = re.compile(
    r"^v?"
    r"(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<build>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

_AND_CLAUSE_RE = re.compile(
    r"(>=|<=|!=|>|<|~|\^|=)?\s*v?\d+(?:\.\d+)*(?:-[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?"
)

_HYPHEN_RANGE_RE = re.compile(
    r"^\s*v?(\d+(?:\.\d+)*(?:-[0-9a-zA-Z.-]+)?(?:\+[0-9a-zA-Z.-]+)?)\s*-\s*v?(\d+(?:\.\d+)*(?:-[0-9a-zA-Z.-]+)?(?:\+[0-9a-zA-Z.-]+)?)\s*$"
)

_PARTIAL_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+)(?:\.(\d+))?)?")

_OP_TO_CMP: dict[str, str] = {
    ">=": "ge",
    "<=": "le",
    "!=": "ne",
    ">": "gt",
    "<": "lt",
    "=": "eq",
}


def _parse_partial(partial: str) -> SemVer:
    m = _PARTIAL_VERSION_RE.match(partial.lstrip("v"))
    if not m:
        raise ValueError(f"cannot parse partial version: {partial!r}")
    major = int(m.group(1))
    minor = int(m.group(2)) if m.group(2) is not None else 0
    patch = int(m.group(3)) if m.group(3) is not None else 0
    return SemVer(major, minor, patch)


@total_ordering
@dataclass(frozen=True, slots=True)
class SemVer:
    """Represent a parsed Semantic Versioning 2.0 value."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str | int, ...] = field(default_factory=tuple)
    build: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        """Render the canonical Semantic Versioning string."""
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base += "-" + ".".join(str(p) for p in self.prerelease)
        if self.build:
            base += "+" + ".".join(self.build)
        return base

    @property
    def is_prerelease(self) -> bool:
        """Return whether the version has prerelease identifiers."""
        return len(self.prerelease) > 0

    @property
    def is_stable(self) -> bool:
        """Return whether the version is stable and has a nonzero major."""
        return self.major > 0 and not self.is_prerelease

    def __eq__(self, other: object) -> bool:
        """Compare versions using SemVer precedence fields."""
        if not isinstance(other, SemVer):
            return NotImplemented
        left = self.major, self.minor, self.patch, self.prerelease
        right = other.major, other.minor, other.patch, other.prerelease
        return left == right

    def __lt__(self, other: SemVer) -> bool:
        """Order this version before another by SemVer precedence."""
        left_core = self.major, self.minor, self.patch
        right_core = other.major, other.minor, other.patch
        if left_core != right_core:
            return left_core < right_core
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease

        return self._cmp_prerelease(other) < 0

    def _cmp_prerelease(self, other: SemVer) -> int:
        a, b = self.prerelease, other.prerelease
        for ai, bi in zip(a, b, strict=False):
            if ai == bi:
                continue
            if isinstance(ai, int) and isinstance(bi, int):
                return -1 if ai < bi else 1
            if isinstance(ai, int):
                return -1
            if isinstance(bi, int):
                return 1
            return -1 if ai < bi else 1
        return (len(a) > len(b)) - (len(a) < len(b))

    def __hash__(self) -> int:
        """Hash the SemVer precedence fields."""
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def bump_major(self) -> SemVer:
        """Return the next major version."""
        return SemVer(self.major + 1, 0, 0)

    def bump_minor(self) -> SemVer:
        """Return the next minor version."""
        return SemVer(self.major, self.minor + 1, 0)

    def bump_patch(self) -> SemVer:
        """Return the next patch version."""
        return SemVer(self.major, self.minor, self.patch + 1)

    def with_prerelease(self, ident: str) -> SemVer:
        """Return a copy with parsed prerelease identifiers."""
        return SemVer(
            self.major,
            self.minor,
            self.patch,
            prerelease=tuple(int(part) if part.isdigit() else part for part in ident.split(".")),
            build=self.build,
        )

    def with_build(self, ident: str) -> SemVer:
        """Return a copy with build metadata identifiers."""
        return SemVer(
            self.major,
            self.minor,
            self.patch,
            prerelease=self.prerelease,
            build=tuple(ident.split(".")),
        )

    def satisfies(self, spec: str) -> bool:
        """Return whether this version satisfies a range specification."""
        return Satisfier.satisfies(self, spec)


def parse(version: str) -> SemVer:
    """Parse a complete Semantic Versioning 2.0 string."""
    m = _SEMVER_RE.match(version)
    if not m:
        raise ValueError(f"invalid semver: {version!r}")

    major = int(m.group("major"))
    minor = int(m.group("minor"))
    patch = int(m.group("patch"))

    prerelease_text = m.group("prerelease")
    prerelease = (
        tuple(int(part) if part.isdigit() else part for part in prerelease_text.split("."))
        if prerelease_text
        else ()
    )
    build_str = m.group("build")
    build = tuple(build_str.split(".")) if build_str else ()
    return SemVer(major, minor, patch, prerelease, build)


def coerce(version: str) -> SemVer:
    """Coerce an arbitrary string containing version digits to SemVer."""
    m = _SEMVER_RE.match(version)
    if m:
        return parse(version)
    digits = re.findall(r"\d+", version)
    if not digits:
        raise ValueError(f"cannot coerce to semver: {version!r}")
    return parse(".".join([*digits, "0", "0"][:3]))


class Satisfier:
    """Evaluate Semantic Versioning range specifications."""

    @staticmethod
    def satisfies(version: SemVer, spec: str) -> bool:
        """Return whether a version satisfies a range specification."""
        trimmed = spec.strip()
        if not trimmed:
            return True
        if trimmed in ("*", "x", "X"):
            return True

        or_parts = [p.strip() for p in trimmed.split("||")]
        if len(or_parts) > 1:
            return any(Satisfier.satisfies(version, p) for p in or_parts)

        hyphen_match = _HYPHEN_RANGE_RE.match(trimmed)
        if hyphen_match:
            return Satisfier._hyphen_range(
                version,
                hyphen_match.group(1),
                hyphen_match.group(2),
            )

        and_clauses = Satisfier._extract_and_clauses(trimmed)
        if len(and_clauses) > 1:
            return all(Satisfier._evaluate_atomic(version, c) for c in and_clauses)

        return Satisfier._evaluate_atomic(version, trimmed)

    @staticmethod
    def _extract_and_clauses(spec: str) -> list[str]:
        clauses: list[str] = []
        for m in _AND_CLAUSE_RE.finditer(spec):
            clauses.append(m.group(0))
        if not clauses:
            return []
        if len(clauses) == 1:
            if clauses[0].strip() == spec.strip():
                return clauses
            return [spec.strip()]
        reconstructed = " ".join(clauses)
        if reconstructed != spec:
            return [spec.strip()]
        return clauses

    @staticmethod
    def _evaluate_atomic(version: SemVer, spec: str) -> bool:
        spec = spec.strip()
        if spec in ("*", "x", "X"):
            return True

        if spec.startswith("~"):
            return Satisfier._tilde(version, spec)
        if spec.startswith("^"):
            return Satisfier._caret(version, spec)
        if spec.startswith(">=") or spec.startswith("<=") or spec.startswith("!="):
            op = spec[:2]
            ref_str = spec[2:].lstrip("v").strip()
            return Satisfier._cmp(version, op, ref_str)
        if spec.startswith(">") or spec.startswith("<"):
            op = spec[:1]
            ref_str = spec[1:].lstrip("v").strip()
            return Satisfier._cmp(version, op, ref_str)
        if spec.startswith("="):
            ref_str = spec[1:].lstrip("v").strip()
            return Satisfier._cmp(version, "=", ref_str)

        if spec[0].isdigit() or spec[0] == "v":
            ref = parse(spec.lstrip("v"))
            return version == ref

        raise ValueError(f"unrecognized range spec: {spec!r}")

    @staticmethod
    def _cmp(version: SemVer, op: str, ref_str: str) -> bool:
        ref = parse(ref_str) if ref_str else version
        method = _OP_TO_CMP.get(op, "eq")
        return bool(getattr(version, f"__{method}__")(ref))

    @staticmethod
    def _tilde(version: SemVer, spec: str) -> bool:
        ref_str = spec[1:].lstrip("v")
        ref = _parse_partial(ref_str)
        dot_count = ref_str.count(".")

        if not (version >= ref):
            return False

        ceiling = SemVer(ref.major, ref.minor + 1, 0) if dot_count >= 1 else SemVer(ref.major + 1, 0, 0)

        return version < ceiling

    @staticmethod
    def _caret(version: SemVer, spec: str) -> bool:
        ref_str = spec[1:].lstrip("v")
        ref = _parse_partial(ref_str)

        if not (version >= ref):
            return False

        if ref.major != 0:
            ceiling = SemVer(ref.major + 1, 0, 0)
        elif ref.minor != 0:
            ceiling = SemVer(0, ref.minor + 1, 0)
        else:
            ceiling = SemVer(0, 0, ref.patch + 1)
        return version < ceiling

    @staticmethod
    def _hyphen_range(version: SemVer, lo: str, hi: str) -> bool:
        lo_ver = _parse_partial(lo.strip())
        hi_ver = _parse_partial(hi.strip())
        return version >= lo_ver and version <= hi_ver


def max_satisfying(versions: list[str], spec: str) -> str | None:
    """Return the greatest valid version satisfying a specification."""
    parsed: list[SemVer] = []
    for v in versions:
        try:
            pv = parse(v)
            parsed.append(pv)
        except ValueError:
            continue
    if not parsed:
        return None
    parsed.sort(reverse=True)
    for pv in parsed:
        if pv.satisfies(spec):
            return str(pv)
    return None


def sort_versions(versions: list[str]) -> list[str]:
    """Return version strings sorted by Semantic Versioning precedence."""
    parsed = [(parse(v), v) for v in versions]
    parsed.sort(key=lambda x: x[0])
    return [v for _, v in parsed]
