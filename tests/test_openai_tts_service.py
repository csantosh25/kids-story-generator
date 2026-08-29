"""Unit tests for OpenAITTSService itself: proves `voice`/`instructions`
are forwarded correctly to the underlying OpenAI SDK call, that the
default stays "alloy" (so the daily pipeline's narration.mp3, which never
passes `voice`, keeps behaving exactly as before), and that exactly one
API call is made per generate() invocation. The OpenAI client is fully
mocked -- no real network call is ever made."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from services.openai_tts_service import OpenAITTSService, DEFAULT_VOICE


class OpenAITTSServiceTests(unittest.TestCase):

    def _make_service_with_mock_client(self):

        with patch("services.openai_tts_service.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            service = OpenAITTSService()

        return service, mock_client

    def test_default_voice_is_alloy_and_no_instructions_sent(self):
        """No caller-supplied voice/instructions -- must match the exact
        request the daily pipeline (services/narration_service.py) has
        always made, so that pipeline's behaviour is unaffected."""

        service, mock_client = self._make_service_with_mock_client()

        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.mp3"
            service.generate(text="hello", output_file=output)

        self.assertEqual(DEFAULT_VOICE, "alloy")

        _, kwargs = mock_client.audio.speech.with_streaming_response.create.call_args
        self.assertEqual(kwargs["model"], "gpt-4o-mini-tts")
        self.assertEqual(kwargs["voice"], "alloy")
        self.assertEqual(kwargs["input"], "hello")
        self.assertNotIn("instructions", kwargs)

    def test_custom_voice_and_instructions_are_forwarded(self):

        service, mock_client = self._make_service_with_mock_client()

        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.mp3"
            service.generate(
                text="Once upon a time...",
                output_file=output,
                voice="coral",
                instructions="warm and calm",
            )

        _, kwargs = mock_client.audio.speech.with_streaming_response.create.call_args
        self.assertEqual(kwargs["voice"], "coral")
        self.assertEqual(kwargs["instructions"], "warm and calm")

    def test_exactly_one_api_call_per_generate_invocation(self):

        service, mock_client = self._make_service_with_mock_client()

        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.mp3"
            service.generate(text="hello", output_file=output, voice="coral")

        mock_client.audio.speech.with_streaming_response.create.assert_called_once()

    def test_generate_returns_output_file_path(self):

        service, mock_client = self._make_service_with_mock_client()

        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.mp3"
            result = service.generate(text="hello", output_file=output)

        self.assertEqual(result, output)


if __name__ == "__main__":
    unittest.main()
