from __future__ import annotations

import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image


def _decode_data_url(data_url: str) -> tuple[str, bytes]:
    header, encoded = data_url.split(",", 1)
    return header, base64.b64decode(encoded)


def _jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), (240, 80, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _mpo_bytes() -> bytes:
    buffer = BytesIO()
    first = Image.new("RGB", (8, 8), (240, 80, 40))
    second = Image.new("RGB", (8, 8), (40, 120, 240))
    try:
        first.save(buffer, format="MPO", save_all=True, append_images=[second])
    except (OSError, KeyError, ValueError) as exc:
        raise unittest.SkipTest(f"Pillow cannot write MPO fixtures: {exc}") from exc
    return buffer.getvalue()


class WebUIExecutorInputTests(unittest.TestCase):
    def test_file_to_data_url_converts_mpo_reference_images_to_png(self) -> None:
        from codex_image.webui.executor_inputs import _file_to_data_url

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.mpo"
            path.write_bytes(_mpo_bytes())

            data_url = _file_to_data_url(path, mime_type="image/mpo")

        header, payload = _decode_data_url(data_url)
        self.assertEqual(header, "data:image/png;base64")
        with Image.open(BytesIO(payload)) as converted:
            self.assertEqual(converted.format, "PNG")
            self.assertEqual(converted.size, (8, 8))

    def test_file_to_data_url_uses_actual_supported_image_type_when_declared_type_is_wrong(self) -> None:
        from codex_image.webui.executor_inputs import _file_to_data_url

        jpeg_data = _jpeg_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.mpo"
            path.write_bytes(jpeg_data)

            data_url = _file_to_data_url(path, mime_type="image/mpo")

        header, payload = _decode_data_url(data_url)
        self.assertEqual(header, "data:image/jpeg;base64")
        self.assertEqual(payload, jpeg_data)

    def test_file_to_data_url_rejects_undecodable_unsupported_images(self) -> None:
        from codex_image.webui.executor_inputs import _file_to_data_url

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.heic"
            path.write_bytes(b"not an image")

            with self.assertRaises(HTTPException) as caught:
                _file_to_data_url(path, mime_type="image/heic")

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail, "Unsupported image type: could not decode image")

    def test_file_to_data_url_converts_animated_gif_reference_to_first_frame_png(self) -> None:
        from codex_image.webui.executor_inputs import _file_to_data_url

        buffer = BytesIO()
        frames = [Image.new("RGB", (8, 8), (240, 20, 20)), Image.new("RGB", (8, 8), (20, 20, 240))]
        frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "previous-output.gif"
            path.write_bytes(buffer.getvalue())

            data_url = _file_to_data_url(path, mime_type="image/gif")

        header, payload = _decode_data_url(data_url)
        self.assertEqual(header, "data:image/png;base64")
        with Image.open(BytesIO(payload)) as converted:
            self.assertEqual(converted.format, "PNG")
            self.assertEqual(getattr(converted, "n_frames", 1), 1)
            red, _, blue = converted.convert("RGB").getpixel((4, 4))
        self.assertGreater(red, 200, "first frame must be kept")
        self.assertLess(blue, 60)

    def test_static_gif_reference_is_sent_unchanged(self) -> None:
        from codex_image.webui.executor_inputs import _file_to_data_url

        buffer = BytesIO()
        Image.new("RGB", (8, 8), (20, 200, 20)).save(buffer, format="GIF")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "still.gif"
            path.write_bytes(buffer.getvalue())

            data_url = _file_to_data_url(path, mime_type="image/gif")

        header, payload = _decode_data_url(data_url)
        self.assertEqual(header, "data:image/gif;base64")
        self.assertEqual(payload, buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
