# Business Data Sources & Research Methodology

How the `general_ludd.business.entity_research` role discovers organizations,
maps corporate networks, inventories digital assets, assesses exposure, and
scores risks — all through structured queries against public APIs.

The canonical data source registry is `collections/ansible_collections/general_ludd/business/roles/entity_research/vars/data_sources.yml`.
Role reference and usage examples: `docs/BUSINESS_RESEARCH_SYSTEM.md`.

---

## 1. Data Source Catalog

All 18 sources from `data_sources.yml`. Each source belongs to one or more
research categories: **discovery**, **associations**, **assets**, **exposure**,
**risks**, **demographics**.

### 1.1 Legal / Registration

#### OpenCorporates
- **Endpoint:** `https://api.opencorporates.com/v0.4`
- **Auth:** API key in header `Authorization: Bearer <key>`
- **Rate limit:** 500 req/day free tier; unlimited with paid plan
- **Data quality:** Official company registries (160+ jurisdictions). Updates
  reflect registry filing schedules — hours to weeks depending on jurisdiction.
- **Categories:** discovery, associations

#### SEC EDGAR
- **Endpoint:** `https://data.sec.gov`
- **Auth:** User-Agent header required (SEC policy). No API key for basic access.
- **Rate limit:** 10 req/s enforced by SEC; submissions API returns current quarter filings.
- **Data quality:** Official US public company filings. 10-K, 10-Q, 8-K, proxy statements
  (DEF 14A), insider transactions (Forms 3/4/5). Filing lag: hours after acceptance.
- **Categories:** discovery, assets, risks

#### UK Companies House
- **Endpoint:** `https://api.company-information.service.gov.uk`
- **Auth:** API key from `developer.company-information.service.gov.uk` (free registration).
- **Rate limit:** 600 req/5 min. 500 search results limit.
- **Data quality:** Official UK company registry. Persons with Significant Control (PSC)
  register maintained under Money Laundering Regulations. Daily filing updates.
- **Categories:** discovery, associations

### 1.2 Business Intelligence

#### Crunchbase
- **Endpoint:** `https://api.crunchbase.com/api/v4`
- **Auth:** API key in query parameter `?user_key=<key>`. Requires paid plan for
  entity details beyond autocomplete.
- **Rate limit:** 44 req/min on free tier; 500 req/min on Enterprise.
- **Data quality:** Crowd-sourced + machine-curated private company data. Funding rounds,
  acquisitions, investors, leadership. Freshness: hours (press releases) to months (manual updates).
- **Categories:** discovery, associations, assets, risks

#### Wikipedia
- **Endpoint:** `https://en.wikipedia.org/w/api.php`
- **Auth:** None required. User-Agent per Wikimedia policy.
- **Rate limit:** Parallel requests allowed; no strict hourly cap for well-behaved clients.
  Wikimedia recommends max 200 req/s for search.
- **Data quality:** Community-edited encyclopedia. Organizational articles often include
  founding, leadership, subsidiaries, and controversies. Varies by language edition.
- **Categories:** discovery, associations

### 1.3 Digital Assets

#### WHOIS / RDAP
- **Endpoint:** `https://rdap.org`
- **Auth:** None (public RDAP protocol).
- **Rate limit:** No universal limit; per-server throttling on individual registries.
- **Data quality:** IETF-standardized replacement for WHOIS. Domain registrant data,
  nameservers, IP blocks (ARIN/RIPE/APNIC/LACNIC/AFRINIC). Redacted fields under GDPR
  but organizational records more complete. Near-real-time.
- **Categories:** assets

#### crt.sh
- **Endpoint:** `https://crt.sh`
- **Auth:** None (public certificate transparency logs).
- **Rate limit:** No documented hard limit; practical ~20 req/s before 429.
- **Data quality:** Certificate Transparency log aggregator. Discover subdomains,
  certificate issuers, and issuance history. Near-real-time (logged within hours of CA issuance).
- **Categories:** assets, exposure

#### PeeringDB
- **Endpoint:** `https://www.peeringdb.com/api`
- **Auth:** API key for write operations; read operations are public.
- **Rate limit:** No documented limit for public reads.
- **Data quality:** Community-maintained interconnection database. AS-to-AS peering,
  network presence at IXPs, facility locations. Updated by network operators.
- **Categories:** assets

#### RIPEstat
- **Endpoint:** `https://stat.ripe.net/data`
- **Auth:** None required for basic data views.
- **Rate limit:** 200 req/minute for unauthenticated; 1000 req/minute with RIPE NCC Access.
- **Data quality:** RIPE NCC routing and Internet statistics. AS neighbors, prefix visibility,
  BGP routing history. Near-real-time from RIS collectors.
