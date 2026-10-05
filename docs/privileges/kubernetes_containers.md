# Least-Privilege ACCESS GUIDE — Container / Orchestrator Facilities

Scope: the read-only access gludd's connectors need against Kubernetes, OpenShift,
containerd/CRI, and the Docker Engine API. Everything here is **read-only
observability** (pod logs + cluster events + container/event/log streams). gludd
never needs write access to run as a monitor; the only place write is discussed is
the clearly-marked *optional* "deploy-compute" role for when gludd schedules model
servers, which you should grant separately and only if you use that feature.

## Implementation status (grounded in the codebase)

Verified against `src/general_ludd/connectors/`:

| Facility | Connector file | Status | What the code actually does |
|---|---|---|---|
| Kubernetes API | `kubernetes.py` (`KubernetesSource`) | **Implemented, wired** | Read-only: `GET /api/v1/events`, `GET /api/v1/namespaces/{ns}/events`, `GET /api/v1/namespaces/{ns}/pods/{pod}/log`, health probes `GET /livez` then `GET /version`. Bearer token from an env var (default `K8S_TOKEN`). No metrics, no create/patch. |
| OpenShift | *(none)* | **Not yet a connector** | No `openshift.py`. OpenShift is API-compatible with the K8s connector for events/logs; the RBAC below is the same plus the SCC/`oc` notes. Routes are not read by any current connector. |
| containerd / CRI | *(none)* | **Not yet a connector** | No `containerd.py`. The K8s connector reaches logs/events via the **API server**, not the node CRI socket. The CRI section below is a forward-looking least-priv spec for a `crictl`-based node connector. |
| Docker Engine API | *(none)* | **Not yet a connector** | No `docker_api.py`. Section below is a forward-looking least-priv spec; **`docker` group membership is root-equivalent** and must not be granted — use a read-only socket proxy. |

> The Kubernetes section is the only one backed by shipping code today. The other
> three are written as least-privilege specs so the access is ready (and safe)
> before the connectors land. They are flagged accordingly.

---

# 1. Kubernetes API (events / pods / logs / [metrics])

## What the connector reads (verbatim from `kubernetes.py`)

- Events: `GET {api_server}/api/v1/namespaces/{ns}/events`, or cluster-wide
  `GET {api_server}/api/v1/events` when `namespace` is blank.
- Pod logs: `GET {api_server}/api/v1/namespaces/{ns}/pods/{pod}/log` (the
  `pods/log` subresource).
- Health: `GET {api_server}/livez`, falling back to `GET {api_server}/version`
  (these are unauthenticated/cluster-info endpoints and need no RBAC rule).
- **metrics.k8s.io is NOT used by the current code.** A `metrics.k8s.io` rule is
  included below only as an *optional, commented-out* block for a future
  metrics-server connector; leave it out unless/until that exists.

Auth is a **ServiceAccount Bearer token** sent as `Authorization: Bearer <token>`.
The token value is read from an environment variable at request time; the connector
does **not** read the in-pod path `/var/run/secrets/kubernetes.io/serviceaccount/token`
itself — you supply the token value via env (see the ENV table at the end).

## 1.a Minimal access — cluster-wide read-only (full YAML)

Use this when gludd watches events/logs across all namespaces (the connector's
cluster-wide `/api/v1/events` mode). Grants ONLY `get`/`list`/`watch` on
`events`, `pods`, and `pods/log`.

```yaml
# k8s-gludd-readonly.yaml  —  kubectl apply -f k8s-gludd-readonly.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: gludd-observer
  namespace: gludd          # the namespace the SA object lives in (not the watch scope)
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: gludd-observer-readonly
rules:
  - apiGroups: [""]
    resources: ["events", "pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]            # pods/log only supports get
  # --- OPTIONAL: only if/when a metrics-server connector is added ---
  # - apiGroups: ["metrics.k8s.io"]
  #   resources: ["pods", "nodes"]
  #   verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: gludd-observer-readonly
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: gludd-observer-readonly
subjects:
  - kind: ServiceAccount
    name: gludd-observer
    namespace: gludd
```

## 1.b Tighter scope — namespace-scoped Role variant

Prefer this when gludd only watches one namespace (set the connector's
`namespace` config to that namespace). Same verbs, but a `Role` + `RoleBinding`
so the SA can read **only** that namespace's events/pods/logs.

