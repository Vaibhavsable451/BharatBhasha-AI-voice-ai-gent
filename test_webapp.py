import base64
import os
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
from webapp import (
    _image_context_content,
    _validate_image_data_url,
    create_app,
    main,
)


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


class AzureAccessTests(unittest.IsolatedAsyncioTestCase):
    def test_app_service_defaults_to_port_8000_and_all_interfaces(self):
        with (
            patch.dict(
                os.environ,
                {
                    "WEBSITE_INSTANCE_ID": "instance",
                    "WEBSITE_HOSTNAME": "bharatbhashaai.azurewebsites.net",
                },
                clear=True,
            ),
            patch("webapp.web.run_app") as run_app,
        ):
            main()

        self.assertEqual(run_app.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(run_app.call_args.kwargs["port"], 8000)

    async def test_app_service_routes_require_auth_and_restrict_host(self):
        hostname = "bharatbhashaai.azurewebsites.net"
        with patch.dict(
            os.environ,
            {
                "WEBSITE_INSTANCE_ID": "instance",
                "WEBSITE_HOSTNAME": hostname,
                "APP_SERVICE_ALLOWED_HOSTS": "",
            },
        ):
            client = TestClient(TestServer(create_app(8000)))
            await client.start_server()
            try:
                health = await client.get(
                    "/healthz", headers={"Host": hostname}
                )
                self.assertEqual(health.status, 200)

                unauthenticated = await client.get(
                    "/", headers={"Host": hostname}
                )
                self.assertEqual(unauthenticated.status, 401)

                authenticated = await client.get(
                    "/",
                    headers={
                        "Host": hostname,
                        "X-MS-CLIENT-PRINCIPAL": "authenticated",
                    },
                )
                self.assertEqual(authenticated.status, 200)

                untrusted = await client.get(
                    "/",
                    headers={
                        "Host": "attacker.example",
                        "X-MS-CLIENT-PRINCIPAL": "authenticated",
                    },
                )
                self.assertEqual(untrusted.status, 403)
            finally:
                await client.close()


if __name__ == "__main__":
    unittest.main()
