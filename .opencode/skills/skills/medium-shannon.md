---
{
  "name": "medium-shannon",
  "description": "Autonomous AI pentesting with 96.15% exploit success rate. Analyzes source code, maps attack surfaces, executes real exploits across 50+ vulnerability types in 5 OWASP categories. No false positives. Source: unicodeveloper/medium-2026.",
  "tags": [
    "security",
    "pentesting",
    "owasp",
    "xss",
    "injection",
    "authentication",
    "medium-2026"
  ],
  "category": "security"
}
---

# Shannon: Autonomous AI Pentester

White-box security testing that executes real exploits and reports only
confirmed vulnerabilities. 96.15% exploit success rate on XBOW benchmark
(100/104 exploits). No false positives — if Shannon can't prove it, it
doesn't report it.

## Prerequisites

- Docker (all attacks run in containers)
- Anthropic API key

## Usage

```bash
/shannon http://localhost:3000 myapp                          # Full pentest
/shannon -scope=xss,injection http://localhost:8080 frontend  # Targeted
/shannon -workspace=audit-q1 http://staging.example.com api   # Named workspace
/shannon status                                                # Check status
/shannon results                                               # View report
```

## 5-Phase Pipeline

1. **Pre-Recon** — Static source code analysis + external scans (Nmap, Subfinder, WhatWeb)
2. **Recon** — Live attack surface mapping via headless browser
3. **Vulnerability Analysis** — 5 parallel agents: Injection, XSS, SSRF, AuthN, AuthZ
4. **Exploitation** — Each agent spawns dedicated exploitation, executes real attacks
5. **Reporting** — Executive summary + reproducible PoC for every finding

## Vulnerability Coverage (50+ types)

- **Injection**: SQL (union, blind, time-based), command, SSTI, NoSQL
- **XSS**: Reflected, stored, DOM-based, file upload, mutation XSS
- **SSRF**: Internal service access, cloud metadata, DNS rebinding, protocol smuggling
- **Broken AuthN**: Default credentials, JWT flaws, session fixation, CSRF, MFA bypass
- **Broken AuthZ**: IDOR, privilege escalation, path traversal, forced browsing, mass assignment

## Safety

- Confirms authorization before every run
- Warns against production targets
- Supports scope controls and avoid-list rules
- All attack tools run inside Docker — nothing on host
- Only use against systems you own or have explicit authorization

## Runtime

~1-1.5 hours per full pentest, ~$50 using Claude Sonnet.

## Install reference

Original: `npx skills add unicodeveloper/shannon`
