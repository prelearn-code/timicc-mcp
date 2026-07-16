# Security policy

Please report vulnerabilities privately through GitHub Security Advisories instead of opening a public issue.

This server sends the caller-provided `file_context` to a TIMI CC endpoint. It does not discover repository files itself, and `allowed_paths` only validates returned patch paths. Never submit secrets, credentials, personal data, customer data, or unrelated proprietary code.

API keys must be supplied through environment variables. Do not commit `.env` files, key files, state databases, or captured model responses.
