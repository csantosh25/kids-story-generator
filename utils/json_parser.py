import json
import re


class JsonParser:

    @staticmethod
    def extract_json(text: str):

        # Remove markdown if Gemini accidentally returns it
        text = re.sub(r"```json", "", text)
        text = re.sub(r"```", "", text)

        text = text.strip()

        return json.loads(text)