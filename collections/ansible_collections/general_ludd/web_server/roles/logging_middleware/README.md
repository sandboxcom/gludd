# logging_middleware — Access/Error Logging, Rotation, Analysis

Configures log formats, rotation, access/error logs, custom fields, JSON structured
logging, syslog integration, and log analysis tooling.

## nginx log_format Variables

| Variable | Description |
|---|---|
| `$remote_addr` | Client IP address |
| `$time_local` | Local time in Common Log Format |
| `$request` | Full request line: `GET /index.html HTTP/1.1` |
| `$status` | Response status code |
| `$body_bytes_sent` | Bytes sent to client (excluding headers) |
| `$bytes_sent` | Bytes sent including headers |
| `$http_referer` | Referer header value |
| `$http_user_agent` | User-Agent header value |
| `$request_time` | Full request processing time (seconds, millisecond resolution) |
| `$upstream_response_time` | Time to receive response from upstream (seconds) |
| `$upstream_addr` | Upstream server address |
| `$upstream_status` | Upstream response status |
| `$ssl_cipher` | Negotiated cipher suite |
| `$ssl_protocol` | Negotiated TLS version |
| `$request_length` | Request length including headers and body |
| `$request_uri` | Full original request URI with arguments |
| `$server_name` | Server name from the server block |
| `$http_host` | Host header from the request |
| `$remote_user` | Username from Basic auth |
| `$gzip_ratio` | Achieved compression ratio |
| `$connection` | Connection serial number |
| `$connection_requests` | Current number of requests on this connection |
| `$msec` | Current time in seconds with millisecond resolution |
| `$pipe` | "p" if request was pipelined, "." otherwise |
| `$request_completion` | "OK" if completed, empty otherwise |
| `$upstream_cache_status` | HIT/MISS/BYPASS/EXPIRED from proxy cache |

### Common Format Strings

**Combined Log Format** (default):
```
'$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"'
```

**Extended with timing**:
```
'$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent" $request_time $upstream_response_time'
```

**JSON structured logging**:
```
log_format json_combined escape=json
  '{'
    '"time":"$time_iso8601",'
    '"remote_addr":"$remote_addr",'
    '"request":"$request",'
    '"status":$status,'
    '"body_bytes_sent":$body_bytes_sent,'
    '"request_time":$request_time,'
    '"http_referer":"$http_referer",'
    '"http_user_agent":"$http_user_agent",'
    '"upstream_addr":"$upstream_addr",'
    '"upstream_response_time":"$upstream_response_time"'
  '}';
```

## Apache LogFormat

| Placeholder | Description |
|---|---|
| `%h` | Remote host (IP) |
| `%l` | Remote logname (identd, usually `-`) |
| `%u` | Remote user (from auth) |
| `%t` | Time in common log format |
| `%r` | First line of request |
| `%s` | Status code |
| `%b` | Response size in bytes (CLF, `-` for 0 bytes) |
| `%D` | Request time in microseconds |
| `%T` | Request time in seconds |
| `%{Referer}i` | Incoming Referer header |
| `%{User-Agent}i` | Incoming User-Agent header |
| `%{VARNAME}e` | Environment variable |
| `%{VARNAME}n` | Note from another module |
| `%{VARNAME}o` | Outgoing response header |

Standard combined format:
```
LogFormat "%h %l %u %t \"%r\" %>s %b \"%{Referer}i\" \"%{User-Agent}i\"" combined
```

## JSON Logging

- nginx: `escape=json` in `log_format` definition.
- Custom field extraction via njs (nginx JavaScript module) for complex parsing.
- JSON logs are natively ingestible by Elasticsearch, Loki, or BigQuery.
- Example pipeline: `Filebeat → Logstash → Elasticsearch` or `Promtail → Loki`.

## Log Rotation

### logrotate (recommended)
- Config at `/etc/logrotate.d/nginx`:
  ```
  /var/log/nginx/*.log {
      daily
      missingok
      rotate 30
      compress
      delaycompress
      notifempty
      create 0640 www-data adm
      sharedscripts
      postrotate
          [ -f /var/run/nginx.pid ] && kill -USR1 `cat /var/run/nginx.pid`
      endscript
  }
  ```
- `postrotate`: sends `USR1` signal to nginx to reopen log files after rotation.
- `delaycompress`: yesterday's log is not compressed until tomorrow (keeps one uncompressed for analysis).
- `sharedscripts`: runs `postrotate` once for all matched logs, not per-file.

### Built-in rotation (systemd journal)
- nginx logging to syslog/journal bypasses log files entirely.
- Systemd `journald` handles rotation, compression, and retention.
- `journalctl -u nginx --since "1 hour ago"` — query by service.

## Syslog Integration

- nginx: `access_log syslog:server=unix:/dev/log,facility=local7,tag=nginx-access main;`
- nginx: `error_log syslog:server=unix:/dev/log,facility=local7,tag=nginx-error warn;`
- syslog destination can be a UDP server: `syslog:server=192.168.1.1:514`
- Maps to `rsyslog` `imuxsock` or `imudp` / `imtcp` inputs.

## Middleware Logging

### Request ID Injection
- Add `$request_id` via nginx variable: `set $request_id $connection-$connection_requests-$msec;`
- Forward to upstream: `proxy_set_header X-Request-ID $request_id;`
- Response header: `add_header X-Request-ID $request_id always;`

### Timing Headers
- `X-Request-Time`: upstream + nginx processing time.
- `X-Upstream-Response-Time`: proxy time only.
- `add_header X-Request-Time $request_time always;`

### Correlation IDs
- Pass a correlation ID across microservices: client generates → nginx passes
  via `proxy_set_header X-Correlation-ID $http_x_correlation_id;` → upstream
  reads and includes in its own logs.
- If absent, nginx can generate one: `map $http_x_correlation_id $correlation_id
  { default $request_id; "" $request_id; }`

## Error Log Levels

| Level | Meaning | Use case |
|-------|---------|----------|
| `debug` | Debugging messages | Development only; very verbose |
| `info` | Informational messages | Server start/stop, config reload |
| `notice` | Normal but significant | Default for nginx error_log |
| `warn` | Warning conditions | Deprecated directive, config issue |
| `error` | Error conditions | Request processing errors |
| `crit` | Critical conditions | Socket failures, allocation errors |
| `alert` | Action must be taken immediately | Similar to crit |
| `emerg` | System unusable | Fatal errors, kernel messages |

## Log Analysis Tools

### goaccess
- Real-time web log analyzer: `goaccess /var/log/nginx/access.log --log-format=COMBINED`
- Terminal-based interactive dashboard.
- Outputs: HTML, JSON, CSV.

### AWStats
- Perl-based log analyzer, generates HTML reports.
- Reads combined log format, resolves geo-location.

### ELK Stack (Elasticsearch, Logstash, Kibana)
- **Filebeat**: lightweight shipper that tails logs and sends to Logstash or ES.
- **Logstash**: parses, transforms, enriches logs (grok filters for nginx).
- **Elasticsearch**: indexes and stores logs.
- **Kibana**: dashboards and visualizations.

### Grafana Loki
- `Promtail` → Loki → Grafana.
- Label-based indexing: `{job="nginx", host="web01"}`.
- Cheaper than Elasticsearch for log storage.
- LogQL query language: `{job="nginx"} |= "500" | json | status > 399`