```yaml
# k8s-gludd-readonly-namespaced.yaml  —  kubectl apply -f k8s-gludd-readonly-namespaced.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: gludd-observer
  namespace: app-prod
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: gludd-observer-readonly
  namespace: app-prod
rules:
  - apiGroups: [""]
    resources: ["events", "pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: gludd-observer-readonly
  namespace: app-prod
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: gludd-observer-readonly
subjects:
  - kind: ServiceAccount
    name: gludd-observer
    namespace: app-prod
```

## 1.c WHERE / HOW to apply and obtain a token

Apply the manifests:

```bash
kubectl apply -f k8s-gludd-readonly.yaml          # cluster-wide variant
# or
kubectl apply -f k8s-gludd-readonly-namespaced.yaml
```

Mint a token for the SA. Three options, least-surprising first:

```bash
# (1) Short/medium-lived projected token (recommended; k8s >= 1.24).
#     --duration sets TTL; the API server caps it (typically <= 48h unless raised).
kubectl create token gludd-observer -n gludd --duration=24h

# (2) Long-lived token via a bound Secret (only if you truly need a static token).
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: gludd-observer-token
  namespace: gludd
  annotations:
    kubernetes.io/service-account.name: gludd-observer
type: kubernetes.io/service-account-token
EOF
kubectl get secret gludd-observer-token -n gludd -o jsonpath='{.data.token}' | base64 -d

# (3) In-cluster pods: mount the SA and read the projected token at
#     /var/run/secrets/kubernetes.io/serviceaccount/token, then export its
#     contents into the env var gludd reads (default K8S_TOKEN).
```

The connector talks straight to the API server URL you configure as `api_server`;
it does not parse a kubeconfig. If you prefer to drive it from a kubeconfig, build
a minimal one and extract the pieces (server URL → `api_server`, token → the env
var, CA → the `ca` config key):

```yaml
# gludd-kubeconfig.yaml (assembly reference; gludd uses the parts, not the file)
apiVersion: v1
kind: Config
clusters:
  - name: target
    cluster:
      server: https://API_SERVER:6443
      certificate-authority: /etc/gludd/k8s-ca.crt   # -> connector `ca` config key
contexts:
  - name: gludd
    context: { cluster: target, user: gludd-observer }
current-context: gludd
users:
  - name: gludd-observer
    user:
      token: <PASTE TOKEN>                             # -> connector token_env value
```

TLS: set the connector `ca` config key to the cluster CA path (it is forwarded to
the transport as `verify`), or `verify: false` only for throwaway/dev clusters.

## 1.d OPTIONAL deploy-compute role (write — schedule model servers)

Grant this **only** if gludd schedules its own model servers. Keep it as a
**separate, clearly-named role** so the observability SA stays read-only. Bind it
to a *different* SA (`gludd-scheduler`) — do not merge it into `gludd-observer`.

```yaml
# k8s-gludd-scheduler.yaml  —  SEPARATE write role; apply ONLY if gludd schedules compute
apiVersion: v1
kind: ServiceAccount
metadata:
  name: gludd-scheduler
  namespace: gludd-compute
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role            # namespace-scoped on purpose — confine writes to the compute ns
metadata:
  name: gludd-scheduler-deploy
  namespace: gludd-compute
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "watch", "create", "patch"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch", "create", "patch"]
  # NOTE: no "delete", no cluster-wide scope, no secrets. Add explicitly if needed.
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: gludd-scheduler-deploy
  namespace: gludd-compute
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: gludd-scheduler-deploy
subjects:
  - kind: ServiceAccount
    name: gludd-scheduler
    namespace: gludd-compute
```

## 1.e Verification (`kubectl auth can-i`, run as the SA)

`--as=system:serviceaccount:<ns>:<sa>` impersonates the SA so you test the SA's
real grants. Each line states the expected answer.

```bash
SA=system:serviceaccount:gludd:gludd-observer

# Expected: yes
kubectl auth can-i get  events            --as=$SA
kubectl auth can-i list events            --as=$SA -A
kubectl auth can-i watch events           --as=$SA
kubectl auth can-i get  pods              --as=$SA
kubectl auth can-i list pods              --as=$SA -A
kubectl auth can-i get  pods/log          --as=$SA

# Expected: NO  (proves least-privilege — no writes, no secrets)
kubectl auth can-i create pods            --as=$SA
kubectl auth can-i delete pods            --as=$SA
kubectl auth can-i get    secrets         --as=$SA
kubectl auth can-i '*'    '*'             --as=$SA

# Scheduler SA (only if you applied 1.d). Expected: yes / yes / NO
SCHED=system:serviceaccount:gludd-compute:gludd-scheduler
kubectl auth can-i create deployments -n gludd-compute --as=$SCHED
kubectl auth can-i patch  pods        -n gludd-compute --as=$SCHED
kubectl auth can-i delete deployments -n gludd-compute --as=$SCHED
```

