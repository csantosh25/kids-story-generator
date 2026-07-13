import json
import random
from pathlib import Path
from datetime import datetime


class ThemeService:

    def __init__(self):

        self.theme_file = Path("data/daily_themes.json")
        self.character_file = Path("data/characters.json")

        if not self.theme_file.exists():
            raise FileNotFoundError(
                "data/daily_themes.json not found."
            )

        if not self.character_file.exists():
            raise FileNotFoundError(
                "data/characters.json not found."
            )

        self.daily_themes = json.loads(
            self.theme_file.read_text(encoding="utf-8")
        )

        self.characters = json.loads(
            self.character_file.read_text(encoding="utf-8")
        )

    # ---------------------------------------
    # Returns today's complete story context
    # ---------------------------------------
    def get_story_context(self):

        today = datetime.now().strftime("%A")

        day_theme = self.daily_themes[today]

        topic = random.choice(day_theme["topics"])

        character = self.characters[today]["character"]

        return {
            "day": today,
            "category": day_theme["category"],
            "topic": topic,
            "learning_goal": day_theme["learning_goal"],
            "story_style": day_theme["story_style"],
            "color": day_theme["color"],
            "emoji": day_theme["emoji"],
            "character": character,
        }

    # ---------------------------------------
    # Backward compatibility
    # Existing code can continue calling this
    # ---------------------------------------
    def random_theme(self):

        context = self.get_story_context()

        return context["topic"]

    # ---------------------------------------
    # Return all themes for dropdown
    # ---------------------------------------
    def get_all_categories(self):

        return [
            self.daily_themes[day]["category"]
            for day in self.daily_themes
        ]

    # ---------------------------------------
    # Return today's day name
    # ---------------------------------------
    def get_today(self):

        return datetime.now().strftime("%A")