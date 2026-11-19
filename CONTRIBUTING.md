# Contributing to FolderDex

Contributions are welcome — code, documentation, translations, tests, bug
reports and ideas. FolderDex is maintained by one person in their spare time,
so this guide exists to make contributing predictable for everyone: what the
project stands for, how to send a change, and what happens next.

**فارسی:** [CONTRIBUTING.fa.md](CONTRIBUTING.fa.md)

## Ways to contribute

- **Report a bug** — use the **bug report** template in [Issues](https://github.com/mohammadjafari-dev/FolderDex/issues/new/choose)
- **Suggest a feature** — use the **feature request** template; describe the problem, not only the solution
- **Fix a bug or improve the code** — send a pull request (see below)
- **Improve the documentation or a translation** — English and Persian are both first-class
- **Add or improve tests** — especially a failing test that reproduces a bug
- **Help others** — answer questions and confirm reproductions in Issues

## Before you start

- **Small and obvious changes** (a typo, a clear bug fix, a doc correction) — open a pull request directly.
- **Anything bigger** (a new feature, a behavior change, a new dependency, a refactor) — open an issue first and wait for a reply, so nobody spends days on a change that cannot be merged.
- **Check the roadmap** in the [README](README.md#roadmap) — performance work and a Linux port are the current direction, so changes that fit it are the easiest to accept.
- **Search existing issues and pull requests** before opening a new one.

## Project principles

Every change is reviewed against these shared rules. They are what keeps
FolderDex trustworthy, so they are not negotiable in a pull request:

- **Offline and private** — no network calls, no telemetry, no update checks; the app never sends anything anywhere at run time
- **Never touch the user's files** — the app only reads the user's media folders; everything it writes lives next to the app (database, covers, caches, backups; in a read-only app folder the caches fall back to `%TEMP%\FolderDex-cache`)
- **Portable** — nothing is written to the registry or `%APPDATA%` (the registry is only ever *read*, to locate the installed media players)
- **Bilingual** — every user-visible string exists in English and Persian, and layouts must work in both RTL and LTR
- **Windows 10/11 first** — Python 3.11–3.13; code that also runs on Linux is welcome but must not break Windows
- **Old data keeps working** — legacy names such as `.mcbak` and old backups must stay readable
- **Careful with sensitive areas** — backup import and per-category openers need extra scrutiny (see [SECURITY.md](SECURITY.md))
- **New dependencies need discussion** — they must be compatible with GPL-3.0-or-later and be listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

## Setting up

Windows 10/11 and Python 3.11–3.13 are needed. From the repository root:

```bat
run.bat
.venv\Scripts\python -m pip install pytest ruff
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m pytest tests/ -q
```

`run.bat` creates the virtual environment and starts the app; the other
commands run the same lint and test steps as CI. More details are in
[docs/BUILDING.md](docs/BUILDING.md).

## Sending a pull request

1. Fork the repository and create a branch from `main` with a descriptive name.
2. Keep the pull request focused — one topic per pull request, as small as practical.
3. Follow the style of the surrounding code; new `.py` and `.bat` files need the same SPDX header as the existing ones.
4. Add or update tests for bug fixes and new behavior.
5. Update the documentation for anything users can see — English and Persian files must keep the same structure (this is checked by `tests/test_repo_integrity.py`). If you cannot write Persian, write the English text and say so in the pull request; help with the translation is welcome.
6. Do not change the app version or the changelog — the maintainer does this at release time.
7. For visible UI changes, attach a screenshot, with personal paths removed.
8. Fill in the pull request template and make sure CI is green.

AI-assisted contributions are fine — this project uses AI itself. You are
responsible for what you submit: read it, run it, and be able to explain it.
Large unreviewed generated changes will be closed.

## Review and merging

- Review is best-effort and there is no guaranteed response time; a polite nudge after two weeks is fine.
- A pull request is merged when it fits the principles above, is focused and readable, and passes CI.
- You may be asked for changes. A pull request can also be declined for scope or direction, with a reason — it is never personal.
- A pull request with no reply to review comments for 30 days may be closed; you can reopen it any time.
- Merged changes are squashed into one commit that keeps you as the author.

## Licensing of contributions

- FolderDex is licensed under [GPL-3.0-or-later](LICENSE). Your contribution is accepted under the same license, and you keep your copyright.
- Every commit must carry a `Signed-off-by` line (`git commit -s`), which certifies the [Developer Certificate of Origin](https://developercertificate.org/): you wrote the change or have the right to submit it under this license.
- Do not copy code from other projects unless its license is GPL-compatible, and say where it came from in the pull request.
- Bundled files such as fonts, icons or binaries need a license that allows redistribution and an entry in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Code of conduct

- Be respectful and assume good faith; disagree with ideas, not with people.
- Write in English or Persian in issues, pull requests and documentation; code comments and docstrings stay English so every contributor can read them.
- No harassment, personal attacks, discrimination or spam.
- The maintainer may edit, hide or remove comments and block people who repeatedly break these rules.
- To report a conduct problem privately, contact the maintainer through the GitHub profile [mohammadjafari-dev](https://github.com/mohammadjafari-dev).

## Security problems

Please do not report vulnerabilities in public issues or pull requests. Follow
[SECURITY.md](SECURITY.md) and use GitHub's private vulnerability reporting.
