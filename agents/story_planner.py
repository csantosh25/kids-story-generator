from google import genai

from config.settings import GEMINI_API_KEY


class StoryPlanner:

    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY)

    def create_outline(self, topic, character):

        prompt = f"""
Create a children's bedtime story outline.

Topic:
{topic}

Main Character:
{character['name']} ({character['animal']})

Return ONLY valid JSON.

Schema:

{{
    "title": "",
    "moral": "",
    "pages": [
        {{
            "page":1,
            "summary":""
        }}
    ]
}}
"""

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

        return response.text