- **Categories:** assets

### 1.4 Intellectual Property

#### USPTO
- **Endpoint:** `https://developer.uspto.gov/ibd-api/v1`
- **Auth:** API key from developer.uspto.gov (free registration).
- **Rate limit:** Not publicly documented; practical ~10 req/s.
- **Data quality:** United States Patent and Trademark Office. Granted patents, published
  applications, and registered trademarks. Patent publication lag: 18 months from filing.
- **Categories:** assets

#### Espacenet (EPO)
- **Endpoint:** `https://worldwide.espacenet.com/3.2/rest/v1`
- **Auth:** None required for basic search.
- **Rate limit:** Not publicly documented.
- **Data quality:** European Patent Office worldwide patent database. 140+ million patent
  documents from 100+ countries. Updated weekly with new publications.
- **Categories:** assets

#### WIPO
- **Endpoint:** `https://api.wipo.int`
- **Auth:** API key may be required for some endpoints.
- **Rate limit:** Not publicly documented.
- **Data quality:** World Intellectual Property Organization. International trademark
  registrations (Madrid System) and brand database. Updated daily.
- **Categories:** assets

### 1.5 Security / Exposure

#### Shodan
- **Endpoint:** `https://api.shodan.io`
- **Auth:** API key in query parameter or URL path. Paid plans required for search filtering
  and full result sets.
- **Rate limit:** 1 req/s on free tier; unlimited on Enterprise.
- **Data quality:** Internet-wide device and service scanner. Open ports, banners,
  service versions, TLS configuration. Scan frequency: weekly typical, on-demand for
  paid plans. Banners may be stale for assets behind dynamic IPs.
- **Categories:** exposure

#### Censys
- **Endpoint:** `https://search.censys.io/api/v2`
- **Auth:** API ID + Secret (HTTP Basic) from censys.io account.
- **Rate limit:** 120 req/min on free Community tier; commercial plans scale higher.
- **Data quality:** Internet asset discovery. Hosts (IPv4 + services), certificates,
  domains. Daily global scan. Certificate corpus updated within hours of issuance.
- **Categories:** exposure

#### Have I Been Pwned
- **Endpoint:** `https://haveibeenpwned.com/api/v3`
- **Auth:** API key for domain search (requires domain ownership verification).
  Breach name lookup is unauthenticated.
- **Rate limit:** 1 req/1.5s for domain search; unauthenticated endpoints are stricter.
- **Data quality:** Troy Hunt's breach aggregation service. Documented breaches with
  verified data dumps. Breach-to-listing lag: hours to days. Covers publicly leaked
  credentials and PII exposures.
- **Categories:** exposure, risks

### 1.6 Technology Stack

#### BuiltWith
- **Endpoint:** `https://api.builtwith.com`
- **Auth:** API key in `KEY` query parameter. Paid plans required.
- **Rate limit:** Varies by plan; ~200 req/day on free, unlimited on Enterprise.
- **Data quality:** Website technology profiler. JavaScript frameworks, CMS, analytics,
  CDN, hosting, SSL providers. Monthly recrawl typical; on-demand for paid plans.
- **Categories:** assets

### 1.7 Social / Market

#### Glassdoor
- **Endpoint:** `https://www.glassdoor.com`
- **Auth:** Rate-limited web scraping (no public API). Requires User-Agent and session
  cookies for company overview pages.
- **Rate limit:** Aggressive bot detection; practical ~1 req/10s for scraping.
- **Data quality:** Employee-contributed reviews, salaries, interview experiences.
  Self-reported and unverified. Useful for company culture, employee sentiment, and
  hiring trends. Updated continuously.
- **Categories:** risks, demographics

### 1.8 Meta-Search

#### SearX
- **Endpoint:** `{{ entity_research_searx_url }}` (default `http://localhost:8888`)
- **Auth:** None (self-hosted instance). Externally: Tor hidden service or public instance.
- **Rate limit:** Self-imposed; configurable in SearX settings. Typical default: 1 req/s.
- **Data quality:** Aggregates Google, Bing, DuckDuckGo, Wikipedia, and specialized engines.
  Privacy-respecting: no tracking, no profiling, query anonymization. Results as fresh as
  the underlying engines. Used for news monitoring, entity discovery, and cross-referencing.
- **Categories:** discovery, associations, exposure, risks, demographics

---

## 2. Research Methodology

The `entity_research` role follows a structured pipeline:

### 2.1 Entity Discovery

