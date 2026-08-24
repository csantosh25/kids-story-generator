import base64

from openai import BadRequestError

from services.openai_service import OpenAIService
from services.cover_prompt_builder import CoverPromptBuilder


class ImageAgent:

    def __init__(self):
        self.service = OpenAIService()

    def generate_cover(self, story, assets):

        print("🎨 Generating cover image...")

        prompt = CoverPromptBuilder.build(story)

        try:

            image = self.service.generate_cover(prompt)

        except BadRequestError as error:

            if not OpenAIService.is_moderation_blocked(error):
                raise

            print("⚠️ Cover image generation was blocked by the image safety system.")
            print("🔄 Trying a simplified safe cover prompt...")

            fallback_prompt = CoverPromptBuilder.build_fallback(story)

            try:

                image = self.service.generate_cover(fallback_prompt)

            except BadRequestError as fallback_error:

                if not OpenAIService.is_moderation_blocked(fallback_error):
                    raise

                request_id = OpenAIService.extract_request_id(fallback_error)

                print("❌ Cover generation failed after safe fallback attempt.")
                print(f"Request ID: {request_id or 'unavailable'}")

                raise RuntimeError(
                    "Cover image generation was blocked by the provider's "
                    "safety system on both the primary and the simplified "
                    "fallback prompt. "
                    f"Request ID: {request_id or 'unavailable'}"
                ) from fallback_error

            print("✅ Cover generated using fallback visual prompt.")

        filepath = assets.get_cover_path()

        with open(filepath, "wb") as f:
            f.write(base64.b64decode(image))

        print(f"✅ Cover saved: {filepath}")

        return filepath
