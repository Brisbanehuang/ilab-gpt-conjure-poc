# OmniAPI Image Studio 分享版

这是 OmniAPI Image Studio 的脱敏分享分支，版本为 `0.2.25`，基于
[iLab GPT Conjure](https://github.com/kadevin/ilab-gpt-conjure) `v0.5.2`
继续开发。

- 分享分支：`share/v0.2.25`
- 分享源码：`https://github.com/Brisbanehuang/ilab-gpt-conjure-poc`
- 许可证：`AGPL-3.0-only`
- 推荐接入：OpenAI-compatible API

![Image Studio 界面](assets/UI_cn.png)

## 分享版说明

此分支保留线上版本的主要功能，但不包含生产凭据、用户数据、输入图、生成图、
SQLite 数据库或部署服务器信息。

与上游 `v0.5.2` 相比，主要增加了：

- OmniAPI 品牌工作台和更紧凑的生成界面。
- 多供应商 OpenAI-compatible API 设置与任务级 BYOK。
- 用户隔离的任务、图库、参考图、模板和提示词片段。
- 本地优先、R2 兜底的输出与缩略图策略。
- R2 原图回源生成缩略图及本地二级缓存。
- 原始、保真、创意三种提示词模式。
- 2K 默认分辨率和常用画幅尺寸。
- `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-5.4-mini`
  主模型候选，默认 `gpt-5.4-mini`。
- 历史库、任务通知、取消结果保留、短时间重复提交防护和更稳健的 JSON 错误处理。
- 可选的 Sub2API 登录桥。分享版默认关闭，启用时必须显式填写自己的主站地址。

## 安全边界

- `OMNI_POC_MODE` 默认关闭，不会连接本站或跳转到本站登录页。
- 默认使用本地存储，不会自动连接 R2。
- `.env`、`.venv/`、`node_modules/`、`input/`、`output/` 和数据库均被 Git 忽略。
- 不要把本地工作目录直接压缩分享；其中可能存在被 Git 忽略的 API Key、任务数据库和图片。
- 对外分享应使用本 Git 分支，或使用 `git archive` 从已提交版本生成源码包。
- WebUI 默认只适合本机或受保护的反向代理环境，不要未经鉴权直接暴露到公网。

## 源码安装

```bash
git clone --branch share/v0.2.25 --single-branch \
  https://github.com/Brisbanehuang/ilab-gpt-conjure-poc.git
cd ilab-gpt-conjure-poc

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-webui.txt
```

启动：

```bash
.venv/bin/python -m uvicorn codex_image.webui.app:app \
  --host 127.0.0.1 --port 8787 --no-access-log
```

打开 `http://127.0.0.1:8787/`，在系统设置中添加自己的 OpenAI-compatible
供应商、API Key、图片模型和调用方式。普通使用不需要 `.env` 文件。

macOS 也可以运行：

```bash
open "Start WebUI.command"
```

Windows 可以双击 `Start WebUI.bat`。

## Docker 启动

分享版提供只绑定宿主机回环地址的 Compose 示例：

```bash
cp .env.example .env
docker compose -f docker-compose.share.yml up -d --build
```

健康检查：

```bash
curl -fsS http://127.0.0.1:8787/api/health
```

停止服务：

```bash
docker compose -f docker-compose.share.yml down
```

运行数据保存在本地 `data/`，该目录不得提交或分享。

## 可选 Sub2API 登录桥

仅当你拥有自己的 Sub2API 主站，并希望用户从主站登录后选择自己的 API Key 时，
才需要启用此模式。复制 `.env.example` 后配置：

| 变量 | 说明 |
| --- | --- |
| `OMNI_POC_MODE` | 设置为 `true` 才启用登录桥 |
| `OMNI_BASE_URL` | 自己的 Sub2API OpenAI `/v1` 根地址 |
| `OMNI_IMAGE_MODEL` | 图片模型，默认 `gpt-image-2` |
| `OMNI_POC_SECRET_KEY` | 用于加密任务 Key 和登录令牌的 Fernet Key |
| `OMNI_POC_LOGIN_URL` | 自己主站的登录/Studio 跳转页 |
| `OMNI_POC_DASHBOARD_URL` | 自己主站的 Dashboard，可选 |
| `OMNI_POC_AUTH_ORIGINS` | 允许交换登录令牌的来源，多个值用逗号分隔 |
| `OMNI_POC_SOURCE_URL` | 页面展示的源码地址 |

生成 Fernet Key：

```bash
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

不要把生成的 Key 写入 Git。带凭据的 CORS 来源必须使用明确的 `http(s)://host`，
不能使用通配符。

## 前端开发

仅运行源码不需要 Node.js。修改 TypeScript 或 CSS 时：

```bash
npm install
npm run check:webui
```

前端构建会更新 `codex_image/webui/static/` 中的浏览器资源。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
npm run check:webui
```

分享安全回归位于 `tests/test_share_distribution.py`，用于阻止生产域名、服务器地址
和错误克隆说明重新进入分享分支。

## Portable 包

此分享分支暂未发布定制 portable 包。上游 `kadevin/ilab-gpt-conjure` 的 portable
包不包含本分支改动，不要用上游更新器覆盖分享版。需要分享离线源码时，使用：

```bash
git archive --format=zip --output=omniapi-image-studio-share-v0.2.25.zip \
  share/v0.2.25
```

## 上游与许可证

感谢原项目 iLab GPT Conjure 及其贡献者。本分支继续采用仓库中的
`AGPL-3.0-only` 许可证；分享和部署时请保留 `LICENSE`、上游归属和对应源码。
