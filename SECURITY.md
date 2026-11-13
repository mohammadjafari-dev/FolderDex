# Security Policy

> Persian: [SECURITY.fa.md](SECURITY.fa.md)

## Supported versions

| Version | Supported |
|---|---|
| Latest 0.n.m release | Yes |
| Older releases | No |

Only the latest release in the 0.n.m series receives security fixes.

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Use GitHub's private vulnerability reporting: in the repository's **Security**
section, choose **"Report a vulnerability"** and describe the problem there.
Reports are reviewed privately first, and publishing any details is coordinated
with you once a fix is ready.

## Sensitive areas

If you are testing or describing a problem, these parts of the app are the
most security-relevant:

- **Backup import** (`.mcbak` files, "Export / import data" in Settings) —
  imported archives are unpacked and merged into the app's database and
  `config.json`.
- **Per-category openers** (Settings → default app of a category) — stored
  executable paths are launched together with the opened document.

Both areas are already hardened (see the Security section of
[CHANGELOG.md](CHANGELOG.md)); reports about them are still especially welcome.
