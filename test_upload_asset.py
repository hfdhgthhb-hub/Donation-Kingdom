import tempfile
import unittest
from pathlib import Path

from scripts.upload_asset import (
    MAX_UPLOAD_BYTES,
    create_and_wait,
    parse_asset_id,
    parse_operation_id,
    require_positive_decimal,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or ""
        self.headers = {}
        self.ok = 200 <= status_code < 400

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.post_calls = []
        self.request_calls = []

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        return FakeResponse({"path": "operations/op-123"}, status_code=200)

    def request(self, method, url, **kwargs):
        self.request_calls.append((method, url, kwargs))
        return FakeResponse(
            {
                "done": True,
                "response": {
                    "assetId": "123456789",
                    "path": "assets/123456789",
                },
            }
        )


class UploadAssetTests(unittest.TestCase):
    def test_creator_id_must_be_positive_decimal(self):
        self.assertEqual(require_positive_decimal("12345", "Creator User ID"), "12345")
        for value in ("", "0", "-1", "12abc", "1.5"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    require_positive_decimal(value, "Creator User ID")

    def test_parse_operation_id(self):
        self.assertEqual(parse_operation_id({"path": "operations/abc123"}), "abc123")
        with self.assertRaises(ValueError):
            parse_operation_id({"path": "assets/123"})

    def test_parse_asset_id_from_asset_id_field(self):
        payload = {"done": True, "response": {"assetId": "123456"}}
        self.assertEqual(parse_asset_id(payload), "123456")

    def test_parse_asset_id_from_path_fallback(self):
        payload = {"done": True, "response": {"path": "assets/654321"}}
        self.assertEqual(parse_asset_id(payload), "654321")

    def test_parse_asset_id_rejects_missing_id(self):
        with self.assertRaises(RuntimeError):
            parse_asset_id({"done": True, "response": {"displayName": "Map"}})

    def test_successful_create_and_poll(self):
        fake = FakeSession()
        with tempfile.TemporaryDirectory() as temp_dir:
            model = Path(temp_dir) / "map.rbxm"
            model.write_bytes(b"test-rbxm-data")
            asset_id = create_and_wait(
                model_path=model,
                api_key="not-a-real-key",
                creator_id="12345",
                creator_type="user",
                display_name="Test Map",
                description="Test description",
                session=fake,
                poll_interval=0,
                max_wait_seconds=5,
            )
        self.assertEqual(asset_id, "123456789")
        self.assertEqual(len(fake.post_calls), 1)
        self.assertEqual(len(fake.request_calls), 1)
        post_args, post_kwargs = fake.post_calls[0]
        self.assertEqual(post_args[0], "https://apis.roblox.com/assets/v1/assets")
        self.assertEqual(post_kwargs["headers"]["x-api-key"], "not-a-real-key")
        self.assertIn("fileContent", post_kwargs["files"])
        self.assertEqual(post_kwargs["files"]["fileContent"][2], "model/x-rbxm")
        request_metadata = __import__("json").loads(post_kwargs["data"]["request"])
        self.assertEqual(request_metadata["assetType"], "Model")
        self.assertEqual(request_metadata["displayName"], "Test Map")
        self.assertEqual(
            request_metadata["creationContext"]["creator"],
            {"userId": "12345"},
        )
        method, operation_url, poll_kwargs = fake.request_calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(
            operation_url,
            "https://apis.roblox.com/assets/v1/operations/op-123",
        )
        self.assertEqual(poll_kwargs["headers"]["x-api-key"], "not-a-real-key")


    def test_group_creator_uses_group_id_field(self):
        fake = FakeSession()
        with tempfile.TemporaryDirectory() as temp_dir:
            model = Path(temp_dir) / "map.rbxm"
            model.write_bytes(b"test-rbxm-data")
            create_and_wait(
                model_path=model,
                api_key="not-a-real-key",
                creator_id="98765",
                creator_type="group",
                display_name="Group Map",
                description="",
                session=fake,
                poll_interval=0,
                max_wait_seconds=5,
            )
        _, post_kwargs = fake.post_calls[0]
        request_data = __import__("json").loads(post_kwargs["data"]["request"])
        self.assertEqual(request_data["creationContext"]["creator"], {"groupId": "98765"})

    def test_upload_rejects_oversized_file_before_http(self):
        fake = FakeSession()
        with tempfile.TemporaryDirectory() as temp_dir:
            model = Path(temp_dir) / "large.rbxm"
            with model.open("wb") as handle:
                handle.truncate(MAX_UPLOAD_BYTES + 1)
            with self.assertRaises(ValueError):
                create_and_wait(
                    model_path=model,
                    api_key="not-a-real-key",
                    creator_id="12345",
                    creator_type="user",
                    display_name="Test Map",
                    description="",
                    session=fake,
                )
        self.assertEqual(fake.post_calls, [])


if __name__ == "__main__":
    unittest.main()
