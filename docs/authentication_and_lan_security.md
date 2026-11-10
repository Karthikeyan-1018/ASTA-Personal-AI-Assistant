# Authentication and Local-Network Security

Phase 8 adds a single-owner authentication boundary and documents how to reach
ASTA safely from a laptop and a phone on a trusted private network.

ASTA still has **no frontend**. This document describes the backend contract a
future frontend must follow.

## Architecture summary

- One owner account, stored in `owner_accounts`.
- Passwords hashed with **Argon2id**; plaintext and hashes are never returned.
- Server-side sessions stored in `auth_sessions`; only SHA-256 hashes of the
  random session token and CSRF token are persisted.
- Opaque session cookie, **not JWT**. This avoids refresh-token complexity and
  makes logout immediately revocable.
- Every business router derives identity from the session cookie only. A
  `user_id` in a body, query string, or header is never trusted.
- Health endpoints remain public because they expose no sensitive data.

## Dependencies

Runtime and test dependencies are pinned in `backend/requirements.txt` and
`backend/requirements-dev.txt`. Argon2id support is provided by
`argon2-cffi`, and the PostgreSQL driver is `asyncpg`.

```powershell
cd C:\Asta\backend
..\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Use `requirements.txt` for a runtime-only install. The files include `tzdata`
for Windows timezone support and the semantic-memory ML stack. Verify a fresh
Python 3.12 install without touching `C:\Asta\venv`:

```powershell
..\venv\Scripts\python.exe scripts\verify_phase81_dependencies.py
```

## Owner account setup

### Option A: local CLI (recommended)

The password never leaves the laptop.

```powershell
cd C:\Asta\backend
..\venv\Scripts\python.exe scripts\create_owner.py
```

The script prompts for the username and password with `getpass`, refuses to run
unless `DB_NAME=asta_db`, and refuses to create a second owner.

### Option B: one-time setup endpoint

1. Generate a long random setup token:

   ```powershell
   ..\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. Set it in `.env` and restart the backend:

   ```dotenv
   AUTH_OWNER_SETUP_TOKEN=<generated-value>
   ```

3. Create the owner:

   ```powershell
   $token = "<generated-value>"
   Invoke-RestMethod -Method Post `
     -Uri https://127.0.0.1:8443/api/auth/setup `
     -Headers @{ "X-Setup-Token" = $token } `
     -ContentType "application/json" `
     -Body '{"username":"owner","password":"<long-passphrase>"}'
   ```

4. Remove `AUTH_OWNER_SETUP_TOKEN` from `.env` and restart. Setup then returns
   `503 Service Unavailable`.

Setup always creates a **new dedicated user**. Existing users, memories,
conversations, preferences, tasks, pending actions, and embeddings are never
reused, modified, or deleted. A second setup attempt returns `409 Conflict`.

Password policy defaults: 12–128 characters. Username: 3–50 characters using
letters, numbers, dots, underscores, or hyphens.

## Login, logout, and session inspection

```http
POST /api/auth/login
Content-Type: application/json

