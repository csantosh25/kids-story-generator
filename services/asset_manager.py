import json
from pathlib import Path
from datetime import datetime


class AssetManager:

    def __init__(self, slug):

        self.slug = slug

        self.story_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.folder = (
            Path("output")
            / f"{self.story_id}_{self.slug}"
        )

        self.folder.mkdir(
            parents=True,
            exist_ok=True,
        )

    def save_story_json(self, story):

        file = self.folder / "story.json"

        file.write_text(
            json.dumps(
                story.model_dump(),
                indent=4,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return file

    def get_cover_path(self):
        return self.folder / "cover.png"

    def get_final_cover_path(self):
        return self.folder / "cover_final.png"

    def get_slide_path(self, page):
        return self.folder / f"slide_{page}.html"

    def get_slide_image_path(self, page):
        return self.folder / f"slide_{page}.png"

    @property
    def output_folder(self):
        return self.folder