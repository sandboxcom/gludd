# http_server — Web Server Setup and Configuration

Installs and configures nginx or Apache, manages virtual hosts, document roots,
modules, proxy pass, rate limiting, buffering, and compression.

## nginx Knowledge

### Server Blocks
- `server { }` blocks define virtual hosts matched by `listen` + `server_name`.
- Default server: first `server` block or explicit `default_server` flag.
- Nested `location { }` blocks match URI paths via prefix, exact (`=`), or regex (`~`, `~*`).

### Location Blocks
- Prefix match (highest priority): `location = /exact {}`
- Preferential prefix: `location ^~ /static/ {}`
- Regex (case-sensitive): `location ~ \.php$ {}`
- Regex (case-insensitive): `location ~* \.jpg$ {}`
- Fallback prefix: `location / {}`

### Upstream Pools
- `upstream backend { server 10.0.0.1:8080 weight=3 max_fails=2 fail_timeout=30s; }`
- Load-balancing algorithms: round-robin (default), least_conn, ip_hash, hash, random.
- Health checks: `max_fails` + `fail_timeout`; `slow_start` for gradual reintroduction.
- Keepalive connections: `keepalive 32;` within upstream block.

### try_files
- `try_files $uri $uri/ /index.php?$args;` — tests files in order, falls through to last arg.
- Last arg can be a named location (`=404`, `@backend`) or a new URI.

### Rewrite Rules
- `rewrite ^/old/(.*)$ /new/$1 permanent;` — 301 redirect.
- `rewrite ^/api/(.*)$ /$1 break;` — internal rewrite, stops processing.
- `rewrite_log on;` for debugging rewrites.

### Rate Limiting
- `limit_req_zone $binary_remote_addr zone=mylimit:10m rate=10r/s;` (in http context).
- `limit_req zone=mylimit burst=20 nodelay;` (in location/server context).
- `limit_conn_zone $binary_remote_addr zone=connlimit:10m;` — connection limiting.
- `limit_conn connlimit 10;` — max concurrent connections per IP.
- `limit_rate 100k;` — bandwidth throttling per connection.

### Buffering
- `proxy_buffering on|off;` — enable/disable response buffering from proxied server.
- `proxy_buffer_size 4k;` — header buffer size.
- `proxy_buffers 8 4k;` — number and size of buffers per connection.
- `client_body_buffer_size 16k;` — buffer for client request body.
- `client_max_body_size 1m;` — max request body size (important: default is 1m).

### Timeouts
- `proxy_read_timeout 60s;` — timeout for reading proxied response.
- `proxy_connect_timeout 60s;` — timeout for establishing proxy connection.
- `proxy_send_timeout 60s;` — timeout for sending request to proxied server.
- `keepalive_timeout 65;` — how long idle keepalive connections stay open.
- `client_header_timeout 60s;` — timeout for reading client request header.
- `client_body_timeout 60s;` — timeout for reading client request body.

### Gzip Compression
- `gzip on;`
- `gzip_types text/plain text/css application/json application/javascript text/xml;`
- `gzip_min_length 1000;` — don't compress tiny responses.
- `gzip_comp_level 6;` — 1 (fastest) to 9 (best). Default 1.
- `gzip_vary on;` — add `Vary: Accept-Encoding` header.
- `gzip_proxied any;` — compress proxied responses.

### HTTP/2 and HTTP/3
- `listen 443 ssl http2;` — enable HTTP/2 over TLS.
- `listen 443 quic reuseport;` — enable HTTP/3 (QUIC). Requires `ssl_protocols TLSv1.3;`.
- `add_header Alt-Svc 'h3=":443"; ma=86400';` — advertise HTTP/3 availability.
- `http2_max_field_size 16k;` — max HPACK-compressed header field size.
- `http2_max_header_size 32k;` — max entire request header list size.

## Apache Knowledge

### VirtualHost
- IP-based: `<VirtualHost 192.168.1.1:80>`
- Name-based: `<VirtualHost *:80>` + `ServerName example.com`
- `ServerAlias www.example.com` — additional hostnames.

### Directory / Location Directives
- `<Directory /var/www/html>` — applies to filesystem paths.
- `<Location /api>` — applies to URL paths (processed after Directory).
- `<Files "\.php$">` — applies to specific filenames.
- Order of merging: Directory → .htaccess → Files → Location.
- `Options -Indexes +FollowSymLinks` — common security settings.

### mod_rewrite
- `RewriteEngine On`
- `RewriteRule ^old\.html$ /new.html [R=301,L]` — redirect.
- `RewriteRule ^api/(.*)$ /api.php/$1 [QSA,L]` — internal rewrite with query string append.
- `RewriteCond %{HTTP_HOST} !^www\. [NC]` — condition: host does not start with www.
- `RewriteCond %{HTTPS} off` — condition: not using HTTPS.
- Flags: `[R]` = redirect, `[L]` = last rule, `[QSA]` = append query string, `[NC]` = case-insensitive, `[PT]` = pass-through.

### MPM Selection
- **prefork**: one thread per connection, no threading. Compatible with mod_php.
- **worker**: multi-process, multi-thread. Better concurrency than prefork.
- **event**: like worker but uses dedicated listener threads. Best for keepalive. Default since Apache 2.4.
- `StartServers 3`, `MinSpareThreads 75`, `MaxSpareThreads 250`, `ThreadsPerChild 25`, `MaxRequestWorkers 400`.

### mod_proxy
- `ProxyPass "/api" "http://backend:8080/api"` — forward requests.
- `ProxyPassReverse "/api" "http://backend:8080/api"` — rewrite Location headers.
- `ProxyPassMatch "^/(.*\.php)$" "fcgi://127.0.0.1:9000/var/www/$1"` — regex-based proxy to PHP-FPM.
- Connection pooling: `ProxySet keepalive=On`

### mod_headers
- `Header set X-Frame-Options "DENY"`
- `Header always set Strict-Transport-Security "max-age=31536000"`
- `Header unset X-Powered-By` — remove headers.
- `RequestHeader set X-Forwarded-Proto "https"`

### mod_deflate
- `AddOutputFilterByType DEFLATE text/html text/plain text/css application/json`
- `DeflateCompressionLevel 6` — 1 to 9.
- `BrowserMatch ^Mozilla/4 gzip-only-text/html` — browser-specific compression.
