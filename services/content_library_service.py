import json
from pathlib import Path
from datetime import datetime


class ContentLibraryService:

    def __init__(self):

        self.file = Path("data/content_library.json")

        self.file.parent.mkdir(exist_ok=True)

        if not self.file.exists():
            self.file.write_text("[]", encoding="utf-8")

    # --------------------------------------------------
    # Internal Methods
    # --------------------------------------------------

    def _load(self):

        return json.loads(
            self.file.read_text(encoding="utf-8")
        )

    def _save(self, data):

        self.file.write_text(
            json.dumps(
                data,
                indent=4,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    # --------------------------------------------------
    # Public Methods
    # --------------------------------------------------

    def get_all_stories(self):

        return self._load()

    def get_story(self, content_id):

        for story in self._load():

            if story["content_id"] == content_id:
                return story

        return None

    def next_content_id(self):

        library = self._load()

        if not library:
            return "KS-000001"

        numbers = [
            int(item["content_id"].split("-")[1])
            for item in library
        ]

        return f"KS-{max(numbers)+1:06d}"

    def add_story(
        self,
        story,
        assets,
        character_name,
    ):

        library = self._load()

        entry = {

            "content_id": self.next_content_id(),

            "created_date": datetime.now().strftime("%Y-%m-%d"),

            "status": "completed",

            "title": story.story_info.title,

            "theme": story.story_info.theme,

            "character": {
                "name": character_name,
                "species": story.character_sheet.main_character.species,
            },

            "folder": str(assets.output_folder),

            "instagram": {
                "posted": True,
                "post_url": ""
            },

            "pinterest": {
                "posted": False,
                "pin_url": ""
            },

            "reel": {
                "generated": False,
                "video": ""
            },

            "youtube": {
                "posted": False,
                "url": ""
            }

        }

        library.append(entry)

        self._save(library)

        print(
            f"✅ Added to Content Library: {entry['content_id']}"
        )

    def update_reel(
        self,
        content_id,
        video_path,
    ):

        library = self._load()

        for story in library:

            if story["content_id"] == content_id:

                story["reel"]["generated"] = True
                story["reel"]["video"] = str(video_path)

                break

        self._save(library)

    def update_pinterest(
        self,
        content_id,
        pin_url="",
    ):

        library = self._load()

        for story in library:

            if story["content_id"] == content_id:

                story["pinterest"]["posted"] = True
                story["pinterest"]["pin_url"] = pin_url

                break

        self._save(library)