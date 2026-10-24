"""Unit tests for services/openai_service.py image generation.

The OpenAI client is fully mocked: no real API calls are made.
"""
import unittest
from unittest.mock import MagicMock, patch

from services.openai_service import IMAGE_MODEL, OpenAIService


def _make_service(b64="aW1hZ2UtYnl0ZXM="):
    with patch("services.openai_service.OpenAI") as client_cls:
        service = OpenAIService()

    image = MagicMock()
    image.b64_json = b64

    result = MagicMock()
    result.data = [image]

    service.client = client_cls.return_value
    service.client.images.generate.return_value = result

    return service


class OpenAIServiceImageModelTests(unittest.TestCase):

    def test_image_model_is_supported_replacement(self):
        self.assertEqual(IMAGE_MODEL, "gpt-image-2.5-flare")

    def test_generate_cover_uses_supported_model_and_portrait_settings(self):
        service = _make_service()

        b64 = service.generate_cover("a fox in a park")

        service.client.images.generate.assert_called_once_with(
            model="gpt-image-2.5-flare",
            prompt="a fox in a park",
            size="1024x1536",
            quality="high",
        )
        self.assertEqual(b64, "aW1hZ2UtYnl0ZXM=")

    def test_generate_image_uses_supported_model_and_default_settings(self):
        service = _make_service()

        b64 = service.generate_image("a reel scene")

        service.client.images.generate.assert_called_once_with(
            model="gpt-image-2.5-flare",
            prompt="a reel scene",
            size="1024x1536",
            quality="high",
        )
        self.assertEqual(b64, "aW1hZ2UtYnl0ZXM=")

    def test_generate_image_passes_through_custom_size_and_quality(self):
        service = _make_service(b64="Y3VzdG9t")

        b64 = service.generate_image("a scene", size="1024x1024", quality="medium")

        kwargs = service.client.images.generate.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-image-2.5-flare")
        self.assertEqual(kwargs["size"], "1024x1024")
        self.assertEqual(kwargs["quality"], "medium")
        self.assertEqual(b64, "Y3VzdG9t")

    def test_no_unsupported_parameters_sent(self):
        service = _make_service()

        service.generate_cover("p")
        service.generate_image("p")

        for call in service.client.images.generate.call_args_list:
            self.assertNotIn("response_format", call.kwargs)
            self.assertNotIn("style", call.kwargs)

    def test_retired_model_not_referenced(self):
        with open("services/openai_service.py", encoding="utf-8") as f:
            source = f.read()

        self.assertNotIn('model="gpt-image-1"', source)


if __name__ == "__main__":
    unittest.main()
