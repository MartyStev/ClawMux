# Changelog

All notable changes to ClawMux will be documented in this file.

## [Unreleased]

### Security
- **Teams webhook**: inbound activities now require a valid Bot Framework JWT (signature via Microsoft JWKS, audience, issuer, expiry) and the activity `serviceUrl` must match an HTTPS host whitelist — closes the unauthenticated-webhook and Bearer-token SSRF exfiltration issues.
- **Bitrix24 webhook**: `POST /api/v1/bitrix/event` requires the incoming-webhook secret (`BITRIX_INBOUND_SECRET`); endpoint is disabled when the secret is not configured.
- **Mattermost action proxy**: `POST /api/v1/mm/action` requires a shared secret (`MM_ACTION_SHARED_SECRET`) via header or callback-URL query param; endpoint is disabled when not configured.
- **Credentials at rest**: OpenClaw `private_key_b64`, `device_token` and `gateway_token` are now Fernet-encrypted in PostgreSQL (`CREDENTIAL_ENCRYPTION_KEY`), with backward-compatible plaintext reads and a one-shot `scripts/encrypt_existing_credentials.py` migration.
- Added `PyJWT[crypto]` and (test-only) `aiosqlite` dependencies.

### Added
- Initial open source readiness files: `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, and GitHub templates.
- `requirements.lock` for deterministic dependency installs.
- GitHub Actions CI workflow for automated test runs.
- `docs/deployment.md` and `docs/architecture.md` for deployment and architecture guidance.
- `CODEOWNERS` for automated maintainers review.

### Changed
- Updated README with Docker and local quick start workflows.
- Improved OpenClaw and multi-user isolation documentation.

### Fixed
- Replaced localized thinking phrases with English equivalents for broader audience.
