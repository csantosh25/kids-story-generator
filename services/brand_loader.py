import json
from pathlib import Path


class BrandLoader:

    @staticmethod
    def load():

        file = (
            Path(__file__).resolve().parent.parent
            / "config"
            / "brand.json"
        )

        with open(file, encoding="utf-8") as f:
            return json.load(f)