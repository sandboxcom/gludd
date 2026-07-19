from __future__ import annotations

import re
from dataclasses import dataclass, field

DOMAIN_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)

IP_PATTERN = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

SEC_EDGAR_CIK_PATTERN = re.compile(r"CIK\s*(?:No\.?|Number|#|is)?\s*[:.]?\s*(\d{6,10})", re.IGNORECASE)
SEC_EDGAR_FILE_PATTERN = re.compile(
    r"(?:Accession|File)\s*(?:No\.?|Number|#)?\s*[:.]?\s*(\d{1,3}-\d{1,6})",
    re.IGNORECASE,
)
SEC_EDGAR_FORM_PATTERN = re.compile(
    r"Form\s+(10-K|10-Q|8-K|S-1|S-3|S-4|S-8|13F|13D|13G|4|3|5)", re.IGNORECASE
)

COMPANIES_HOUSE_PATTERN = re.compile(
    r"(?:(?:Company|Companies|Registration)\s*(?:No\.?|Number|#)?\s*[:.]?\s*)?"
    r"\b([A-Z]{2}\d{6}|[A-Z]\d{7}|\d{8}\b)",
    re.IGNORECASE,
)

FUNDING_ROUND_PATTERN = re.compile(
    r"(Series\s+[A-G]|Seed|Pre-Seed|Angel|Bridge|Growth|IPO\s+Round|"
    r"Series\s+[A-G]\d?|\$\d+(?:\.\d+)?\s*(?:[MBK]|million|billion)?\s*(?:funding|round|raised|seed))",
    re.IGNORECASE,
)

FUNDING_AMOUNT_PATTERN = re.compile(
    r"\$\s*(\d+(?:\.\d+)?)\s*(million|billion|[MBK])\b", re.IGNORECASE
)

ACQUISITION_PATTERN = re.compile(
    r"(acquired\s+by|acquisition\s+of|merged\s+with|merger\s+with|"
    r"purchased\s+by|takeover\s+of|buyout\s+of)\s+([A-Z][A-Za-z0-9&.]{1,59})",
    re.IGNORECASE,
)


@dataclass
class ExtractedDomain:
    domain: str
    protocol: str | None = None
    path: str | None = None


@dataclass
class ExtractedIP:
    address: str


@dataclass
class SECFiling:
    cik: str | None = None
    file_number: str | None = None
    form_type: str | None = None


@dataclass
class CompaniesHouseRecord:
    registration_number: str


@dataclass
class FundingRound:
    round_type: str
    amount: str | None = None
    raw_text: str = ""


@dataclass
class AcquisitionInfo:
    acquirer: str
    action: str = "acquired"
    raw_text: str = ""


@dataclass
class EntityResearchResult:
    domains: list[ExtractedDomain] = field(default_factory=list)
    ip_addresses: list[ExtractedIP] = field(default_factory=list)
    sec_filings: list[SECFiling] = field(default_factory=list)
    companies_house_records: list[CompaniesHouseRecord] = field(default_factory=list)
    funding_rounds: list[FundingRound] = field(default_factory=list)
    acquisitions: list[AcquisitionInfo] = field(default_factory=list)
    raw_text: str = ""


def extract_domains(text: str) -> list[ExtractedDomain]:
    results: list[ExtractedDomain] = []
    seen: set[str] = set()
    for match in DOMAIN_PATTERN.finditer(text):
        domain = match.group(1).lower()
        if domain not in seen:
            seen.add(domain)
            results.append(ExtractedDomain(domain=domain))
    return results


def extract_ip_addresses(text: str) -> list[ExtractedIP]:
    results: list[ExtractedIP] = []
    seen: set[str] = set()
    for match in IP_PATTERN.finditer(text):
        addr = match.group(0)
        if addr not in seen:
            seen.add(addr)
            results.append(ExtractedIP(address=addr))
    return results


def parse_sec_filing(text: str) -> list[SECFiling]:
    cik_match = SEC_EDGAR_CIK_PATTERN.search(text)
    file_match = SEC_EDGAR_FILE_PATTERN.search(text)
    form_match = SEC_EDGAR_FORM_PATTERN.search(text)
    if not (cik_match or file_match or form_match):
        return []
    return [
        SECFiling(
            cik=cik_match.group(1) if cik_match else None,
            file_number=file_match.group(1) if file_match else None,
            form_type=form_match.group(1) if form_match else None,
        )
    ]


def parse_companies_house(text: str) -> list[CompaniesHouseRecord]:
    results: list[CompaniesHouseRecord] = []
    for match in COMPANIES_HOUSE_PATTERN.finditer(text):
        results.append(CompaniesHouseRecord(registration_number=match.group(1)))
    return results


def detect_funding_rounds(text: str) -> list[FundingRound]:
    results: list[FundingRound] = []
    for match in FUNDING_ROUND_PATTERN.finditer(text):
        round_type = match.group(1)
        amount_match = FUNDING_AMOUNT_PATTERN.search(
            text[match.start():match.start() + 200]
        )
        results.append(
            FundingRound(
                round_type=round_type,
                amount=amount_match.group(0) if amount_match else None,
                raw_text=match.group(0),
            )
        )
    return results


def detect_acquisitions(text: str) -> list[AcquisitionInfo]:
    results: list[AcquisitionInfo] = []
    for match in ACQUISITION_PATTERN.finditer(text):
        action_word = match.group(1).strip().lower()
        if "merged" in action_word:
            action = "merged"
        elif (
            "acquired" in action_word
            or "purchased" in action_word
            or "buyout" in action_word
            or "takeover" in action_word
        ):
            action = "acquired"
        else:
            action = action_word
        results.append(
            AcquisitionInfo(
                acquirer=match.group(2).strip(),
                action=action,
                raw_text=match.group(0),
            )
        )
    return results


def research_entity(text: str) -> EntityResearchResult:
    return EntityResearchResult(
        domains=extract_domains(text),
        ip_addresses=extract_ip_addresses(text),
        sec_filings=parse_sec_filing(text),
        companies_house_records=parse_companies_house(text),
        funding_rounds=detect_funding_rounds(text),
        acquisitions=detect_acquisitions(text),
        raw_text=text,
    )