---

# 2. OpenShift (events / routes / pod logs)

> Forward-looking: no `openshift.py` connector exists yet. For events and pod logs
> OpenShift is API-compatible with the Kubernetes connector — point `api_server`
> at the OpenShift API and reuse Section 1's RBAC. Routes (`route.openshift.io`)
> are **not** read by any current connector, so the routes rule is included only
> commented-out for a future connector.

## 2.a Minimal access — read-only ClusterRole

```yaml
# ocp-gludd-readonly.yaml  —  oc apply -f ocp-gludd-readonly.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: gludd-observer
  namespace: gludd
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: gludd-observer-readonly
rules:
  - apiGroups: [""]
    resources: ["events", "pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
  # --- OPTIONAL: only if a future connector reads OpenShift Routes ---
  # - apiGroups: ["route.openshift.io"]
  #   resources: ["routes"]
  #   verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: gludd-observer-readonly
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: gludd-observer-readonly
subjects:
  - kind: ServiceAccount
    name: gludd-observer
    namespace: gludd
```

## 2.b `oc adm policy` equivalent (instead of writing the binding YAML)

```bash
oc create sa gludd-observer -n gludd
oc apply -f ocp-gludd-readonly.yaml        # creates the ClusterRole

# Bind the read-only ClusterRole to the SA (equivalent to the binding above):
oc adm policy add-cluster-role-to-user gludd-observer-readonly \
  -z gludd-observer -n gludd

# Namespace-scoped equivalent (tighter): bind only within one namespace
oc adm policy add-role-to-user gludd-observer-readonly \
  -z gludd-observer -n app-prod --role-namespace=app-prod
```

## 2.c Minimal SCC note

A read-only API client needs **no elevated SecurityContextConstraints**. If gludd
runs *as a pod inside* OpenShift, leave it on the default `restricted-v2` SCC
(non-root, no host access). Do **not** grant `anyuid`/`privileged`/`hostaccess`
for an observability client — it reads the API, it does not need host or root.

```bash
# Only if gludd runs as a pod and you must confirm it stays on restricted-v2.
# Expected: restricted-v2 (NOT anyuid/privileged)
oc adm policy who-can use scc restricted-v2 -n gludd
# Do NOT run: oc adm policy add-scc-to-user privileged -z gludd-observer  (unnecessary)
```

## 2.d Token + verification

```bash
# Mint a token (OpenShift >= 4.11 supports kubectl create token semantics):
oc create token gludd-observer -n gludd --duration=24h
# Legacy fallback (long-lived): create a service-account-token Secret as in 1.c(2).

SA=system:serviceaccount:gludd:gludd-observer
oc auth can-i get pods/log --as=$SA     # Expected: yes
oc auth can-i list events  --as=$SA -A  # Expected: yes
oc auth can-i create pods  --as=$SA     # Expected: no
oc auth can-i get secrets  --as=$SA     # Expected: no
```

---

# 3. containerd / CRI (crictl over the runtime socket)

> Forward-looking: no `containerd.py` connector exists. The current K8s connector
> reaches logs/events via the **API server**, not the node socket. This section is
> the least-privilege spec for a future node-local `crictl`-based connector.

`crictl` is the read client for the CRI runtime socket. Inspection verbs (`ps`,
`pods`, `inspect`, `logs`, `inspecti`, `imagefsinfo`, `stats`) are read-only — but
the socket itself is a powerful capability, so least-privilege is about **OS-level
access to the socket**, not about CRI verbs.

## 3.a OS-level access — no root, group/ACL on the socket

The runtime socket is typically one of:
- containerd: `unix:///run/containerd/containerd.sock`
- CRI-O: `unix:///run/crio/crio.sock`

Run gludd as a dedicated unprivileged user and grant **read/write on the socket
via a group or ACL** — never via `sudo`/root, never by adding the user to a broad
admin group.

