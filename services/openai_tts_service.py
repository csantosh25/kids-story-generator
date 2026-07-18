from pathlib import Path

from openai import OpenAI

from config.settings import OPENAI_API_KEY


class OpenAITTSService:

    def __init__(self):

        self.client = OpenAI(
            api_key=OPENAI_API_KEY
        )

    def generate(
        self,
        text: str,
        output_file: Path,
    ):

        with self.client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
        ) as response:

            response.stream_to_file(output_file)

        print(f"✅ Narration MP3 saved: {output_file}")

        return output_file