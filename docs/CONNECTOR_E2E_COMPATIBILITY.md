# Connector E2E Compatibility Notes

This note records compatibility contracts that repeatedly matter when running
the connector E2E suite. It is an operational guide for deterministic tests;
it does not replace a provider's API documentation.

## Transport Contract

Connector tests should inject a small fake transport instead of making network
calls. The fake must implement the verbs used by the connector under test:

| Connector family | Required fake behavior | E2E assertion |
| --- | --- | --- |
| Webhook and incident writers (Slack, PagerDuty, Bugsnag, Rollbar, Graylog) | `post(...)` accepts the same keyword arguments as the production client and returns a response with status and body accessors. | A successful write emits one normalized event and preserves the provider response status. |
| Pull APIs (ServiceNow, Zendesk, Entra Sign-In, Linear, Notion, Trello, Airtable, Asana, Monday) | `get(...)` accepts keyword-only query/header parameters; pagination responses are deterministic. | Empty pages terminate; a non-empty page maps to the connector's declared `KIND`. |
| File and host readers (Syslog, Journald, Mac unified log) | The source reader is supplied as a fixture or temporary path; no host daemon is required. | Missing sources are handled as an empty stream or explicit, typed error. |
| Profiling readers (Pyroscope, Parca) | Constructor receives the configured endpoint and a fake `get`; response payloads include the minimum profile metadata. | Profiles map to `traces` and retain service/timestamp labels. |

Do not use a get-only fake for a connector that can write. Conversely, a fake
that only accepts positional arguments will hide regressions because production
callers pass query, header, timeout, and pagination values by keyword. Keep
transport fakes local to the test module and assert the captured call shape.

## Naming and Configuration

The exported class name and `KIND` are part of the connector contract. Tests
should import the actual symbol (`EntraSignInSource`, not a guessed spelling),
instantiate it with the documented configuration object, and assert the
declared kind (`tickets`, `tasks`, `pages`, `records`, `events`, `logs`, or
`traces`). Configuration aliases belong in the connector, not in individual
tests. A test that needs an undocumented alias should first add a compatibility
fixture and a task entry explaining why the alias is required.

For CI and headless runs, keep credentials in environment variables or the
provider harness. Never put tokens in a fixture, snapshot, or failure message.
Use the repository harness targets with `LIVE=0` for contract tests and reserve
`LIVE=1` for an explicitly scoped, read-only operator run.

## Long-Lived Issue Signals

Community reports consistently point to the same failure modes represented in
our E2E suite: webhook flows need an acknowledgement/verification step before
an asynchronous API call, and environment-specific credentials should be
resolved at runtime rather than copied into connection records.

* A Slack community thread describes the Events API challenge handshake,
  signature verification, three-second acknowledgement, and asynchronous
  forwarding requirements. Treat this as a reason to test acknowledgement and
  retry behavior separately: [Slack Events API workflow discussion](https://www.reddit.com/r/Slack/comments/1tmh4a3/triggering_gh_actions_from_slack/).
* A ServiceNow community discussion describes the long-lived pain of keeping
  environment-specific client IDs, secrets, and endpoints in connection
  records. Prefer runtime properties/secret injection and test that each
  environment resolves its own values: [ServiceNow environment-variable
  discussion](https://www.reddit.com/r/servicenow/comments/1jwviv3/what_is_the_best_way_to_handle_environment_variables/).

These are user reports, not normative specifications. The normative provider
references remain the [Slack webhook guidance](https://docs.slack.dev/tools/slack-github-action/additional-configurations/),
the [ServiceNow CI authentication contract](https://servicenow.github.io/sdk/4.9.0/config/ci-integration),
and the [Grafana Pyroscope client documentation](https://grafana.com/docs/pyroscope/configure-client/).

## E2E Checklist

1. Use a deterministic fake transport with both verb coverage and keyword
   argument capture.
2. Assert class import, constructor configuration, normalized event shape, and
   `KIND` for every connector.
3. Exercise empty, paginated, malformed, unauthorized, and retry responses.
4. Run the focused connector batch before the full shard runner.
5. Record any new provider incompatibility as a task and update this note with
   the failing contract and mitigation.