```text
SearX metasearch (entity_name + aliases)
    |
    v
Find canonical identity: legal name, jurisdiction, domain
    |
    v
OpenCorporates / Companies House / SEC EDGAR
    (jurisdiction-appropriate registry lookup)
    |
    v
Cross-reference Wikipedia + Crunchbase for conflicting records
    |
    v
Canonical entity record (legal name, reg number, jurisdiction, status)
```

1. Broad SearX search with entity name and `entity_search_aliases` to find
   website, news mentions, and Wikipedia article.
2. Extract domain and jurisdiction hints from search results.
3. Query the appropriate company registry (OpenCorporates for global, Companies
   House for UK, SEC EDGAR for US public companies).
4. Cross-reference findings across sources. Conflicts are flagged with evidence
   from each source; the most recently filed official registry record wins.

### 2.2 Association Mapping

```text
Board members (Crunchbase, EDGAR DEF 14A, Companies House officers)
    |
    v
Executives (same sources + Glassdoor)
    |
    v
Shareholders (EDGAR Schedule 13D/G, Companies House PSC register)
    |
    v
Subsidiaries / parents (OpenCorporates, EDGAR Exhibit 21, Wikipedia)
    |
    v
Competitors (Crunchbase, SearX news, Wikipedia)
    |
    v
EntityGraph (DOT output, max_depth=3, max_nodes=200)
```

The `EntityGraph` class (`src/general_ludd/business/entity_graph.py`) builds a
directed property graph with 17 association types classified as financial,
personal, contractual, or competitive. Traversal is bounded by
`entity_research_association_max_depth` (default 3) and
`entity_research_association_max_nodes` (default 200).

### 2.3 Asset Discovery

```text
Domains (WHOIS/RDAP, crt.sh, Shodan DNS)
    |
    v
IP blocks (WHOIS/RDAP, RIPEstat)
    |
    v
ASNs (PeeringDB, RIPEstat)
    |
    v
SSL certificates (crt.sh, Censys)
    |
    v
Technology stack (BuiltWith, Shodan/Censys banner analysis)
    |
    v
Trademarks (USPTO, WIPO)
    |
    v
Patents (USPTO, Espacenet)
```

Asset scan depth is controlled by `entity_research_asset_scan_depth`:
- **shallow**: domains + IPs only
- **medium** (default): adds ASNs, certificates, and trademarks
- **deep**: adds patents and full technology stack profiling

### 2.4 Risk Scoring

A weighted multi-factor model with evidence requirements:

| Factor | Weight | Sources | Evidence required |
|---|---|---|---|
| Legal (litigation, sanctions, regulatory) | 0.35 | EDGAR, SearX, news APIs | Docket number or regulatory filing ID |
| Financial (bankruptcy, debt, going concern) | 0.25 | EDGAR, OpenCorporates | Filing date and case number |
| Reputational (breaches, fines, adverse media) | 0.25 | HaveIBeenPwned, SearX | Breach name or article URL + date |
| Operational (leadership churn, layoffs) | 0.10 | Crunchbase, Glassdoor, SearX | Named person + date |
| Geopolitical (jurisdiction risk, sanctions) | 0.05 | OpenCorporates, sanction lists | Sanctions list name + entry ID |

Each finding carries a confidence score (0.0–1.0). Findings below
`entity_research_risk_confidence_threshold` (default 0.6) are filtered out.
The overall risk score is the weighted sum of all findings above threshold,
normalized to 0.0–1.0, categorized as low (≤0.3), medium (≤0.6), high (≤0.8),
or critical (>0.8).

### 2.5 Demographics (Optional)

Consumer profiling uses publicly available data:
- Audience segments from Crunchbase industry classifications and market reports
- Brand sentiment from SearX news aggregation (positive/negative/neutral ratio)
- Competitive landscape from Crunchbase similar-companies and Wikipedia categories

---

## 3. SearX Integration — Continuous Monitoring

When `entity_research_searx_monitor: true`, the role registers the entity for
ongoing news and event monitoring via the self-hosted SearX instance.

### 3.1 Topic Registration

Seven alert topics are tracked (configurable via
`entity_research_searx_alert_topics`):

| Topic | Query pattern | Signal |
|---|---|---|
| `acquisition` | `"{entity_name}" acquisition OR merger OR buyout` | M&A activity |
| `breach` | `"{entity_name}" data breach OR hack OR ransomware` | Security incidents |
| `funding` | `"{entity_name}" funding OR investment OR series` | Capital raises |
| `layoff` | `"{entity_name}" layoff OR restructuring OR downsizing` | Workforce changes |
| `leadership` | `"{entity_name}" CEO OR appointed OR resigned` | Executive changes |
| `lawsuit` | `"{entity_name}" lawsuit OR sued OR litigation` | Legal actions |
| `bankruptcy` | `"{entity_name}" bankruptcy OR insolvency OR chapter` | Financial distress |