{"username": "owner", "password": "<passphrase>"}
```

On success the response contains the owner, the session expiry, and a CSRF
token. Two cookies are set:

| Cookie | HttpOnly | Purpose |
| --- | --- | --- |
| `asta_session` | Yes | Opaque session credential, `Path=/api` |
| `asta_csrf` | No | Readable CSRF token for `X-CSRF-Token`, `Path=/` |

Both cookies use `Secure` by default and `SameSite=Lax`. Change the names,
paths, `SameSite`, or lifetime with the `AUTH_*` variables in `.env.example`.
The session cookie is restricted to `/api`; the CSRF cookie is readable at `/`
so a frontend SPA can still read it after a page reload.

```http
GET /api/auth/me
```

Returns the owner and the current session expiry, or `401`.

```http
POST /api/auth/logout
X-CSRF-Token: <csrf token>
```

Revokes the session row, clears both cookies, and returns `204`. A replayed
session cookie is rejected afterwards.

## Session lifecycle

1. Login creates a row with `user_id`, `token_hash`, `csrf_token_hash`,
   `created_at`, and `expires_at`.
2. Protected requests validate the cookie against PostgreSQL. Expired and
   revoked rows are rejected.
3. `last_used_at` is updated at most once per
   `AUTH_SESSION_LAST_USED_UPDATE_SECONDS` to avoid a write per request.
4. Logout sets `revoked_at`. The token hash remains for audit purposes but can
   never authenticate again.
5. `AUTH_SESSION_LIFETIME_HOURS` controls lifetime (default 168 hours).

Session tokens are 256-bit random values. Token and CSRF comparisons use
constant-time comparison. Passwords, tokens, and hashes are never logged.

## CSRF protection

`GET`, `HEAD`, `OPTIONS`, and `TRACE` are treated as safe. Every state-changing
request made with a session cookie must include:

```http
X-CSRF-Token: <value of the asta_csrf cookie>
```

Missing or incorrect tokens return `403`. The CSRF token is bound to the session
row, so possessing the session cookie alone is insufficient. Requests without a
session cookie are handled by the authentication dependency (`401`), not by the
CSRF check.

## Protected APIs

All of these now require a valid session:

- `POST /api/chat/`
- `/api/conversations/*`
- `/api/memories/*`
- `/api/preferences/*`
- `/api/tasks/*`
- `/api/task-actions/*` (including confirm and reject)
- `POST /api/auth/logout`
- `GET /api/auth/me`

Public: `/`, `/health`, `/api/health/providers`, `POST /api/auth/login`, and
the one-time `POST /api/auth/setup`.

Ownership checks inside every service are unchanged. Because identity now comes
from one session, the owner can only reach their own conversations, memories,
preferences, tasks, and pending actions.

## Login and setup rate limiting

`POST /api/auth/login` and `POST /api/auth/setup` are protected by a
database-backed rate limiter so restarting the backend does not clear an active
cooldown and every worker shares the same state.

- State table: `auth_login_attempts` (additive, no credentials stored).
- Counter key: normalised source IP plus a normalised account identifier.
- Default threshold: 5 failed attempts for the same `(IP, account)` pair.
- Initial cooldown: 15 minutes.
- Progressive cooldowns: after the first cooldown expires, a new cycle of 5
  failures applies `AUTH_LOGIN_INITIAL_COOLDOWN_MINUTES *
  AUTH_LOGIN_COOLDOWN_MULTIPLIER^level`, capped at
  `AUTH_LOGIN_MAX_COOLDOWN_MINUTES` (default 24 hours). The cooldown level is
  retained across expired cooldowns so repeated abuse escalates.
- Response: blocked attempts return `429 Too Many Requests` with a generic
  detail and a `Retry-After` header. Error text never reveals whether the
  username exists.
- Reset: a successful login clears the relevant `(IP, account)` state. This
  does not clear counters for other IPs or other account keys.
- Lockout: cooldowns always expire. There is no permanent owner lockout.
- Atomicity: counters are incremented with SQL updates inside PostgreSQL; a
  concurrent burst is counted consistently.
- Cleanup: expired attempt rows are purged when failures are recorded and can
  be removed explicitly with `login_rate_limit_service.cleanup_expired()`.
- Trusted proxy: `X-Forwarded-For` is ignored unless the direct peer is listed in
  `AUTH_TRUSTED_PROXIES`. When trusted, the left-most forwarded address is used;
  a malformed value falls back to the peer address. Never trust a client address.

Configuration:

```dotenv
AUTH_LOGIN_RATE_LIMIT_ENABLED=true
AUTH_LOGIN_MAX_FAILED_ATTEMPTS=5
AUTH_LOGIN_INITIAL_COOLDOWN_MINUTES=15
AUTH_LOGIN_COOLDOWN_MULTIPLIER=2
AUTH_LOGIN_MAX_COOLDOWN_MINUTES=1440
AUTH_LOGIN_ATTEMPT_RETENTION_HOURS=48
AUTH_TRUSTED_PROXIES=
```

## Local-network configuration

### Bind only where needed

Run the backend on the laptop's private LAN address and a single HTTPS port:

```powershell
cd C:\Asta\backend
..\venv\Scripts\python.exe -m uvicorn app.main:app `
  --host 0.0.0.0 --port 8443 `
  --ssl-keyfile C:\Asta\certs\asta-key.pem `
  --ssl-certfile C:\Asta\certs\asta-cert.pem
```

Use the laptop's actual LAN IPv4 address (for example `192.168.1.20`) from the
phone. Do **not** configure router port forwarding or expose ASTA to the public
internet.

Keep private services on loopback:

- PostgreSQL: set `listen_addresses = 'localhost'` in `postgresql.conf` and
  connect with `DB_HOST=127.0.0.1`. No `pg_hba.conf` rule for the LAN.
- Ollama: keep `OLLAMA_BASE_URL=http://localhost:11434`; do not run it with a
  LAN-facing `OLLAMA_HOST`.
- Groq credentials stay in `.env` on the laptop and are only used server-side.

### Trusted hosts and CORS

Set the hostnames the phone will use:

```dotenv
TRUSTED_HOSTS=localhost,127.0.0.1,::1,192.168.1.20,asta.local
```

For a same-origin frontend, leave `CORS_ALLOWED_ORIGINS` empty. If a frontend is
served from a different origin, list explicit HTTPS origins:

```dotenv
CORS_ALLOWED_ORIGINS=https://192.168.1.20:8443,https://asta.local:8443
```

CORS is only enabled when at least one origin is configured. Credentials are
allowed, but a wildcard origin is never used.

### Windows Firewall

Allow only the HTTPS backend port, only on the Private profile and local subnet:

```powershell
New-NetFirewallRule -DisplayName "ASTA HTTPS (Private LAN)" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 `
  -Profile Private -RemoteAddress LocalSubnet

New-NetFirewallRule -DisplayName "Block ASTA PostgreSQL from LAN" `
  -Direction Inbound -Action Block -Protocol TCP -LocalPort 5432

New-NetFirewallRule -DisplayName "Block ASTA Ollama from LAN" `
  -Direction Inbound -Action Block -Protocol TCP -LocalPort 11434
```

Mark the Wi-Fi network as **Private** in Windows. If the network is Public,
these rules do not apply and the phone should not connect.

### HTTPS certificate

`AUTH_COOKIE_SECURE=true` means credentials are only sent over HTTPS. Plain HTTP
on Wi-Fi is **not** secure credential transport.

Using `mkcert`:

```powershell
mkcert -install
mkcert -key-file C:\Asta\certs\asta-key.pem `
  -cert-file C:\Asta\certs\asta-cert.pem `
  192.168.1.20 asta.local localhost 127.0.0.1
```

Install the `mkcert` root CA on the phone so the certificate is trusted. For a
self-signed certificate instead, install that certificate as trusted on the
phone as well.

### Development-only HTTP

For loopback development only, `.env` may set:

```dotenv
AUTH_COOKIE_SECURE=false
```

Never use this for phone access over Wi-Fi.

## Future frontend contract

When a frontend is added:

1. Call `POST /api/auth/login` with `credentials: "include"`.
2. Read the non-HttpOnly `asta_csrf` cookie (available at `Path=/`) and send
   it as `X-CSRF-Token` on every `POST`, `PATCH`, `PUT`, or `DELETE`. The login
   response also returns the same token for immediate use after login.
3. Never store the session token in `localStorage` or `sessionStorage`; the
   HttpOnly cookie is the credential.
4. On `401`, clear local UI state and show the login view.
5. Serve the frontend from one of `CORS_ALLOWED_ORIGINS` and use HTTPS.

## Security limitations

- Rate limiting is per `(source IP, normalised account)` pair. A determined
  attacker can change source IPs to gain additional attempts; keep ASTA on a
  private LAN, use HTTPS, and restrict the firewall to the Private profile and
  `LocalSubnet`.
- `X-Forwarded-For` is only trusted for explicitly configured proxy peers.
  Misconfiguring `AUTH_TRUSTED_PROXIES` can let an attacker forge a source IP.
- No two-factor authentication, email verification, or password-reset email.
  If the password is lost, rotate it with a local administrative procedure.
- Session rows and expired login-attempt rows are not automatically purged on a
  schedule; expired rows are harmless but can be cleaned periodically.
- A stolen session cookie remains valid until expiry or logout. Keep the laptop
  and phone locked, and use HTTPS.
- The API does not defend against a compromised owner device or malicious
  browser extensions.
- `TrustedHostMiddleware` validates the Host header, not the client's network
  location. Restrict network access with the firewall and Wi-Fi configuration.
- No public deployment is supported. Do not port-forward, use a public tunnel,
  or bind PostgreSQL/Ollama to the LAN.
- Audit logging is limited to non-sensitive authentication events.
- Dependency pinning reduces surprise upgrades, but the semantic-memory ML stack
  is large and should be revalidated after any version or model change.

## Verification

```powershell
cd C:\Asta\backend

# Dependency consistency and imports
..\venv\Scripts\python.exe -m unittest tests.test_phase81_dependencies -v
..\venv\Scripts\python.exe scripts\verify_phase81_dependencies.py

# Auth, session, CSRF, cookie, protection, spoofing, pending-action tests
..\venv\Scripts\python.exe -m unittest tests.test_phase8_auth -v

# Login rate limiting
..\venv\Scripts\python.exe -m unittest tests.test_phase81_rate_limit -v

# Real PostgreSQL checks (asta_db only, throwaway rows, automatic cleanup)
..\venv\Scripts\python.exe scripts\verify_phase8_auth.py
..\venv\Scripts\python.exe scripts\verify_phase81_rate_limit.py

# Full regression suite
..\venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```
