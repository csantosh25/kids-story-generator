import json

from google import genai
from pydantic import ValidationError

from config.settings import GEMINI_API_KEY
from models.story_models import StoryPackage
from prompts.master_prompt_builder import PromptBuilder
from services.story_settings import StorySettings
from services.story_validator import StoryValidator
from utils.json_parser import JsonParser


class StoryGenerationError(RuntimeError):
    """Raised when no valid story is produced within the retry budget."""


class StoryAgent:

    MAX_ATTEMPTS = 3

    def __init__(self):

        self.settings = StorySettings.load()

        self.client = genai.Client(api_key=GEMINI_API_KEY)

        self.master_prompt = PromptBuilder().build()


    def generate_story(self, story_context):

        prompt = self.master_prompt

        # ---------------------------------------
        # Story Context
        # ---------------------------------------

        prompt = prompt.replace(
            "{{day}}",
            story_context["day"],
        )

        prompt = prompt.replace(
            "{{category}}",
            story_context["category"],
        )

        prompt = prompt.replace(
            "{{learning_goal}}",
            story_context["learning_goal"],
        )

        prompt = prompt.replace(
            "{{story_style}}",
            story_context["story_style"],
        )

        prompt = prompt.replace(
            "{{theme}}",
            story_context["topic"],
        )

        # ---------------------------------------
        # Character
        # ---------------------------------------

        character = story_context["character"]

        prompt = prompt.replace(
            "{{character_name}}",
            character["name"],
        )

        prompt = prompt.replace(
            "{{character_animal}}",
            character.get("species", character.get("animal", "Animal")),
        )

        prompt = prompt.replace(
            "{{character_appearance}}",
            character.get("appearance", ""),
        )

        prompt = prompt.replace(
            "{{character_personality}}",
            ", ".join(character["personality"]),
        )

        prompt = prompt.replace(
            "{{character_place}}",
            character.get("home", character.get("favorite_place", "Wonderwood Valley")),
        )

        prompt = prompt.replace(
            "{{character_catchphrase}}",
            character["catchphrase"],
        )

        # ---------------------------------------
        # Story Settings
        # ---------------------------------------

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

        # ---------------------------------------
        # Generate Story
        # ---------------------------------------

        failures = []

        attempt_prompt = prompt

        for attempt in range(1, self.MAX_ATTEMPTS + 1):

            print(f"📖 Story generation attempt {attempt}/{self.MAX_ATTEMPTS}")

            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=attempt_prompt,
            )

            try:
                story = self._parse_story(response.text)

            except (ValueError, TypeError) as exc:
                # ValueError covers json.JSONDecodeError and pydantic's
                # ValidationError; TypeError covers an empty response.
                reasons = self._describe_parse_error(exc)

            else:
                self._enforce_canonical_fields(story, story_context)

                valid, reasons = StoryValidator.validate(
                    story,
                    day=story_context.get("day"),
                )

                if valid:
                    print("✅ Story validation passed.")
                    return story

            print(f"❌ Story generation attempt {attempt}/{self.MAX_ATTEMPTS} failed:")

            for reason in reasons:
                print(f"   • {reason}")

            failures.append(reasons)

            # Each retry starts from the original prompt plus guidance for
            # the most recent failure only, so the prompt never snowballs.
            attempt_prompt = prompt + self._corrective_guidance(reasons)

        raise StoryGenerationError(
            f"Failed to generate a valid story after {self.MAX_ATTEMPTS} "
            f"attempts. Last failure: {'; '.join(failures[-1])}"
        )

    # ---------------------------------------
    # Helpers
    # ---------------------------------------

    @staticmethod
    def _parse_story(text):

        if not text or not text.strip():
            raise TypeError("Gemini returned an empty response.")

        data = JsonParser.extract_json(text)

        return StoryPackage.model_validate(data)

    @staticmethod
    def _describe_parse_error(exc):

        # Deliberately excludes the raw model output / field values: only
        # the location and type of the problem are logged.

        if isinstance(exc, ValidationError):
            return [
                f"Schema error at {'.'.join(str(part) for part in error['loc']) or 'root'}: "
                f"{error['msg']}"
                for error in exc.errors(include_input=False, include_url=False)
            ]

        if isinstance(exc, json.JSONDecodeError):
            return [
                f"Response was not valid JSON ({exc.msg} at line "
                f"{exc.lineno}, column {exc.colno})."
            ]

        return [f"Could not parse response: {exc}"]

    @staticmethod
    def _corrective_guidance(reasons):

        issues = "\n".join(f"- {reason}" for reason in reasons)

        return (
            "\n\nIMPORTANT: Your previous response was rejected for the "
            "following reasons:\n"
            f"{issues}\n"
            "Generate the complete story again, fixing every issue above. "
            "Return ONLY valid JSON that exactly follows the required "
            "structure, with every field present."
        )

    @staticmethod
    def _enforce_canonical_fields(story, story_context):

        # ---------------------------------------
        # Enforce Canonical Character Identity
        # ---------------------------------------

        canonical_character = story_context["character"]

        story.character_sheet.main_character.name = canonical_character["name"]

        story.character_sheet.main_character.species = canonical_character.get(
            "species",
            canonical_character.get("animal", "Animal"),
        )

        story.character_sheet.main_character.appearance = canonical_character.get(
            "appearance",
            "",
        )

        story.character_sheet.main_character.personality = ", ".join(
            canonical_character["personality"]
        )

        # ---------------------------------------
        # Enforce Established Posting Time
        # ---------------------------------------
        # Guaranteed in code, not left to the model's judgment -- the
        # prompt already asks for this value, but this overwrite
        # ensures it's never a random/unrelated time regardless of
        # what the model actually returns.

        story.publishing.best_posting_time = (
            "7:00–9:00 PM IST — our established Instagram posting window."
        )