Each topic is registered as a periodic saved search. The role writes
`monitor_config.json` to `entity_research_output_dir` with the registered
queries and their scan intervals.

### 3.2 Scan Frequency & Deduplication

- **Interval:** `entity_research_searx_check_interval_hours` (default 24).
  Each topic is re-queried at this interval.
- **Deduplication:** result URLs are hashed and stored. Duplicate URLs across
  scan cycles are suppressed. New content is detected by URL novelty.
- **Date filtering:** SearX queries include time-range filters (`timerange`)
  to limit results to the scan window, reducing noise from stale results.

### 3.3 Alert Thresholds & Escalation

- **Risk score change:** if the entity's computed risk score changes by more
  than `entity_research_searx_alert_on_risk_threshold` (default 0.7) between
  scans, an alert is raised.
- **Topic match:** any result matching an alert topic is surfaced immediately,
  regardless of scan interval.
- **Escalation:** alerts are written as structured JSON to
  `entity_research_output_dir/alerts/` with timestamp, topic, source URL, and
  extracted summary. The caller (agent or playbook) decides how to surface
  these (TUI notification, daemon event, log).

### 3.4 Evidence Preservation

Every SearX result that contributes to a risk finding or association is
preserved:
- **URL + snippet** stored in the finding's `evidence` field
- **Timestamp** of the SearX query stored alongside
- **`monitor_log.json`** in the output directory logs every scan cycle:
  query executed, number of results, new unique URLs, alerts generated
- Audit trail: any finding backed by SearX results can be traced to the
  specific scan cycle and URL

---

## 4. Privacy & Ethics

All research is scoped to publicly available data. The following constraints
are structural (enforced by the role's data source toggles and API client
defaults), not advisory:

### 4.1 Public Data Only

- Only data accessible without authentication beyond a free API key is queried.
- No credentials, logins, or sessions to access non-public or restricted
  datasets are used by default.
- Sources requiring paid plans (Crunchbase entity details, Shodan full results,
  BuiltWith technology profiling) default to `false` and must be explicitly
  enabled with `entity_research_use_<source>: true`.

### 4.2 Respect robots.txt & Rate Limits

- `entity_research_api_delay_seconds` (default 1) enforces a minimum delay
  between all API calls.
- `entity_research_api_max_retries` (default 3) with exponential backoff on
  429/503 responses.
- SearX respects `robots.txt` of underlying search engines (SearX is the
  privacy proxy — it handles compliance).
- Wikipedia: per Wikimedia User-Agent policy, the role sets a descriptive
  User-Agent header identifying itself as an automated research tool.

### 4.3 No Paywall Circumvention

- The role does not attempt to bypass paywalls, login gates, or
  subscription-only content.
- If a source returns a 401/403 or a paywall page, the result is recorded
  as `inaccessible` with the reason, not scraped.
- Crunchbase entity detail fields beyond the autocomplete summary require
  a paid plan; the role records `requires_paid_plan: true` in the canonical
  entity record when such fields are unavailable.

### 4.4 PII Limitations

- Personally identifiable information is only collected when it is already
  publicly listed by an official source:
  - Directors and officers from company registries (legally required filings)
  - Significant controllers from PSC registers (legally required disclosures)
  - Named individuals in SEC filings (executives, major shareholders)
- Contact details (email, phone, personal address) are NOT collected, even
  when publicly available. The role explicitly strips these fields from API
  responses before storage.
- HaveIBeenPwned domain search returns breach names and descriptions, NOT
  the compromised credentials or PII within breaches.

### 4.5 Attribution

All output artifacts include a `_sources` section per finding:
```json
{
  "finding": "...",
  "confidence": 0.85,
  "sources": [
    {
      "name": "opencorporates",
      "url": "https://api.opencorporates.com/v0.4/companies/us_de/1234567",
      "accessed": "2026-07-12T14:30:00Z",
      "license": "Open Database License (ODbL)"
    }
  ]
}
```

- Every finding carries the source name, URL, access timestamp, and data
  license where available.
- Wikipedia content is attributed per CC BY-SA 4.0 with article URL and
  revision timestamp.
- The generated Markdown report includes a "Sources" section linking back
  to each API or platform used.

### 4.6 Opt-In Only

Every research category (discovery, associations, assets, exposure, risks,
demographics) defaults to `false`. No data is collected without an explicit
opt-in via the corresponding `entity_research_<category>: true` variable.
This is enforced at the Ansible role level: each task block is guarded by
its category flag, so missed data is a silent skip, not a crash.
