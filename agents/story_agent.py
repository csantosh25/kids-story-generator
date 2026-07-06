from google import genai

from config.settings import GEMINI_API_KEY
from models.story_models import StoryPackage
from prompts.master_prompt_builder import PromptBuilder
from services.character_service import CharacterService
from services.story_settings import StorySettings
from services.story_validator import StoryValidator
from utils.json_parser import JsonParser


class StoryAgent:

    def __init__(self):

        self.settings = StorySettings.load()

        self.client = genai.Client(api_key=GEMINI_API_KEY)

        self.master_prompt = PromptBuilder().build()

        self.character_service = CharacterService()

    def generate_story(self, topic):

        # Pick a character for this story
        character = self.character_service.random_character()

        prompt = self.master_prompt

        prompt = prompt.replace(
            "{{theme}}",
            topic,
        )

        prompt = prompt.replace(
            "{{character_name}}",
            character["name"],
        )

        prompt = prompt.replace(
            "{{character_animal}}",
            character["animal"],
        )

        prompt = prompt.replace(
            "{{character_appearance}}",
            character["appearance"],
        )

        prompt = prompt.replace(
            "{{character_personality}}",
            character["personality"],
        )

        prompt = prompt.replace(
            "{{character_place}}",
            character["favorite_place"],
        )

        prompt = prompt.replace(
            "{{character_catchphrase}}",
            character["catchphrase"],
        )

        prompt = prompt.replace(
            "{{target_age}}",
            self.settings["target_age"],
        )

        prompt = prompt.replace(
            "{{reading_level}}",
            self.settings["reading_level"],
        )

        prompt = prompt.replace(
            "{{pages}}",
            str(self.settings["pages"]),
        )

        prompt = prompt.replace(
            "{{words_per_page}}",
            str(self.settings["words_per_page"]),
        )

        prompt = prompt.replace(
            "{{tone}}",
            self.settings["tone"],
        )

        prompt = prompt.replace(
            "{{ending_style}}",
            self.settings["ending_style"],
        )

        for attempt in range(1, 4):

            print(f"📖 Story generation attempt {attempt}/3")

            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )

            data = JsonParser.extract_json(response.text)

            story = StoryPackage(**data)

            valid, errors = StoryValidator.validate(story)

            if valid:
                print("✅ Story validation passed.")
                return story

            print("❌ Story validation failed:")

            for error in errors:
                print(f"   • {error}")

        raise RuntimeError(
            "Failed to generate a valid story after 3 attempts."
        )