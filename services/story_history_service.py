import json
from pathlib import Path
from datetime import date


class StoryHistoryService:

    def __init__(self):
        self.file = Path("data/story_history.json")

        if not self.file.exists():
            self.file.write_text("[]", encoding="utf-8")

    def load(self):
        return json.loads(
            self.file.read_text(encoding="utf-8")
        )

    def save(self, history):
        self.file.write_text(
            json.dumps(history, indent=2),
            encoding="utf-8"
        )

    def add_story(self, story, context):
        history = self.load()

        history.append({
            "date": str(date.today()),
            "title": story.story_info.title,
            "character": context["character"]["name"],
            "theme": context["topic"],
            "lesson": context["learning_goal"],
            "setting": story.story_info.theme,
            "story_archetype": context.get("story_archetype", "")
        })

        # Keep only the latest 50 stories
        history = history[-50:]

        self.save(history)

    def recent_summary(self, limit=10):
        history = self.load()
        return history[-limit:]