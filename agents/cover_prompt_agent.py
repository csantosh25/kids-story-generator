from google import genai

from config.settings import GEMINI_API_KEY


class CoverPromptAgent:

    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY)

    def generate(self, story):

        prompt = f"""
You are an expert children's book illustrator.

Read the story below and create ONE detailed illustration prompt.

Requirements:

- Portrait composition (4:5)
- Pixar-quality 3D illustration
- Bright colours
- Soft bedtime lighting
- Character appearance must exactly match the story.
- Include the environment.
- Do NOT include any text.
- One single scene.
- Highly detailed.
- Optimised for OpenAI image generation.

Story title:
{story.story_info.title}

Story summary:
{story.story_info.description}

Main character:
{story.story_info.main_character}

Return ONLY the prompt.
"""

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

        return response.text.strip()