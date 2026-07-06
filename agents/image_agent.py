import base64

from services.openai_service import OpenAIService
from services.cover_prompt_builder import CoverPromptBuilder


class ImageAgent:

    def __init__(self):
        self.service = OpenAIService()

    def generate_cover(self, story, assets):

        print("🎨 Generating cover image...")

        prompt = CoverPromptBuilder.build(story)

        image = self.service.generate_cover(prompt)

        filepath = assets.get_cover_path()

        with open(filepath, "wb") as f:
            f.write(base64.b64decode(image))

        print(f"✅ Cover saved: {filepath}")

        return filepath