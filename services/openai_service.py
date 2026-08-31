from openai import OpenAI, BadRequestError

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

    def generate_image(self, prompt, size="1024x1536", quality="high"):
        """Generic text-to-image call, reusing the same client/config as
        generate_cover(). Used by ReelImageService for dedicated Reel
        scene illustrations -- kept separate from generate_cover so the
        daily cover-generation call path/behavior is never touched."""

        result = self.client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size=size,
            quality=quality,
        )

        return result.data[0].b64_json

    @staticmethod
    def is_moderation_blocked(error):
        """Detects OpenAI's moderation_blocked BadRequestError without
        depending on one specific SDK error-shape, since the exact
        attribute layout has varied across openai-python versions."""

        if not isinstance(error, BadRequestError):
            return False

        if getattr(error, "code", None) == "moderation_blocked":
            return True

        body = getattr(error, "body", None)

        if isinstance(body, dict) and body.get("code") == "moderation_blocked":
            return True

        return "moderation_blocked" in str(error)

    @staticmethod
    def extract_request_id(error):
        """Best-effort extraction of the OpenAI request id for diagnostics,
        without assuming one specific SDK version's exact attribute name."""

        request_id = getattr(error, "request_id", None)

        if request_id:
            return request_id

        response = getattr(error, "response", None)

        headers = getattr(response, "headers", None)

        if headers:
            return headers.get("x-request-id")

        return None