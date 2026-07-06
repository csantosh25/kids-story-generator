from agents.story_agent import StoryAgent
from agents.image_agent import ImageAgent
from agents.slide_agent import SlideAgent
from agents.email_agent import EmailAgent
from services.carousel_renderer import CarouselRenderer

from services.asset_manager import AssetManager
from services.cover_designer import CoverDesigner


class StoryGenerator:

    def __init__(self):

        self.story_agent = StoryAgent()

        self.image_agent = ImageAgent()

        self.slide_agent = SlideAgent()

        self.cover_designer = CoverDesigner()

        self.email_agent = EmailAgent()

        self.carousel_renderer = CarouselRenderer()

    def generate(self, theme):

        print("=" * 60)
        print("🌙 DreamForge AI Studio")
        print("=" * 60)

        story = self.story_agent.generate_story(theme)

        assets = AssetManager(story)

        assets.save_story_json()

        self.image_agent.generate_cover(
            story,
            assets,
        )

        self.cover_designer.create_cover(
            assets,
            story,
        )

        self.carousel_renderer.render(
            story,
            assets,
        )

        self.slide_agent.generate(
            story,
            assets,
        )

        self.email_agent.generate(
            story,
            assets,
        )

        print()
        print("🎉 Story generation completed.")
        print(f"📁 Output: {assets.folder}")

        return assets.folder