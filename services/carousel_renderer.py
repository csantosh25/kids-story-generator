from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from services.brand_loader import BrandLoader
from utils.text_layout import fit_text_block, draw_text_block, wrap_text_to_width


class CarouselRenderer:

    WIDTH = 1080
    HEIGHT = 1350

    MARGIN = 80

    TITLE_SIZE = 60
    TITLE_MIN_SIZE = 34
    SUBTITLE_SIZE = 52
    SUBTITLE_MIN_SIZE = 30
    BODY_SIZE = 42
    FOOTER_SIZE = 30

    HEADER_TOP = 108
    HEADER_MAX_HEIGHT = 150
    SUBHEADER_MAX_HEIGHT = 130

    BODY_TOP_MIN = 340
    BODY_BOTTOM = 1180

    def __init__(self):

        self.brand = BrandLoader.load()

        self.font_folder = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "fonts"
        )

        self.font_path = str(self.font_folder / "Poppins-Bold.ttf")

        self.title_font = ImageFont.truetype(
            self.font_path,
            self.TITLE_SIZE,
        )

        self.footer_font = ImageFont.truetype(
            self.font_path,
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

        content_x = self.MARGIN
        content_width = self.WIDTH - (2 * self.MARGIN)

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

        # -------------------------------------------------------------
        # Story title (auto-wraps and shrinks instead of running off-canvas)
        # -------------------------------------------------------------

        y = self.HEADER_TOP

        title_font, title_lines, title_line_h = fit_text_block(
            draw=draw,
            text=story_title,
            font_path=self.font_path,
            max_width=content_width,
            max_height=self.HEADER_MAX_HEIGHT,
            start_size=self.TITLE_SIZE,
            min_size=self.TITLE_MIN_SIZE,
        )

        y = draw_text_block(
            draw=draw,
            lines=title_lines,
            font=title_font,
            x=content_x,
            y=y,
            line_height=title_line_h,
            fill=self.brand["text_color"],
        )

        y += 12

        # -------------------------------------------------------------
        # Tagline
        # -------------------------------------------------------------

        draw.text(
            (content_x, y),
            self.brand["tagline"],
            font=self.footer_font,
            fill="#777777",
        )

        tagline_bbox = draw.textbbox(
            (content_x, y),
            self.brand["tagline"],
            font=self.footer_font,
        )

        y = tagline_bbox[3] + 16

        # -------------------------------------------------------------
        # Slide title (auto-wraps and shrinks instead of running off-canvas)
        # -------------------------------------------------------------

        subtitle_font, subtitle_lines, subtitle_line_h = fit_text_block(
            draw=draw,
            text=slide.title,
            font_path=self.font_path,
            max_width=content_width,
            max_height=self.SUBHEADER_MAX_HEIGHT,
            start_size=self.SUBTITLE_SIZE,
            min_size=self.SUBTITLE_MIN_SIZE,
        )

        y = draw_text_block(
            draw=draw,
            lines=subtitle_lines,
            font=subtitle_font,
            x=content_x,
            y=y,
            line_height=subtitle_line_h,
            fill=self.brand["primary_color"],
        )

        y += 24

        # -------------------------------------------------------------
        # Divider + body box
        # (bottom edge stays fixed so the footer/page-numbering position
        # never moves, regardless of how much the header grew)
        # -------------------------------------------------------------

        divider_y = y

        draw.line(
            [(80, divider_y), (1000, divider_y)],
            fill="#DDDDDD",
            width=3,
        )

        body_top = max(self.BODY_TOP_MIN, divider_y + 30)

        draw.rounded_rectangle(
            [(60, body_top), (1020, self.BODY_BOTTOM)],
            radius=55,
            fill="#FFFDF8",
        )

        body_font = self.get_body_font(slide.text)

        body_max_width = 1020 - 60 - 80  # matches original ~34-char wrap box

        wrapped_lines = wrap_text_to_width(draw, slide.text, body_font, body_max_width)

        body_bbox = draw.textbbox((0, 0), "Agy", font=body_font)
        body_line_height = (body_bbox[3] - body_bbox[1]) + 12

        draw_text_block(
            draw=draw,
            lines=wrapped_lines,
            font=body_font,
            x=100,
            y=body_top + 50,
            line_height=body_line_height,
            fill="#333333",
        )

        # -------------------------------------------------------------
        # Footer (unchanged position: brand handle + page numbering)
        # -------------------------------------------------------------

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
