# Security Policy

## Local-only assumptions

iLab GPT Conjure is designed for local personal workflows. Do not expose the
WebUI directly to the public internet unless you have reviewed and hardened the
deployment yourself.

## Secrets and local data

Do not publish OAuth tokens, API keys, account files, `.env` files, input images,
generated outputs, task metadata, SQLite databases, or debug logs.

Sensitive local paths include:

- `~/.codex/auth.json`
- `output/`
- `outputs/`
- `input/`
- `inputs/`

## Advanced local auth warning

The optional Codex / ChatGPT OAuth mode calls an internal ChatGPT backend
endpoint. It is not an officially recommended OpenAI API integration path and
may change or stop working without notice. Prefer OpenAI-compatible API mode for
stable integrations.

## Optional Sub2API login bridge

The share branch keeps the Omni/Sub2API login bridge disabled by default. When
enabling it, configure the Sub2API base URL, login URL, dashboard URL, and CORS
origins explicitly. Never use `*` as a credentialed CORS origin, and do not
publish the Fernet key used by `OMNI_POC_SECRET_KEY`.

## Portable updater behavior

Portable startup launchers only start the local WebUI server and open the local
browser URL. They do not contact GitHub and do not update files automatically.

Portable update scripts are manually run. They fetch GitHub Release metadata and
the matching portable zip, verify the published SHA256 file, preserve local
`data/`, only replace package-managed files inside the extracted portable
folder, and keep backups under `.backup/`.

## Reporting issues

Please report security issues privately to the maintainer instead of opening a
public issue containing credentials, tokens, private prompts, or private images.
