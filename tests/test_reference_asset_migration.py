from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ReferenceAssetMigrationTests(unittest.TestCase):
    def test_migrates_global_reference_assets_to_task_owner_scoped_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_root = root / "webui-inputs"
            source_data_root = root / "webui-outputs" / "source-data"
            data = b"legacy-reference"
            asset_id = hashlib.sha256(data).hexdigest()
            legacy_dir = input_root / "reference-assets" / asset_id[:2]
            legacy_dir.mkdir(parents=True)
            legacy_json = legacy_dir / f"{asset_id}.json"
            legacy_png = legacy_dir / f"{asset_id}.png"
            legacy_json.write_text(
                json.dumps(
                    {
                        "id": asset_id,
                        "sha256": asset_id,
                        "filename": "old.png",
                        "stored_filename": f"{asset_id}.png",
                        "mime_type": "image/png",
                    }
                ),
                encoding="utf-8",
            )
            legacy_png.write_bytes(data)
            task_dir = source_data_root / "tasks" / "2026-06-27"
            task_dir.mkdir(parents=True)
            (task_dir / "20260627173625-4c1c1b7e.metadata.json").write_text(
                json.dumps(
                    {
                        "task_id": "20260627173625-4c1c1b7e",
                        "owner_id": "user_1",
                        "reference_assets": [{"id": asset_id}],
                        "input_sources": [{"kind": "asset", "id": asset_id}],
                    }
                ),
                encoding="utf-8",
            )

            script = Path(__file__).resolve().parents[1] / "scripts" / "migrate_reference_assets_to_owners.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--input-root",
                    str(input_root),
                    "--source-data-root",
                    str(source_data_root),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            scoped_dir = input_root / "reference-assets" / "users" / "user_1" / "reference-assets" / asset_id[:2]
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["copied_files"], 2)
            self.assertEqual((scoped_dir / f"{asset_id}.json").read_text(encoding="utf-8"), legacy_json.read_text(encoding="utf-8"))
            self.assertEqual((scoped_dir / f"{asset_id}.png").read_bytes(), data)
            self.assertTrue(legacy_json.exists())
            self.assertTrue(legacy_png.exists())


if __name__ == "__main__":
    unittest.main()
