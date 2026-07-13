# cgi_wsgi — CGI/FastCGI/SCGI/PSGI/WSGI/ASGI Gateway Configuration

Configures gateway interfaces, sets up application server sockets, manages process
pools, and generates WSGI/ASGI configs.

## CGI (Common Gateway Interface)

- Per-request process fork — each HTTP request spawns a new OS process.
- Environment variables passed to the script via stdin/stdout: `REQUEST_METHOD`,
  `QUERY_STRING`, `CONTENT_TYPE`, `CONTENT_LENGTH`, `PATH_INFO`, `PATH_TRANSLATED`,
  `SCRIPT_NAME`, `SERVER_NAME`, `SERVER_PORT`, `SERVER_PROTOCOL`, `REMOTE_ADDR`.
- nginx: `fastcgi.conf` or `fastcgi_params` include file with `fastcgi_param`.
- Very high latency (fork cost). Only suitable for extremely low-traffic or legacy.

## FastCGI

- Persistent processes that survive across requests — no fork per request.
- Socket communication: Unix domain socket (`unix:/var/run/php-fpm.sock`) or TCP
  (`127.0.0.1:9000`).
- Process manager options:
  - **php-fpm**: PHP FastCGI Process Manager — pool-based, dynamic/static/ondemand
    process management. `pm = dynamic`, `pm.max_children = 50`,
    `pm.start_servers = 5`, `pm.min_spare_servers = 5`, `pm.max_spare_servers = 35`.
  - **spawn-fcgi**: generic FastCGI process spawner, used with Perl/Python/C.
- nginx: `fastcgi_pass unix:/var/run/php-fpm.sock;`
- `fastcgi_read_timeout 60s;` — prevent hung FastCGI processes from blocking.
- `fastcgi_buffers 8 16k;`, `fastcgi_buffer_size 32k;`

## WSGI (Python Web Server Gateway Interface, PEP 3333)

- Application callable: `def application(environ, start_response):`
- `environ`: dict of CGI-like variables + `wsgi.*` keys.
- `start_response`: callable `(status, headers, exc_info=None)` → returns write().
- The application MUST call `start_response` before yielding the response body.

### Gunicorn (Green Unicorn)
- Worker types: `sync` (default, one worker = one request at a time),
  `gevent` (greenlet-based async, good for I/O-bound), `eventlet`,
  `uvicorn.workers.UvicornWorker` (for ASGI apps), `gthread` (threads per worker).
- Bind: `gunicorn -b unix:/tmp/app.sock -w 4 app:application`
- Worker count formula: `2 * CPU + 1`. For Docker, use CPU limit, not host CPU count.
- Worker lifecycle: `--max-requests 1000` (restart after N requests to prevent memory leaks),
  `--max-requests-jitter 100` (randomize to avoid thundering herd),
  `--timeout 30` (kill workers idle for N seconds),
  `--graceful-timeout 30` (how long to wait for graceful shutdown).
- Graceful restart: `kill -HUP <master-pid>` → starts new workers, drains old ones.
- Preload: `--preload` loads application before forking workers (saves memory with copy-on-write).

### uWSGI
- `uwsgi --socket /tmp/app.sock --wsgi-file app.py --callable application --processes 4 --threads 2`
- Supports multiple languages (Python, Ruby, Perl, PHP).
- Emperor mode: manage multiple uWSGI instances.
- `--harakiri 30` — kill workers after N seconds.

### mod_wsgi (Apache embedded)
- Embed Python in Apache worker: `WSGIDaemonProcess app python-path=/var/www/app processes=5 threads=2`
- `WSGIScriptAlias / /var/www/app/app.wsgi`
- Daemon mode (recommended) vs. embedded mode.

## ASGI (Asynchronous Server Gateway Interface, Python async)

- Async application callable: `async def application(scope, receive, send):`
- `scope`: dict with connection metadata (type: `http`, `websocket`, `lifespan`).
- `receive`: awaitable that returns events (`http.request`, `websocket.receive`).
- `send`: awaitable to send events (`http.response.start`, `http.response.body`).
- Lifespan protocol: `startup` and `shutdown` events for initialization/cleanup.

### ASGI Servers
- **Uvicorn**: `uvicorn app:application --uds /tmp/app.sock --workers 4`
- **Hypercorn**: supports HTTP/1, HTTP/2, HTTP/3, WebSocket.
- **Daphne**: Django Channels' reference ASGI server.

### nginx + ASGI
- `proxy_pass http://unix:/tmp/app.sock;` (without upstream block).
- `proxy_http_version 1.1;` required for keepalive and WebSocket.
- `proxy_set_header Upgrade $http_upgrade;` and `proxy_set_header Connection "upgrade";` for WebSocket.

## PSGI (Perl Web Server Gateway Interface)

- Application: `sub { my $env = shift; return [200, ['Content-Type' => 'text/html'], ['Hello']]; }`
- Servers: **Starman** (preforking), **Starlet** (forking), **plackup** (development).
- `plackup -s Starman --listen :5000 --workers 10 app.psgi`

## SCGI (Simple Common Gateway Interface)

- Netstring framing: `<length>:<data>,` — simpler than FastCGI.
- nginx: `scgi_pass unix:/tmp/scgi.sock;`
- Largely superseded by WSGI/ASGI.

## nginx Gateway Directives

| Directive | Gateway | Example |
|-----------|---------|---------|
| `fastcgi_pass` | FastCGI | `fastcgi_pass unix:/var/run/php-fpm.sock;` |
| `uwsgi_pass` | uWSGI | `uwsgi_pass unix:/tmp/uwsgi.sock;` |
| `scgi_pass` | SCGI | `scgi_pass 127.0.0.1:9000;` |
| `proxy_pass` | WSGI/ASGI | `proxy_pass http://unix:/tmp/app.sock;` |

## Process Management

- Worker count: `2 * CPU + 1` — one to handle slow clients, rest for real work.
- Request queuing: nginx buffers requests when no worker is available.
- Backlog: `--backlog 2048` — pending connection queue size.
- Graceful shutdown: SIGTERM → stop accepting new requests, finish current ones,
  then exit. SIGKILL as fallback after `graceful_timeout`.
- Avoid thundering herd with `--preload` + `SO_REUSEPORT` (or `accept_mutex` off in newer gunicorn).
