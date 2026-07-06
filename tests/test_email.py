from pathlib import Path

from services.email_service import EmailService


class DummyStoryInfo:
    title = "Test Story"
    theme = "Testing"
    reading_time = "3 minutes"
    moral = "Always test before deploying."


class DummyStory:
    story_info = DummyStoryInfo()


class DummyAssets:
    output_folder = Path("output")


EmailService().send_story(
    DummyStory(),
    DummyAssets(),
)