from pathlib import Path
from textwrap import fill

from PIL import Image, ImageDraw, ImageFont

from services.brand_loader import BrandLoader


class CarouselRenderer:

    WIDTH = 1080
    HEIGHT = 1350

    MARGIN = 80

    TITLE_SIZE = 60
    BODY_SIZE = 42
    FOOTER_SIZE = 30

    def __init__(self):

        self.brand = BrandLoader.load()

        self.font_folder = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "fonts"
        )

        self.title_font = ImageFont.truetype(
            str(self.font_folder / "Poppins-Bold.ttf"),
            self.TITLE_SIZE,
        )

        self.footer_font = ImageFont.truetype(
            str(self.font_folder / "Poppins-Bold.ttf"),
            self.FOOTER_SIZE,
        )

    def get_body_font(self, text):

        words = len(text.split())

        if words < 60:
            size = 46
        elif words < 90:
            size = 42
        elif words < 120:
            size = 38
        else:
            size = 34

        return ImageFont.truetype(
            str(self.font_folder / "Poppins-Bold.ttf"),
            size,
        )

    def render(self, story, assets):

        for slide in story.slides:

            self.render_slide(
                slide=slide,
                story_title=story.story_info.title,
                total_pages=len(story.slides),
                output_file=assets.get_slide_image_path(slide.page),
            )

    def render_slide(
        self,
        slide,
        story_title,
        total_pages,
        output_file,
    ):

        image = Image.new(
            "RGB",
            (self.WIDTH, self.HEIGHT),
            slide.background_color,
        )

        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle(
            [(40, 20), (1040, 100)],
            radius=25,
            fill=self.brand["primary_color"],
        )

        brand_bbox = draw.textbbox(
            (0, 0),
            self.brand["brand_name"],
            font=self.footer_font,
        )

        brand_width = brand_bbox[2] - brand_bbox[0]

        draw.text(
            ((1080 - brand_width) / 2, 45),
            self.brand["brand_name"],
            font=self.footer_font,
            fill="white",
        )

        draw.text(
            (self.MARGIN, 100),
            story_title,
            font=self.title_font,
            fill=self.brand["text_color"],
        )

        draw.text(
            (self.MARGIN, 170),
            self.brand["tagline"],
            font=self.footer_font,
            fill="#777777",
        )

        draw.text(
            (self.MARGIN, 250),
            slide.title,
            font=self.title_font,
            fill=self.brand["primary_color"],
        )

        draw.line(
            [(80, 310), (1000, 310)],
            fill="#DDDDDD",
            width=3,
        )

        draw.rounded_rectangle(
            [(60, 340), (1020, 1180)],
            radius=55,
            fill="#FFFDF8",
        )

        wrapped = fill(slide.text, width=34)

        draw.multiline_text(
            (100, 390),
            wrapped,
            font=self.get_body_font(slide.text),
            spacing=12,
            fill="#333333",
        )

        draw.line(
            [(80, 1220), (1000, 1220)],
            fill="#DDDDDD",
            width=2,
        )

        footer = (
            f"{self.brand['instagram_handle']}   •   "
            f"Page {slide.page} of {total_pages}"
        )

        draw.text(
            (self.MARGIN, 1260),
            footer,
            font=self.footer_font,
            fill="#666666",
        )

        image.save(output_file)