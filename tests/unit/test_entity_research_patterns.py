"""Tests for entity research patterns: domain/IP extraction, filing parsing, funding detection."""

from __future__ import annotations

from general_ludd.entity.research_patterns import (
    detect_acquisitions,
    detect_funding_rounds,
    extract_domains,
    extract_ip_addresses,
    parse_companies_house,
    parse_sec_filing,
    research_entity,
)


class TestDomainExtraction:
    def test_simple_domain(self) -> None:
        domains = extract_domains("Visit example.com for more info")
        assert len(domains) == 1
        assert domains[0].domain == "example.com"

    def test_www_subdomain(self) -> None:
        domains = extract_domains("Go to www.example.org now")
        assert len(domains) == 1
        assert domains[0].domain == "example.org"

    def test_https_domain(self) -> None:
        domains = extract_domains("Check https://secure.example.io/landing")
        assert len(domains) == 1
        assert domains[0].domain == "secure.example.io"

    def test_multiple_domains(self) -> None:
        text = "Email us at info@acme.com or visit beta.example.net/products"
        domains = extract_domains(text)
        domain_names = {d.domain for d in domains}
        assert "acme.com" in domain_names
        assert "beta.example.net" in domain_names
        assert len(domains) == 2

    def test_duplicate_domains_removed(self) -> None:
        text = "acme.com is great. Visit acme.com today. www.acme.com too"
        domains = extract_domains(text)
        assert len(domains) == 1
        assert domains[0].domain == "acme.com"

    def test_no_domain(self) -> None:
        assert extract_domains("Just some text without domains") == []

    def test_complex_url(self) -> None:
        domains = extract_domains("API at https://api-v2.staging.company.co.uk/v1/endpoint?q=1")
        assert len(domains) == 1
        assert domains[0].domain == "api-v2.staging.company.co.uk"

    def test_tld_variations(self) -> None:
        for tld in ("com", "org", "net", "io", "co.uk", "gov", "edu", "dev"):
            domains = extract_domains(f"Check example.{tld}")
            assert len(domains) == 1

    def test_email_addresses_as_domains(self) -> None:
        domains = extract_domains("Contact support@company.com for help")
        assert len(domains) == 1
        assert domains[0].domain == "company.com"

    def test_empty_string(self) -> None:
        assert extract_domains("") == []

    def test_html_with_domains(self) -> None:
        html = '<a href="https://partner.finance.com/path">Link</a>'
        domains = extract_domains(html)
        assert len(domains) == 1
        assert domains[0].domain == "partner.finance.com"


class TestIPExtraction:
    def test_single_ipv4(self) -> None:
        ips = extract_ip_addresses("Server at 192.168.1.1")
        assert len(ips) == 1
        assert ips[0].address == "192.168.1.1"

    def test_multiple_ips(self) -> None:
        ips = extract_ip_addresses("DNS: 8.8.8.8 and 8.8.4.4")
        assert len(ips) == 2
        addr_set = {ip.address for ip in ips}
        assert "8.8.8.8" in addr_set
        assert "8.8.4.4" in addr_set

    def test_duplicate_ips_removed(self) -> None:
        ips = extract_ip_addresses("10.0.0.1 is the gateway. Use 10.0.0.1 again.")
        assert len(ips) == 1

    def test_no_ip(self) -> None:
        assert extract_ip_addresses("No IP here") == []

    def test_edge_values(self) -> None:
        ips = extract_ip_addresses("0.0.0.0 and 255.255.255.255")
        assert len(ips) == 2

    def test_invalid_octets_not_matched(self) -> None:
        ips = extract_ip_addresses("999.999.999.999 is not real")
        assert len(ips) == 0

    def test_ip_in_log_format(self) -> None:
        text = "2024-01-15 10:00:01 [INFO] Connection from 203.0.113.42"
        ips = extract_ip_addresses(text)
        assert len(ips) == 1
        assert ips[0].address == "203.0.113.42"


