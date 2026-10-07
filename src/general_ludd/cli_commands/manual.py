"""Static manual text for the command-line interface."""

from __future__ import annotations

MAN_PAGE = """\
NAME
    gludd — General Ludd Agent — autonomous coding system

SYNOPSIS
    gludd <command> [<subcommand>] [options...]

DESCRIPTION
    General Ludd Agent is an autonomous coding system with Ansible runners
    and multi-model AI agents. It coordinates AI models and local automation
    to complete software work.

COMMANDS
    daemon              Start the daemon (server + event loop)
      --host HOST         Bind address (default: 127.0.0.1)
      --port PORT         Port (default: 8000)
      --log-level LEVEL   debug|info|warning|error (default: info)
      --tick-interval N   Event loop tick interval in seconds (default: 1.0)
      --workers N         Gunicorn workers (default: 1)
      --config-dir PATH   Configuration directory
      --templates-dir PATH  Prompt templates directory
      --playbooks-dir PATH  Ansible playbooks directory

    add                 Add a todo to the queue
      TITLE               Task title (required)
      --description TEXT  Detailed description
      --queue NAME        Target queue (default: core)
      --priority INT      Priority (default: 100)
      --work-type TYPE    code|test|review|refactor|docs|etc
      --project ID        Project identifier
      --daemon-url URL    Daemon URL (default: http://localhost:8000)

    status              Show todo or system status
      [TODO_ID]           Optional todo ID for details
      --project ID        Filter by project
      --daemon-url URL    Daemon URL

    list                List todos
      --queue NAME        Filter by queue
      --status STATUS     Filter by status
      --project ID        Filter by project
      --daemon-url URL    Daemon URL

    log-level           Change daemon log level at runtime
      LEVEL               debug|info|warning|error
      --daemon-url URL    Daemon URL

    deployments         List active deployments
      --daemon-url URL    Daemon URL

    version             Show version

    health              Check daemon health
      --daemon-url URL    Daemon URL

    smoke              Run low-cost provider/service smoke tests
      list               List all registered smoke tests
      PROVIDER TEST      Run a smoke test, e.g. aws ec2-a100
      --live             Allow cheap live metadata probes
      --json             Emit logs, metrics, and events as JSON

    model               Local model management (download, quantize, serve, evaluate)
      download NAME        Download a model from HuggingFace
        --revision REV       Model revision/tag
        --cache-dir DIR      Override cache directory
      quantize NAME         Quantize a downloaded model
        --method METHOD      q4_k_m (default), q4_0, q5_k_m, q8_0
        --output-dir DIR     Output directory for quantized model
      serve NAME            Start a local inference server
        --engine ENGINE      llamacpp (default), vllm, mlx
        --host HOST          Bind address (default: 127.0.0.1)
        --port PORT          Port (default: 8080)
        --gpu-layers N       GPU layers to offload
        --context-size N     Context window size (default: 4096)
      evaluate NAME          Run evaluation benchmarks
        --benchmark NAME     Specific benchmark
        --limit N            Sample limit per benchmark
      recommend              Recommend models for a task
        --task TASK           Task description (required)
        --max-params N        Max parameter count in billions
      radar NAME             Show capability radar for a model

    models              Model management commands
      search              Search HuggingFace models
        [QUERY]             Search query
        --limit N           Max results (default: 20)
        --daemon-url URL    Daemon URL
      searx-search        Search models via SearXNG
        [QUERY]             Search query
        --source SRC        Source: huggingface, github, web (default: huggingface)
        --searx-url URL     SearXNG instance URL
      deploy              Deploy a model found via SearXNG
        NAME                Model name to find and deploy
        --provider P        Cloud provider (default: aws)
        --engine E          Engine: vllm or llamacpp (default: vllm)
        --workload-type W   Workload type (default: realtime_api)
        --searx-url URL     SearXNG instance URL
        --region REGION     Cloud region
        --gpu-count N       Number of GPUs (default: 1)
        --max-cost N        Max cost in USD (default: 10.0)
      downloaded          List downloaded models
        --daemon-url URL    Daemon URL
      discover            Discover free models from providers
        --provider NAME      Provider (default: openrouter)
        --daemon-url URL     Daemon URL
      discovered          List auto-discovered model profiles
        --daemon-url URL     Daemon URL
      performance         Show model performance data
        --service S          Filter by service
        --task-type T        Filter by task type
        --daemon-url URL     Daemon URL
      ranking             Show model rankings for a task type
        --task-type T        Task type (required)
        --strategy S         Ranking strategy (balanced|quality|cheapest|fastest)
        --daemon-url URL     Daemon URL
      router-status       Show current router configuration
        --daemon-url URL     Daemon URL
      router-set          Set routing strategy for a task type
        --task-type T        Task type (required)
        --strategy S         Routing strategy (balanced|quality|cheapest|fastest)
        --daemon-url URL     Daemon URL

    local-serve         Start a local inference server
      --engine ENGINE     vllm|llamacpp (default: vllm)
      --model MODEL       Model name or path (required)
      --host HOST         Host (default: localhost)
      --port PORT         Port (default: 8001)
      --gpu-layers N      GPU layers (default: -1)
      --context-size N    Context size (default: 4096)
      --daemon-url URL    Daemon URL

    worktree            Worktree monitor commands
      scan                Scan for abandoned worktrees with AGENTS.md
        --path PATHS        Comma-separated paths to scan
        --daemon-url URL    Daemon URL
      status              Show tracked worktrees
        --daemon-url URL    Daemon URL

    mcp                 MCP server catalog commands
      search              Search MCP catalog
        [QUERY]             Search query
        --daemon-url URL    Daemon URL
      list                List known MCP servers
        --daemon-url URL    Daemon URL
      info                Show MCP server details
        NAME                Server name
        --daemon-url URL    Daemon URL

    skills              Skills catalog commands
      search              Search skills catalog
        [QUERY]             Search query
        --daemon-url URL    Daemon URL
      list                List all skills
        --daemon-url URL    Daemon URL
      install             Install a skill
        NAME                Skill name
        --daemon-url URL    Daemon URL

    compute             Compute endpoint commands
      endpoints           List compute endpoints
        --daemon-url URL    Daemon URL
      register            Register a compute endpoint
        --id ID             Endpoint ID
        --url URL           Endpoint URL
        --model MODEL       Model name
        --daemon-url URL    Daemon URL
      unregister          Remove a compute endpoint
        ENDPOINT_ID         Endpoint to remove
        --daemon-url URL    Daemon URL
      launch              Launch a GPU compute instance
        --provider NAME     Cloud provider (aws, azure, gcp, runpod, etc.)
        --gpu TYPE          GPU type (t4, a100_80, h100, etc.)
        --model MODEL       Model name to serve
        --daemon-url URL    Daemon URL
      destroy             Destroy a GPU compute instance
        INSTANCE_ID         Instance ID to destroy
        --daemon-url URL    Daemon URL

    scores              View benchmark scores
      --task-type TYPE    Filter by task type
      --daemon-url URL    Daemon URL

    leaderboard         View prompt+model leaderboard
      --task-type TYPE    Filter by task type
      --daemon-url URL    Daemon URL

    login               Browser-based OAuth2 / API key login for services
      <service>            Service to log into (github, openai, deepseek, zai, anthropic, gemini, openrouter)
      --list               List available services
      --timeout N          OAuth2 callback timeout in seconds (default: 120)
      --store {env,openbao}  Credential storage backend (default: env)

    help                Show this manual

    test self           Run daemon self-tests (canonical command)
      --daemon-url URL    Daemon URL (default: http://localhost:8000)
    selftest            Backward-compatible alias for ``test self``
      --daemon-url URL    Daemon URL (default: http://localhost:8000)

    Pause state is managed via tasks/agents/infra API endpoints:
      POST /api/tasks/{task_id}/pause
      POST /api/tasks/{task_id}/resume
      POST /api/agents/{agent_id}/pause
      POST /api/infra/{deployment_id}/pause
      GET  /api/pause/status

    test-bg             Background test runner commands
      launch              Launch a test in the background
        TESTFILE            Test file path (required)
        --wait              Block until test completes
      status              Check status of a background test
        TESTFILE            Test file path (required)
      poll-all            Status for all tracked background tests
      kill                Kill a background test
        TESTFILE            Test file path (required)
        --force             Force SIGKILL after SIGTERM
      results             Get final results for a completed test
        TESTFILE            Test file path (required)

    searx               SearXNG meta-search engine commands
      start               Start the local SearXNG server
      stop                Stop the local SearXNG server
      status              Check if SearXNG is running
      config              Show/generate SearXNG configuration

    filestore           Filestore management commands
      list [PATH]         List filestore contents (default: /)
        --daemon-url URL    Daemon URL
      cat PATH             Read a file from filestore
        --daemon-url URL    Daemon URL
      bootstrap            Download binaries into filestore
        --binary NAME        Binary to download (default: openbao)
        --daemon-url URL     Daemon URL
      binaries             List stored binaries
        --daemon-url URL     Daemon URL

    payment             PCI-DSS payment card vault (envelope-encrypted in OpenBao)
      add                 Store a card under a label
        --card-number NUM    Card number (prompted via getpass if omitted)
        --expiry-month MM    Expiry month 01-12 (required)
        --expiry-year YY     Expiry year 2-digit (required)
        --cvc CVC            CVC (prompted via getpass if omitted)
        --holder-name NAME   Cardholder name (required)
        --label NAME         Storage label (default: default)
        --processor NAME     Payment processor (default: stripe)
      list                List stored cards (masked only)
      show LABEL          Show masked metadata for one card
      delete LABEL        Delete a stored card (-y to skip confirm)
      provision SERVICE   Simulate 1-click provisioning using a stored card
        --label NAME         Card label to use (default: default)

EXAMPLES
    gludd daemon
    gludd add "Fix login bug" --work-type bug_fix
    gludd status
    gludd list --queue core
    gludd models discover --provider openrouter
    gludd worktree scan --path ~/projects
    gludd mcp search github
    gludd scores --task-type code
    gludd help

ENVIRONMENT
    OPENROUTER_API_KEY   OpenRouter API key for model discovery
    OPENAI_API_KEY       OpenAI API key
    ANTHROPIC_API_KEY    Anthropic API key
    ZAI_API_KEY          Z.AI API key

FILES
    ~/.config/general-ludd/general-ludd.yml   User configuration
    ~/.cache/general-ludd/                    Cache directory

SEE ALSO
    gludd daemon --help    Daemon-specific options
    docs/quickstart.md     Getting started guide
    docs/configuration.md  Full configuration reference
"""
