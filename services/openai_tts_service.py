from pathlib import Path

from openai import OpenAI

from config.settings import OPENAI_API_KEY


# Default voice for the daily story pipeline's narration.mp3 (see
# services/narration_service.py). Left unchanged so that pipeline's
# behaviour is not affected by callers (e.g. the Reel pipeline) that pass
# their own `voice`.
DEFAULT_VOICE = "alloy"


class OpenAITTSService:

    def __init__(self):

        self.client = OpenAI(
            api_key=OPENAI_API_KEY
        )

    def generate(
        self,
        text: str,
        output_file: Path,
        voice: str = DEFAULT_VOICE,
        instructions: str = None,
    ):
        """Generates one TTS narration MP3 -- exactly one API call per
        invocation. `voice` must be one of the values the installed
        OpenAI SDK's Voice type actually supports (see
        openai.types.audio.speech_create_params.Voice); `instructions`
        is an optional free-text steering hint supported by the
        gpt-4o-mini-tts model used here (ignored by tts-1/tts-1-hd)."""

        request_kwargs = {
            "model": "gpt-4o-mini-tts",
            "voice": voice,
            "input": text,
        }

        if instructions:
            request_kwargs["instructions"] = instructions

        with self.client.audio.speech.with_streaming_response.create(
            **request_kwargs
        ) as response:

            response.stream_to_file(output_file)

        print(f"✅ Narration MP3 saved: {output_file}")

        return output_file