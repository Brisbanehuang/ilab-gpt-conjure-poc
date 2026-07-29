from __future__ import annotations

from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".mjs", ".py", ".ps1", ".sh", ".ts", ".txt", ".yml", ".yaml"}


class ShareDistributionTests(unittest.TestCase):
    def test_share_tree_has_no_production_host_references(self) -> None:
        forbidden = (
            "api." + "brislouise.online",
            "img." + "brislouise.online",
            "159.69." + "185.229",
            "141.95." + "41.137",
        )
        roots = [ROOT / "codex_image", ROOT / "packaging", ROOT / "scripts", ROOT / "tests"]
        files = [path for root in roots for path in root.rglob("*") if path.is_file() and path.suffix in TEXT_SUFFIXES]
        files.extend([ROOT / "README.md", ROOT / "README.en.md", ROOT / "RELEASES.md", ROOT / ".env.example"])
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for value in forbidden:
                self.assertNotIn(value, text, f"production reference leaked into {path.relative_to(ROOT)}")

    def test_share_readme_clones_the_share_branch(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("--branch share/v0.2.25", readme)
        self.assertIn("Brisbanehuang/ilab-gpt-conjure-poc.git", readme)
        self.assertNotIn("git clone https://github.com/kadevin/ilab-gpt-conjure.git", readme)

    def test_runtime_and_secret_paths_remain_ignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in (".env", "output/", "outputs/", "input/", "inputs/", "data/", ".venv/", "node_modules/"):
            self.assertIn(entry, gitignore)
        manifest = (ROOT / ".public-export-manifest.json").read_text(encoding="utf-8")
        self.assertIn('"data/"', manifest)

    def test_public_export_allowlist_has_no_stale_paths(self) -> None:
        manifest = json.loads((ROOT / ".public-export-manifest.json").read_text(encoding="utf-8"))
        missing = [entry for entry in manifest["allowlist"] if not (ROOT / entry).exists()]
        self.assertEqual(missing, [])

    def test_share_compose_binds_only_to_host_loopback(self) -> None:
        compose = (ROOT / "docker-compose.share.yml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:8787:8787"', compose)
        self.assertIn("OMNI_POC_MODE: ${OMNI_POC_MODE:-false}", compose)

    def test_disabled_bridge_example_does_not_activate_placeholder_links(self) -> None:
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("OMNI_POC_MODE=false", env_example)
        self.assertIn("\nOMNI_POC_LOGIN_URL=\n", env_example)
        self.assertIn("\nOMNI_POC_DASHBOARD_URL=\n", env_example)
        self.assertIn("\nOMNI_POC_AUTH_ORIGINS=\n", env_example)

    def test_static_shell_does_not_force_the_optional_login_bridge(self) -> None:
        html = (ROOT / "codex_image/webui/static/index.html").read_text(encoding="utf-8")
        source = (ROOT / "codex_image/webui/frontend/src/omni-poc-key.ts").read_text(encoding="utf-8")
        self.assertNotIn('<html lang="zh-CN" class="omni-poc-mode">', html)
        self.assertIn("return enabled;", source)
        self.assertIn('classList.toggle("omni-poc-mode", Boolean(config.enabled))', source)
        self.assertIn("bridge.methods.renderAuthSource?.(bridge.state.authStatus)", source)
        self.assertIn("bridge.methods.updateRequestPreview?.()", source)

    def test_share_tree_excludes_personal_contact_assets(self) -> None:
        self.assertFalse((ROOT / "assets/wechat-qr.jpg").exists())


if __name__ == "__main__":
    unittest.main()
