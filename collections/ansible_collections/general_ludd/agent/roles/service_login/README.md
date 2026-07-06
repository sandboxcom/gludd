# service_login

Automates XDG browser-based OAuth2 / API-key login flows for gludd services.

## What it does

1. Opens the user's default browser to the service's authorization URL
2. Starts a local HTTP redirect server to receive the OAuth2 callback
3. Exchanges the authorization code for tokens (OAuth2 services)
4. Stores credentials in environment variables or OpenBao
5. Writes result artifact to `service_login_<service>.json`

## Supported services

| Service    | Auth method | Credential env var      |
|------------|------------|--------------------------|
| github     | OAuth2+PKCE| GITHUB_TOKEN              |
| openai     | API key    | OPENAI_API_KEY            |
| deepseek   | API key    | DEEPSEEK_API_KEY          |
| zai        | API key    | ZAI_API_KEY               |
| anthropic  | API key    | ANTHROPIC_API_KEY         |
| gemini     | OAuth2+PKCE| GOOGLE_API_KEY            |
| openrouter | API key    | OPENROUTER_API_KEY        |

## Pre-requisites

For OAuth2 services (github, gemini), the operator must first:
1. Create an OAuth application on the service's developer portal
2. Set the redirect URI to `http://localhost:<random-port>/callback`
3. Export `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, etc.

See `gludd` README.md → Service Login for detailed setup instructions.

## Key variables

| Variable | Default | Description |
|---|---|---|
| `login_service` | - | Service to log into (required) |
| `login_store` | `env` | Credential storage: `env` or `openbao` |
| `login_timeout` | `120` | OAuth2 callback timeout in seconds |
| `login_non_interactive` | `false` | Skip browser, provide key via `login_api_key` |
| `login_api_key` | `""` | Pre-obtained API key (non-interactive mode) |
| `repo_path` | playbook_dir | Working directory for CLI invocation |
| `artifact_dir` | `/tmp` | Where to write result artifact |

## Example playbook

```yaml
- name: Log into GitHub
  hosts: localhost
  gather_facts: false
  roles:
    - role: general_ludd.agent.service_login
      vars:
        login_service: github
        login_store: env
```

## Non-interactive mode (CI / headless)

```yaml
- name: Set API key from secrets
  hosts: localhost
  gather_facts: false
  roles:
    - role: general_ludd.agent.service_login
      vars:
        login_service: openai
        login_non_interactive: true
        login_api_key: "{{ lookup('env', 'OPENAI_API_KEY') }}"
```

## Safety

- The local HTTP server binds to `127.0.0.1` only — it is never reachable off-host
- Tokens are never logged (API keys and OAuth tokens are redacted)
- OpenBao-backed storage enforces secret path allow-lists
