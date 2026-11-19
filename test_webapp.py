import base64
import unittest

from webapp import _image_context_content, _validate_image_data_url


class ImageMessageTests(unittest.TestCase):
    def setUp(self):
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        self.image = (
            "data:image/png;base64,"
            + base64.b64encode(image_bytes).decode("ascii")
        )

    def test_builds_image_context_without_requesting_a_text_turn(self):
        content = _image_context_content({"images": [self.image]})

        self.assertIn("spoken question", content[0]["text"])
        self.assertEqual(content[1]["type"], "input_image")
        self.assertEqual(content[1]["image_url"], self.image)

    def test_rejects_empty_image_context(self):
        with self.assertRaisesRegex(ValueError, "at least one image"):
            _image_context_content({"images": []})

    def test_rejects_mime_mismatch_and_too_many_images(self):
        with self.assertRaisesRegex(ValueError, "bytes do not match"):
            _validate_image_data_url(
                self.image.replace("image/png", "image/jpeg", 1)
            )
        with self.assertRaisesRegex(ValueError, "no more than 3"):
            _image_context_content({"images": [self.image] * 4})


if __name__ == "__main__":
    unittest.main()
