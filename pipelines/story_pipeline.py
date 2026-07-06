from agents.story_agent import StoryAgent
from agents.image_agent import ImageAgent

from services.asset_manager import AssetManager
from services.cover_designer import CoverDesigner
from services.carousel_renderer import CarouselRenderer
from services.publishing_service import PublishingService
from services.email_service import EmailService
from services.report_service import ReportService
from services.pdf_service import PDFService

from pipelines.pipeline_result import PipelineResult

from utils.logger import log


class StoryPipeline:

    def __init__(self):

        self.story_agent = StoryAgent()
        self.image_agent = ImageAgent()
        self.cover_designer = CoverDesigner()
        self.carousel_renderer = CarouselRenderer()
        self.publishing_service = PublishingService()
        self.email_service = EmailService()
        self.report_service = ReportService()
        self.pdf_service = PDFService()

    def run(self, topic):

        log("Generating story...")

        from utils.text_utils import slugify

        story = self.story_agent.generate_story(topic)

        assets = AssetManager(
            slugify(story.story_info.title)
        )

        assets.save_story_json(story)

        log("Story generated.")

        log("Generating cover image with OpenAI...")

        self.image_agent.generate_cover(
            story,
            assets,
        )

        log("Cover image generated.")

        log("Designing Instagram cover...")

        self.cover_designer.render(
            story,
            assets,
        )

        log("Cover designed.")

        log("Rendering carousel slides...")

        self.carousel_renderer.render(
            story,
            assets,
        )

        log("Carousel rendering completed.")

        log("Generating publishing guide...")

        self.publishing_service.generate(
            story,
            assets,
        )

        log("Publishing guide generated.")

        log("Generating report...")

        self.report_service.generate(
            story,
            assets,
        )

        log("Report generated.")

        log("Generating Story Book PDF...")

        self.pdf_service.generate(
            story,
            assets,
        )

        log("Story Book PDF generated.")

        log("Sending email...")

        self.email_service.send_story(
            story,
            assets,
        )

        log("Email sent.")

        log("Pipeline completed successfully.")
        return PipelineResult(
            story=story,
            output_folder=assets.output_folder,
            cover_image=assets.get_final_cover_path(),
        )