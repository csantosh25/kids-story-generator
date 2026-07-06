from services.slide_service import SlideService


class SlideAgent:

    def __init__(self):
        self.service = SlideService()

    def generate(self, story, assets):

        print("📄 Generating slides...")

        for slide in story.slides:

            self.service.create_slide(

                title=story.story_info.title,

                story=slide.text,

                page=slide.page,

                background=slide.background_color,

                output_file=assets.get_slide_path(slide.page),

            )

        print("✅ Slides created.")