```bash
# Dedicated unprivileged service user (no login, no sudo)
sudo useradd --system --no-create-home --shell /usr/sbin/nologin gludd

# Option A — dedicated group owning the socket (preferred; survives restarts via
# the runtime's config, see below). Replace path for CRI-O if needed.
sudo groupadd --system crisock
sudo chgrp crisock /run/containerd/containerd.sock
sudo chmod 660    /run/containerd/containerd.sock     # rw for owner+group, nothing for others
sudo usermod -aG crisock gludd

# Make it stick across containerd restarts (it recreates the socket):
#   /etc/containerd/config.toml
#   [grpc]
#     gid = <gid of crisock>          # containerd chowns the socket to this gid
# then: sudo systemctl restart containerd

# Option B — POSIX ACL for just this user (no group plumbing):
sudo setfacl -m u:gludd:rw /run/containerd/containerd.sock
```

Do **not**: add `gludd` to the `root`/`wheel`/`sudo` groups; `chmod 666` the
socket; or run the connector as root. The socket grants full container control at
the OS level, so confine it to one user/group.

## 3.b Point the connector / crictl at the socket (read-only usage)

```yaml
# /etc/crictl.yaml  (or pass --runtime-endpoint each call)
runtime-endpoint: unix:///run/containerd/containerd.sock
image-endpoint:   unix:///run/containerd/containerd.sock
timeout: 10
debug: false
```

## 3.c Verification (read-only crictl as the gludd user)

```bash
# Run AS the gludd user (su/runuser), not root. Expected: succeeds, lists pods.
sudo -u gludd crictl --runtime-endpoint unix:///run/containerd/containerd.sock pods
sudo -u gludd crictl ps                     # Expected: lists containers (read-only)
sudo -u gludd crictl logs <container-id>     # Expected: prints logs (read-only)

# Confirm socket perms are tight (Expected: srw-rw---- root crisock, NOT world-rw):
stat -c '%A %U:%G' /run/containerd/containerd.sock
```

---

# 4. Docker Engine API (containers / logs / events)

> Forward-looking: no `docker_api.py` connector exists. This section is the
> least-privilege spec for a future Docker connector.

## 4.a RISK CALLOUT — `docker` group membership is root-equivalent

**Do NOT add gludd to the `docker` group, and do NOT mount `/var/run/docker.sock`
read-write into the gludd container.** The Docker daemon runs as root and the API
allows `-v /:/host` bind mounts, privileged containers, etc. — so **any process
with access to the raw Docker socket can trivially gain root on the host.** Group
membership in `docker` is therefore equivalent to passwordless root. An
observability client must never have it.

## 4.b Recommended — read-only socket proxy (least privilege)

Put a filtering proxy in front of the socket and expose **only** the read
endpoints gludd needs (containers, events, logs). `tecnativa/docker-socket-proxy`
denies everything by default; each `*=1` whitelists one API surface, and `POST=0`
blocks all write/exec/create calls including container exec.

```yaml
# docker-socket-proxy.compose.yml  —  docker compose -f docker-socket-proxy.compose.yml up -d
services:
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    container_name: gludd-docker-proxy
    environment:
      # Read surfaces gludd needs:
      CONTAINERS: 1        # GET /containers/json + /containers/{id}/json
      EVENTS: 1            # GET /events stream
      LOGS: 1              # GET /containers/{id}/logs
      PING: 1             # GET /_ping (health)
      VERSION: 1          # GET /version (health)
      # Everything else stays DENIED (defaults are 0). Explicit zeros for clarity:
      POST: 0             # blocks ALL writes: create/start/stop/exec/etc.
      EXEC: 0
      IMAGES: 0
      NETWORKS: 0
      VOLUMES: 0
      INFO: 0
      AUTH: 0
      SECRETS: 0
      SWARM: 0
      SERVICES: 0
      NODES: 0
      TASKS: 0
      BUILD: 0
      COMMIT: 0
      PLUGINS: 0
      SYSTEM: 0
      DISTRIBUTION: 0
      CONFIGS: 0
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro   # proxy reads socket read-only
    ports:
      - "127.0.0.1:2375:2375"      # bind to loopback ONLY — never expose publicly
    restart: unless-stopped
    read_only: true
    tmpfs:
      - /run
    security_opt:
      - no-new-privileges:true
```

Then point the future Docker connector at the **proxy**, not the raw socket:

```text
DOCKER_HOST = tcp://127.0.0.1:2375     # the proxy; raw /var/run/docker.sock is never given to gludd
```

The gludd container/process gets **no** access to `/var/run/docker.sock` and is
**not** in the `docker` group. Its only Docker reachability is the loopback proxy,
which can only answer read calls.

## 4.c Verification (proxy enforces read-only)

