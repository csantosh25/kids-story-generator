import json
from pathlib import Path


class StorySettings:

    @staticmethod
    def load():

        file = (
            Path(__file__).resolve().parent.parent
            / "config"
            / "story_settings.json"
        )

        with open(file, encoding="utf-8") as f:
            return json.load(f)