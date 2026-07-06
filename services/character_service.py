import json
from pathlib import Path
import random


class CharacterService:

    def __init__(self):

        file = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "characters.json"
        )

        with open(file, encoding="utf-8") as f:
            self.characters = json.load(f)

    def random_character(self):

        return random.choice(self.characters)