```bash
# Read calls via the proxy — Expected: 200 OK
curl -s http://127.0.0.1:2375/_ping                    # -> "OK"
curl -s http://127.0.0.1:2375/version        | head    # -> JSON version
curl -s 'http://127.0.0.1:2375/containers/json?all=1'  | head   # -> JSON list
curl -s 'http://127.0.0.1:2375/events?since=0' --max-time 2     # -> event stream

# Write/exec calls via the proxy — Expected: 403 Forbidden (POST=0)
curl -s -o /dev/null -w '%{http_code}\n' -XPOST \
  http://127.0.0.1:2375/containers/create                       # -> 403
curl -s -o /dev/null -w '%{http_code}\n' -XPOST \
  http://127.0.0.1:2375/containers/SOMEID/exec                  # -> 403

# Confirm gludd's user is NOT in the docker group (Expected: gludd NOT listed):
getent group docker
id gludd        # must show NO "docker" group
```

---

# ENV VARS / KEYS / URLs — connector configuration reference

Grounded in `kubernetes.py` for the K8s row; the containerd/Docker rows describe
the standard interface a future connector would use.

| Env var / config key | Meaning | How to obtain | Maps to (RBAC / OS object) |
|---|---|---|---|
| `K8S_TOKEN` (env; name is the `token_env` config, default `"K8S_TOKEN"`) | Bearer token value the K8s connector sends as `Authorization: Bearer …`, read at request time | `kubectl create token gludd-observer -n gludd --duration=24h`, or decode the SA-token Secret (1.c) | ServiceAccount `gludd-observer` + `gludd-observer-readonly` ClusterRole/Role |
| `api_server` (config key) | Base URL of the K8s/OpenShift API server, e.g. `https://API:6443`. SSRF guard rejects private hosts unless `allow_private=True`; loopback/metadata always blocked | API server URL (`kubectl config view --minify -o jsonpath='{..server}'`) | The API server the SA authenticates to |
| `ca` (config key) | Path to cluster CA cert; forwarded to transport as `verify` for TLS | Cluster CA (`kubectl config view --raw -o jsonpath='{..certificate-authority-data}'` → decode to a file) | TLS trust for the API server |
| `verify` (config key) | TLS verify hint (bool or CA path). `false` for dev only | n/a | TLS behavior |
| `namespace` (config key) | Namespace to scope events/logs; blank ⇒ cluster-wide `/api/v1/events` | choose target ns | Selects ClusterRole (cluster-wide) vs Role (namespaced) |
| `allow_private` (config key, default `False`) | Permit an RFC-1918 internal API server past the SSRF guard | set when the API server is on a private cluster network | n/a (egress policy) |
| `timeout_s` (config key, default `10.0`) | Per-request deadline | n/a | n/a |
| `K8S_SCHED_TOKEN` *(suggested, if you use 1.d)* | Token for the **separate** scheduler SA; keep distinct from `K8S_TOKEN` | `kubectl create token gludd-scheduler -n gludd-compute` | ServiceAccount `gludd-scheduler` + `gludd-scheduler-deploy` Role |
| `CONTAINER_RUNTIME_ENDPOINT` / crictl `runtime-endpoint` *(future containerd connector)* | CRI socket URL, e.g. `unix:///run/containerd/containerd.sock` (CRI-O: `unix:///run/crio/crio.sock`) | path of the node runtime socket | OS group/ACL on the socket (§3.a) — not RBAC |
| `DOCKER_HOST` *(future Docker connector)* | Docker API endpoint — **point at the read-only proxy** `tcp://127.0.0.1:2375`, never the raw socket | the socket-proxy address (§4.b) | docker-socket-proxy whitelist (CONTAINERS/EVENTS/LOGS=1, POST=0) |

---

## Least-privilege summary

- **Grant only `get`/`list`/`watch` on `events`, `pods`, `pods/log`.** No write, no
  secrets, no wildcards. Prefer the namespaced Role (1.b) over the ClusterRole when
  one namespace suffices.
- **No `metrics.k8s.io` and no Routes** until a connector actually reads them; the
  rules are left commented out.
- **Keep write (deploy-compute) on a separate SA/Role** (1.d), namespace-confined,
  never merged into the observer SA.
- **containerd/CRI: socket access via a dedicated group/ACL on a non-root service
  user** — never root, never `chmod 666`.
- **Docker: never grant the `docker` group or the raw socket** (root-equivalent);
  expose only read endpoints through a loopback `docker-socket-proxy` with `POST=0`.
- Every section ships a verification block (`kubectl/oc auth can-i`, `crictl`,
  proxy `curl`) that must show **yes** for the read verbs and **no/403** for writes.
