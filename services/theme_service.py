import json
import random
from pathlib import Path


class ThemeService:

    def __init__(self):

        self.file = Path("data/themes.json")

        if not self.file.exists():
            raise FileNotFoundError(
                "data/themes.json not found."
            )

        self.themes = json.loads(
            self.file.read_text(encoding="utf-8")
        )

    def get_theme(self):

        return random.choice(self.themes)