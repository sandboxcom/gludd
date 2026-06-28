# openbao_break_glass_backup

OpenBao break-glass encrypted backup. Snapshots the OpenBao raft store to an
encrypted tarball, GPG-encrypted with a local (auto-created) user key.

## What it does

1. **GPG key check / creation** — if the invoking user has no GPG secret key, a
   4096-bit RSA key (no passphrase, 2-year expiry) is generated non-interactively
   via `gpg --batch --generate-key` with `%no-protection`. This is idempotent —
   the role probes `gpg --list-secret-keys` first and only generates when absent.
2. **OpenBao raft snapshot** — calls `POST /v1/sys/storage/raft/snapshot` on the
   OpenBao server (via the `gludd_break_glass` module by default; falls back to
   `ansible.builtin.uri` if `use_break_glass_module: false`). The raw bytes are
   written to a temp file.
3. **GPG encryption** — `gpg --batch --yes --trust-model always --recipient
   <key> --output <backup> --encrypt <tmpfile>` produces the on-disk `.gpg`
   file. The unencrypted temp file is then shredded.
4. **Verify** — `gpg --list-packets <backup>` confirms the encrypted file is
   well-formed (does NOT decrypt — that requires the private key).
5. **Retention** — at the end of the run, backups older than
   `backup_retention_days` (default 30) are pruned via the `cleanup old backups`
   handler.

## Why GPG (not just file permissions)

Encrypted backups can be moved off-host (rsync to a NAS, upload to S3, copy to
a USB key) without trusting the transport or the destination. File permissions
protect the file at rest on the gludd host; GPG protects it everywhere else.

## Defaults

| Variable | Default | Notes |
|---|---|---|
| `backup_dir` | `/var/backups/gludd/openbao` | operator overrides |
| `backup_filename` | `openbao-{{ ansible_date_time.iso8601 }}.gpg` | ISO timestamp per run |
| `backup_path` | `{{ backup_dir }}/{{ backup_filename }}` | final on-disk path |
| `backup_retention_days` | `30` | pruned by handler |
| `gpg_user_id` | `{{ ansible_user_id }}` | GPG identity probe target |
| `gpg_user_email` | `gludd-backup@localhost` | recipient address |
| `gpg_recipient` | `{{ gpg_user_email }}` | passed to `--recipient` |
| `openbao_addr` | `https://127.0.0.1:8200` | OpenBao URL (https or loopback only) |
| `openbao_token_source` | `env` | `env` = `VAULT_TOKEN`; `secret` = gludd SecretsManager |
| `snapshot_temp_path` | `/tmp/gludd-openbao-snapshot-...bin` | shredded after encrypt |

## GPG key creation flow

* **Auto-create** — when the role runs and no secret key exists for the user,
  it generates one. The key has no passphrase (`%no-protection`) so the role
  runs unattended. The key material lives in `~/.gnupg/` on the gludd host.
* **Pre-create (operator)** — operators who already have a key should ensure
  `gpg --list-secret-keys <user>` returns it; the role will skip generation.
  Override `gpg_user_email` / `gpg_recipient` to point at the existing key.

## Exporting the private key (off-site backup)

**This step is mandatory.** Without the private key, every encrypted backup is
permanently unrecoverable. If the gludd host dies and you have not exported the
private key, the backups are gone.

```bash
# On the gludd host (or wherever the key was generated):
KEY_ID=$(gpg --list-secret-keys --with-colons \
    {{ gpg_user_email | default('gludd-backup@localhost') }} \
    | awk -F: '/^sec/ {print $5; exit}')
gpg --armor --export-secret-keys "$KEY_ID" > private-key.asc
```

Store `private-key.asc` somewhere safe and OFF the gludd host:
* Paper backup (print the armored text, store in a safe).
* Password manager (1Password, Bitwarden, KeePass).
* Hardware token (YubiKey, Nitrokey) via `gpg --edit-key` `keytocard`.

## Deleting the private key from the gludd host (air-gap)

For air-gap scenarios where the gludd install host should NOT retain decryption
capability (e.g. a hardened bastion that only creates new backups):

```bash
KEY_ID=$(gpg --list-secret-keys --with-colons \
    {{ gpg_user_email | default('gludd-backup@localhost') }} \
    | awk -F: '/^sec/ {print $5; exit}')

# AFTER you have verified the private key is safely exported:
gpg --delete-secret-and-public-keys "$KEY_ID"

# Keep the public key around so this host can still CREATE (encrypt) backups:
gpg --import public-key.asc
```

After deletion, this host can still CREATE new encrypted backups using the
public key (which can be re-imported via `gpg --import public-key.asc`), but
**cannot decrypt them**. Decryption requires the private key, which now lives
only in your off-site backup.

## Restore procedure

On a trusted host that holds the private key:

```bash
# 1. Import the private key.
gpg --import private-key.asc

# 2. Decrypt the backup tarball, pipe into the new OpenBao raft restore API.
#    The destination OpenBao must be running and the token must have root.
DECRYPTED=$(gpg --decrypt openbao-*.gpg | base64 -)
curl --data-binary @- \
    -H "X-Vault-Token: $VAULT_TOKEN" \
    http://new-openbao:8200/v1/sys/storage/raft/restore \
    <<<"$DECRYPTED"

# Or equivalently, using the gludd_break_glass module:
# - general_ludd.agent.gludd_break_glass:
#     mode: restore
#     openbao_addr: http://new-openbao:8200
#     token: "{{ vault_token }}"
#     restore_source: /path/to/openbao-*.gpg
```

## Scheduling

The gludd daemon ships a default scheduled task that runs this role against
localhost daily at 03:00 local time. The schedule entry is created via
`POST /api/todos/scheduled` (PSK-gated) when OpenBao is initialized, and is
persisted so it survives daemon restarts. See
`docs/design/OPENBAO_BREAK_GLASS_BACKUP.md` for the full timer contract.

## Idempotency

* GPG key generation probes `gpg --list-secret-keys` first and skips when a key
  exists.
* GPG encrypt uses `args.creates: {{ backup_path }}` so a re-run does not
  overwrite an existing backup file.
* The retention handler is notified only when a backup is actually created.