class TestSECFilingParsing:
    def test_cik_extraction(self) -> None:
        filings = parse_sec_filing("Company CIK: 0001234567 registered")
        assert len(filings) == 1
        assert filings[0].cik == "0001234567"

    def test_cik_variants(self) -> None:
        for prefix in ("CIK No. 0001234567", "CIK Number: 0001234567", "CIK #0001234567", "CIK:0001234567"):
            filings = parse_sec_filing(prefix)
            assert len(filings) == 1
            assert filings[0].cik == "0001234567"

    def test_file_number(self) -> None:
        filings = parse_sec_filing("Accession No. 001-12345 filed")
        assert len(filings) == 1
        assert filings[0].file_number == "001-12345"

    def test_form_type(self) -> None:
        filings = parse_sec_filing("Company filed Form 10-K on March 15")
        assert len(filings) == 1
        assert filings[0].form_type == "10-K"

    def test_all_form_types(self) -> None:
        for form in ("10-K", "10-Q", "8-K", "S-1", "S-3", "S-4", "S-8", "13F", "13D", "13G", "4", "3", "5"):
            filings = parse_sec_filing(f"Filed Form {form}")
            assert len(filings) == 1
            assert filings[0].form_type == form

    def test_combined_fields(self) -> None:
        text = "CIK 0001234567 filed Form 10-K with File No. 001-98765"
        filings = parse_sec_filing(text)
        assert len(filings) == 1
        f = filings[0]
        assert f.cik == "0001234567"
        assert f.file_number == "001-98765"
        assert f.form_type == "10-K"

    def test_no_sec_data(self) -> None:
        assert parse_sec_filing("No SEC data here") == []

    def test_partial_sec_data(self) -> None:
        filings = parse_sec_filing("The CIK is 0001234567 but no form mentioned")
        assert len(filings) == 1
        assert filings[0].cik == "0001234567"
        assert filings[0].form_type is None


class TestCompaniesHouseParsing:
    def test_standard_company_number(self) -> None:
        results = parse_companies_house("Company No. 12345678 registered in England")
        assert len(results) == 1
        assert results[0].registration_number == "12345678"

    def test_ni_prefix(self) -> None:
        results = parse_companies_house("NI654321 is the Northern Ireland company")
        assert len(results) == 1
        assert results[0].registration_number == "NI654321"

    def test_sc_prefix(self) -> None:
        results = parse_companies_house("Scottish company SC123456")
        assert len(results) == 1
        assert results[0].registration_number == "SC123456"

    def test_single_letter_prefix(self) -> None:
        results = parse_companies_house("Registration Number: A0001234")
        assert len(results) == 1
        assert results[0].registration_number == "A0001234"

    def test_multiple_company_numbers(self) -> None:
        text = "Companies 12345678 and 87654321 both filed"
        results = parse_companies_house(text)
        assert len(results) == 2

    def test_no_company_number(self) -> None:
        assert parse_companies_house("No company data") == []

    def test_variant_labels(self) -> None:
        for label in ("Company No. 12345678", "Registration No: 12345678", "Company Number: 12345678"):
            results = parse_companies_house(label)
            assert len(results) == 1
            assert results[0].registration_number == "12345678"


class TestFundingRoundDetection:
    def test_series_a(self) -> None:
        rounds = detect_funding_rounds("Startup Inc raised a Series A round")
        assert len(rounds) == 1
        assert rounds[0].round_type == "Series A"

    def test_series_b(self) -> None:
        rounds = detect_funding_rounds("Series B funding of $50M announced")
        assert len(rounds) == 1
        assert rounds[0].round_type == "Series B"

    def test_series_c(self) -> None:
        rounds = detect_funding_rounds("The Series C investment round closed")
        assert len(rounds) == 1
        assert rounds[0].round_type == "Series C"

    def test_all_series(self) -> None:
        for letter in "ABCDEFG":
            rounds = detect_funding_rounds(f"Company raised Series {letter}")
            assert len(rounds) == 1
            assert rounds[0].round_type == f"Series {letter}"

    def test_seed_round(self) -> None:
        rounds = detect_funding_rounds("Seed funding of $2M")
        assert len(rounds) == 1
        assert rounds[0].round_type == "Seed"

    def test_pre_seed(self) -> None:
        rounds = detect_funding_rounds("Pre-Seed round closed")
        assert len(rounds) == 1
        assert rounds[0].round_type == "Pre-Seed"

    def test_angel_round(self) -> None:
        rounds = detect_funding_rounds("Angel investors participated")
        assert len(rounds) == 1
        assert rounds[0].round_type == "Angel"

    def test_bridge_round(self) -> None:
        rounds = detect_funding_rounds("Bridge round of $10M")
        assert len(rounds) == 1

    def test_growth_round(self) -> None:
        rounds = detect_funding_rounds("Growth round led by Tiger Global")
        assert len(rounds) == 1

    def test_amount_extraction(self) -> None:
        rounds = detect_funding_rounds("Series A funding of $25M raised")
        assert len(rounds) >= 1
        amount_found = any(r.amount is not None and "25" in r.amount for r in rounds)
        assert amount_found

    def test_multiple_funding_rounds(self) -> None:
        text = "After the Seed round, the company went on to raise a Series A of $15M"
        rounds = detect_funding_rounds(text)
        assert len(rounds) >= 2

    def test_no_funding(self) -> None:
        assert detect_funding_rounds("No funding information here") == []

    def test_dollar_amount_variants(self) -> None:
        for amount_text in ("$5M round", "$10 million funding", "$100K seed", "$1.5B round"):
            rounds = detect_funding_rounds(amount_text)
            assert len(rounds) >= 1


