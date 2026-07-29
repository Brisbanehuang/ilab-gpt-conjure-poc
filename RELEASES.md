# 分享版发布说明

## `share/v0.2.25`

当前分享版通过 Git 分支发布：

`https://github.com/Brisbanehuang/ilab-gpt-conjure-poc/tree/share/v0.2.25`

该版本基于上游 iLab GPT Conjure `v0.5.2`，包含 OmniAPI Image Studio 的
任务级 BYOK、用户隔离、R2 输出与缩略图、本地缓存、提示词模式、2K 默认分辨率、
GPT-5.6 主模型候选以及前端稳定性修复。

当前没有为分享版发布 Windows/macOS portable 资产。上游 portable 包不包含这些
定制改动，也不应作为本分支的更新包。请按 [README](README.md) 从源码或 Docker
启动。

离线分享应从干净 Git tree 生成：

```bash
git archive --format=zip --output=omniapi-image-studio-share-v0.2.25.zip \
  share/v0.2.25
```

不要压缩包含 `data/`、`output/`、`.env`、OAuth 文件、API Key 或 SQLite 数据库的
本地运行目录。
