# Security Policy

## Reporting a Vulnerability

If you discover a security issue in ClawMux, please report it so the maintainers can investigate and fix it quickly.

Preferred reporting process:

1. Open a GitHub issue titled `SECURITY: <short description>`.
2. Include a brief summary of the issue, the affected version, and steps to reproduce it.
3. Avoid posting sensitive data (passwords, tokens, keys) directly in the issue body.

If you need a private disclosure channel, open the issue and request private communication; the maintainers will respond with a secure way to share sensitive details.

## What to include

- A short description of the vulnerability.
- The version or commit SHA of ClawMux you are using.
- Exact steps to reproduce the issue, if available.
- Any relevant logs or error output.

## Response

Maintainers will aim to acknowledge the report promptly and track remediation openly when possible.

## Security best practices

- Do not share credentials, API tokens, or private keys in public issues.
- If you are unsure whether a report is sensitive, ask for a private communication channel after opening the issue.

## Hardening (production checklist)

Inbound channels and stored secrets are protected as follows — configure all of these before exposing ClawMux publicly:

| Surface | Control | Env var |
|---|---|---|
| `POST /api/v1/trigger`, `/notify`, `/mappings/reload` | Constant-time `X-Api-Token` check; empty token = endpoint disabled | `API_TOKEN` |
| `POST /api/v1/teams/messages` | Bot Framework JWT verification (signature via Microsoft JWKS, `aud`, `iss`, expiry) + HTTPS host whitelist for `serviceUrl` so the adapter's Bearer token is never sent to an untrusted host (anti-SSRF) | `TEAMS_APP_ID`, `TEAMS_JWT_AUDIENCE`, `TEAMS_JWKS_URLS`, `TEAMS_ALLOWED_ISSUERS`, `TEAMS_ALLOWED_SERVICE_URL_HOSTS` |
| `POST /api/v1/bitrix/event` | Shared webhook secret (`auth.access_token` or `?secure=`), constant-time compare; empty secret = endpoint disabled | `BITRIX_INBOUND_SECRET` |
| `POST /api/v1/mm/action` | Shared secret via `X-MM-Action-Secret` header or `?secret=` in the callback URL registered in Mattermost; empty secret = endpoint disabled | `MM_ACTION_SHARED_SECRET` |
| OpenClaw device credentials in PostgreSQL (`private_key_b64`, `device_token`, `gateway_token`) | Encrypted at rest with Fernet; transparent read/write via SQLAlchemy | `CREDENTIAL_ENCRYPTION_KEY` |

To enable credential encryption on an existing database:

1. Generate a key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Set `CREDENTIAL_ENCRYPTION_KEY` in the environment.
3. Run `python scripts/encrypt_existing_credentials.py --dry-run`, then without `--dry-run`.

Plaintext rows written before the rollout remain readable, so the migration can happen without downtime. Never remove or rotate the key while encrypted rows exist (use the script to re-encrypt after rotation).
