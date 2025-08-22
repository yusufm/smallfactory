# Developers

Useful entry points:

- PLM SPEC (Core v1): ../../smallfactory/core/v1/SPECIFICATION.md
- Git workflow and environment: git-workflow.md

Code layout (high level):
- Core API (versioned): ../../smallfactory/core/v1/
- CLI wrapper: ../../smallfactory/cli/sf_cli.py
- Web app: ../../web/

## Running behind Cloudflare Access or another auth proxy

smallFactory's web app can run behind an authentication proxy (e.g., Cloudflare Access, OAuth2/OIDC gateways, Nginx with SSO). When an upstream proxy authenticates the user and injects identity headers, the app will:

- Use the incoming user/email to set Git author/committer for web mutations, so commits reflect the actual operator.
- Only apply identity if both name and email are available; if only an email is present, the app derives a readable name from the email local part.

Headers and configuration:
- Recognized defaults (case-insensitive):
  - User: `X-Forwarded-User`, `X-Auth-Request-User`
  - Email: `X-Forwarded-Email`, `X-Auth-Request-Email`
- Override or add header names via environment (comma-separated supported):

```sh
# Example: Cloudflare Access or a proxy that provides only an email header
export SF_WEB_IDENTITY_HEADER_EMAIL="Cf-Access-Authenticated-User-Email"
# Optional if your proxy also provides a distinct user header; otherwise name is derived from email
export SF_WEB_IDENTITY_HEADER_NAME="X-Forwarded-User"
```

How it works (see `web/app.py`):
- Header resolution: `_get_proxy_identity_header_names()` and `_extract_identity_from_headers()`
- Per-request Git identity: `_with_git_identity()`
- Applied around mutations: `_run_repo_txn()`

Security notes:
- Only trust identity headers added by your auth proxy. Do not expose the app directly to the internet while trusting headers.
- Ensure your reverse proxy strips/overwrites inbound `X-Forwarded-*`/`X-Auth-Request-*` headers from clients.
