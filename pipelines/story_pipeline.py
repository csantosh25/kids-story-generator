from services.theme_service import ThemeService
from agents.story_agent import StoryAgent
from agents.image_agent import ImageAgent

from services.asset_manager import AssetManager
from services.cover_designer import CoverDesigner
from services.carousel_renderer import CarouselRenderer
from services.publishing_service import PublishingService
from services.email_service import EmailService
from services.report_service import ReportService
from services.pdf_service import PDFService
from services.narration_service import NarrationService

from pipelines.pipeline_result import PipelineResult
from services.story_history_service import StoryHistoryService
from services.content_library_service import ContentLibraryService

from utils.logger import log


class StoryPipeline:

    def __init__(self, progress_callback=None):

        self.progress_callback = progress_callback

        self.story_agent = StoryAgent()
        self.image_agent = ImageAgent()
        self.cover_designer = CoverDesigner()
        self.carousel_renderer = CarouselRenderer()
        self.publishing_service = PublishingService()
        self.email_service = EmailService()
        self.report_service = ReportService()
        self.pdf_service = PDFService()
        self.story_history = StoryHistoryService()
        self.narration_service = NarrationService()
        self.content_library = ContentLibraryService()

    def update_progress(self, progress, step):

        if self.progress_callback:
            self.progress_callback(progress, step)

    def run(self, topic=None):

        from utils.text_utils import slugify

        # --------------------------------------------------
        # Generate Story
        # --------------------------------------------------

        self.update_progress(10, "📖 Preparing Story Context...")

        log("Loading story context...")

        theme_service = ThemeService()

        story_context = theme_service.get_story_context()

        # If a manual topic was selected from the UI,
        # override today's random topic.
        if topic:
            story_context["topic"] = topic

        self.update_progress(15, "✍️ Generating Story...")

        log("Generating story...")

        story = self.story_agent.generate_story(
            story_context
        )

        self.story_history.add_story(
            story,
            story_context,
        )

        assets = AssetManager(
            slugify(story.story_info.title)
        )

        assets.save_story_json(story)

        log("Story generated.")

        # --------------------------------------------------
        # Generate Cover Image
        # --------------------------------------------------

        self.update_progress(30, "🎨 Generating Cover Image...")

        log("Generating cover image with OpenAI...")

        self.image_agent.generate_cover(
            story,
            assets,
        )

        log("Cover image generated.")

        # --------------------------------------------------
        # Design Instagram Cover
        # --------------------------------------------------

        self.update_progress(45, "🖼️ Designing Cover...")

        log("Designing Instagram cover...")

        self.cover_designer.render(
            story,
            assets,
        )

        log("Cover designed.")

        # --------------------------------------------------
        # Render Carousel
        # --------------------------------------------------

        self.update_progress(60, "📱 Rendering Carousel...")

        log("Rendering carousel slides...")

        self.carousel_renderer.render(
            story,
            assets,
        )

        log("Carousel rendering completed.")

        # --------------------------------------------------
        # Publishing Guide
        # --------------------------------------------------

        self.update_progress(70, "📝 Creating Publishing Guide...")

        log("Generating publishing guide...")

        self.publishing_service.generate(
            story,
            assets,
        )

        log("Publishing guide generated.")

        # --------------------------------------------------
        # Report
        # --------------------------------------------------

        self.update_progress(80, "📊 Generating Report...")

        log("Generating report...")

        self.report_service.generate(
            story,
            assets,
        )

        log("Report generated.")

        # --------------------------------------------------
        # PDF
        # --------------------------------------------------

        self.update_progress(90, "📚 Creating Story Book PDF...")

        log("Generating Story Book PDF...")

        self.pdf_service.generate(
            story,
            assets,
        )

        log("Story Book PDF generated.")


        # --------------------------------------------------
        # Email
        # --------------------------------------------------

        self.update_progress(95, "📧 Sending Email...")

        log("Sending email...")

        self.email_service.send_story(
            story,
            assets,
        )

        log("Email sent.")

        # --------------------------------------------------
        # Content Librar
        # --------------------------------------------------

        log("Updating Content Library...")

        self.content_library.add_story(
            story=story,
            assets=assets,
            character_name=story.character_sheet.main_character.name,
        )

        # --------------------------------------------------
        # Complete
        # --------------------------------------------------

        self.update_progress(100, "✅ Completed!")

        log("Pipeline completed successfully.")

        return PipelineResult(
            story=story,
            output_folder=assets.output_folder,
            cover_image=assets.get_final_cover_path(),
        )