class TestAcquisitionDetection:
    def test_acquired_by(self) -> None:
        acqs = detect_acquisitions("Acme Corp acquired by BigCorp for $100M")
        assert len(acqs) == 1
        assert acqs[0].acquirer == "BigCorp"
        assert acqs[0].action == "acquired"

    def test_acquisition_of(self) -> None:
        acqs = detect_acquisitions("Acquisition of StartUp by MegaCorp")
        assert len(acqs) == 1
        assert acqs[0].acquirer == "MegaCorp"

    def test_merged_with(self) -> None:
        acqs = detect_acquisitions("Company Alpha merged with Company Beta")
        assert len(acqs) == 1
        assert acqs[0].acquirer == "Company Beta"
        assert acqs[0].action == "merged"

    def test_merger_with(self) -> None:
        acqs = detect_acquisitions("Merger with GlobalTech announced")
        assert len(acqs) == 1
        assert acqs[0].acquirer == "GlobalTech"
        assert acqs[0].action == "merged"

    def test_purchased_by(self) -> None:
        acqs = detect_acquisitions("The division was purchased by PrivateEquity Co")
        assert len(acqs) == 1
        assert acqs[0].acquirer == "PrivateEquity Co"

    def test_takeover_of(self) -> None:
        acqs = detect_acquisitions("Hostile takeover of TargetCorp by AcquirerInc")
        assert len(acqs) == 1
        assert acqs[0].acquirer == "AcquirerInc"

    def test_buyout_of(self) -> None:
        acqs = detect_acquisitions("Management buyout of SubCorp")
        assert len(acqs) == 1
        assert acqs[0].acquirer == "SubCorp"

    def test_multiple_acquisitions(self) -> None:
        text = "Acme was acquired by BigCorp, then merged with GiantInc"
        acqs = detect_acquisitions(text)
        assert len(acqs) >= 2

    def test_no_acquisition(self) -> None:
        assert detect_acquisitions("No M&A activity here") == []

    def test_acquirer_name_with_special_chars(self) -> None:
        acqs = detect_acquisitions("acquired by A&B Partners, LLC")
        assert len(acqs) >= 1

    def test_real_news_headline(self) -> None:
        text = "Tech Giant Google acquired AI StartUp DeepMind for $500M"
        acqs = detect_acquisitions(text)
        assert len(acqs) == 1
        assert acqs[0].acquirer == "AI StartUp DeepMind"


class TestResearchEntityIntegration:
    def test_research_entity_combines_all(self) -> None:
        text = (
            "Acme Corp (acme.com, CIK 0001234567) is a Delaware company "
            "in the technology sector. The company raised Series A funding "
            "of $25M led by investors at 192.168.1.1. "
            "In 2023, Acme Corp was acquired by BigCorp for $100M. "
            "UK subsidiary registered as Company No. 12345678."
        )
        result = research_entity(text)
        assert len(result.domains) >= 1
        assert len(result.sec_filings) >= 1
        assert len(result.companies_house_records) >= 1
        assert len(result.funding_rounds) >= 1
        assert len(result.acquisitions) >= 1
        assert len(result.ip_addresses) >= 1

    def test_research_entity_empty(self) -> None:
        result = research_entity("")
        assert result.domains == []
        assert result.ip_addresses == []
        assert result.sec_filings == []
        assert result.companies_house_records == []
        assert result.funding_rounds == []
        assert result.acquisitions == []
        assert result.raw_text == ""

    def test_research_entity_raw_text_preserved(self) -> None:
        text = "Some research text"
        result = research_entity(text)
        assert result.raw_text == text

    def test_research_complex_entity(self) -> None:
        text = (
            "Microsoft Corporation (www.microsoft.com, CIK: 789019) "
            "filed Form 10-K at File No. 001-37845. The company has IP "
            "range 131.107.0.0 to 131.107.255.255. "
            "Microsoft acquired LinkedIn for $26.2 billion. "
            "Microsoft also acquired GitHub and merged with Skype. "
            "The company's Series X funding (internal investment) was $1B."
        )
        result = research_entity(text)
        assert any(d.domain == "microsoft.com" for d in result.domains)
        assert any(f.cik == "789019" for f in result.sec_filings)
        assert len(result.acquisitions) >= 2
