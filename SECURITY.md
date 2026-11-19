# Security Policy

> Persian: [SECURITY.fa.md](SECURITY.fa.md)

## Supported versions

| Version | Supported |
|---|---|
| Latest release | Yes |
| Older releases | No |

Only the latest release receives security fixes. Until
the first portable zip is published on the GitHub Releases page, the supported
state is the current source on `main` (run via `run.bat`); the table applies
from the first published release on.

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Use GitHub's private vulnerability reporting: in the repository's **Security**
section, choose **"Report a vulnerability"** and describe the problem there.
Reports are reviewed privately first, and publishing any details is coordinated
with you once a fix is ready.

## Sensitive areas

If you are testing or describing a problem, these parts of the app are the
most security-relevant:

- **Backup import** (`.mcbak` files, *Settings → General → "Export data (backup)"
  / "Import data from backup"*) —
  imported archives are unpacked and merged into the app's database and
  `config.json`.
- **Per-category openers** (Settings → Categories → "Default apps for
  categories…") — stored executable paths are launched together with the
  opened document.

These areas received specific hardening in recent releases — backup imports
pass a ZIP-bomb check and can no longer plant a custom player executable, and
opener programs from foreign backups are never imported; archive extraction,
`desktop.ini` icon handling and the confirm-before-run gate for executable
files were hardened as well (details in [CHANGELOG.md](CHANGELOG.md)).
Reports about them are still especially welcome.
