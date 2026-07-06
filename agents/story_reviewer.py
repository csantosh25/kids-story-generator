from google import genai

from config.settings import GEMINI_API_KEY


class StoryReviewer:

    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY)

    def review(self, story_json):

        prompt = f"""
You are reviewing a children's bedtime story.

Check the following:

1. Is the story appropriate for children aged 4–8?
2. Is the moral positive?
3. Are all pages connected logically?
4. Does the main character stay consistent?
5. Is the ending comforting?
6. Is the JSON complete?

Return ONLY JSON.

{{
    "approved": true,
    "score": 0-100,
    "feedback": [
        ""
    ]
}}

Story:

{story_json}
"""

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

        return response.text