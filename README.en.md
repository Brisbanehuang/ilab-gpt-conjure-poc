# OmniAPI Image Studio Share Build

This is the sanitized `share/v0.2.25` branch of OmniAPI Image Studio. It is based
on [iLab GPT Conjure](https://github.com/kadevin/ilab-gpt-conjure) `v0.5.2` and
keeps the customized Image Studio workflow without production credentials,
runtime databases, user files, or server-specific addresses.

- Branch: `share/v0.2.25`
- Repository: `https://github.com/Brisbanehuang/ilab-gpt-conjure-poc`
- License: `AGPL-3.0-only`
- Recommended integration: OpenAI-compatible API

![Image Studio UI](assets/UI_en.png)

## Highlights

- Multi-provider OpenAI-compatible API settings and task-scoped BYOK.
- User-isolated tasks, galleries, reference assets, templates, and snippets.
- Local-first output and thumbnails with optional R2 fallback.
- R2 original-image thumbnail generation with a local secondary cache.
- Original, faithful, and creative prompt modes.
- 2K default resolution and common aspect-ratio presets.
- Built-in main-model choices for `gpt-5.6-sol`, `gpt-5.6-terra`,
  `gpt-5.6-luna`, and `gpt-5.4-mini`, with `gpt-5.4-mini` as the default.
- History library, task notifications, cancellation result persistence,
  duplicate-submit protection, and safer JSON error handling.
- Optional Sub2API login bridge with deployment-supplied URLs and CORS origins.

The Sub2API bridge is disabled by default. Local API mode does not connect or
redirect to the maintainer's production services.

## Source installation

```bash
git clone --branch share/v0.2.25 --single-branch \
  https://github.com/Brisbanehuang/ilab-gpt-conjure-poc.git
cd ilab-gpt-conjure-poc

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-webui.txt
.venv/bin/python -m uvicorn codex_image.webui.app:app \
  --host 127.0.0.1 --port 8787 --no-access-log
```

Open `http://127.0.0.1:8787/` and configure an OpenAI-compatible provider in
System Settings. Normal local use does not require an `.env` file.

## Docker

```bash
cp .env.example .env
docker compose -f docker-compose.share.yml up -d --build
curl -fsS http://127.0.0.1:8787/api/health
```

The example compose file publishes the service only on host loopback. Runtime
data is stored under `data/` and must not be committed or shared.

## Optional Sub2API bridge

Set `OMNI_POC_MODE=true` only when integrating with your own Sub2API service.
Configure `OMNI_BASE_URL`, `OMNI_POC_SECRET_KEY`, `OMNI_POC_LOGIN_URL`,
`OMNI_POC_DASHBOARD_URL`, and `OMNI_POC_AUTH_ORIGINS` in your copied `.env`.

Generate a Fernet key with:

```bash
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Do not commit that key. Credentialed CORS origins must be explicit HTTP(S)
origins and must never use a wildcard.

## Development and tests

```bash
npm install
npm run check:webui
.venv/bin/python -m unittest discover -s tests -v
```

`tests/test_share_distribution.py` prevents production hosts and unsafe sharing
instructions from returning to this branch.

## Security and distribution

Do not zip and share a used local working directory: ignored `output/`, `data/`,
OAuth files, API keys, images, and SQLite databases may still exist locally.
Share the Git branch or create an archive from the committed tree with
`git archive`.

This branch currently does not publish customized portable packages. Upstream
portable packages do not contain these modifications and must not be used as an
updater for this branch.

This project retains the repository's `AGPL-3.0-only` license and upstream
attribution.
