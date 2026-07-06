from openai import OpenAI

from config.settings import OPENAI_API_KEY


class OpenAIService:

    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)

    def generate_cover(self, prompt):

        result = self.client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1536",
            quality="high"
        )

        return result.data[0].b64_json