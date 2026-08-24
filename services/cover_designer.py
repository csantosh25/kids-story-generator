from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from utils.text_layout import fit_text_block, draw_text_block


class CoverDesigner:

    WIDTH = 1080
    HEIGHT = 1350

    # When cropping to 4:5, keep more of the TOP of the source image (where
    # a storybook character's head/face is most likely to be) and trim more
    # from the bottom (which is covered by the title overlay anyway).
    TOP_CROP_BIAS = 0.3

    TITLE_MARGIN_X = 60
    TITLE_MAX_SIZE = 70
    TITLE_MIN_SIZE = 34
    TITLE_MAX_AREA_RATIO = 0.32
    TITLE_MIN_OVERLAY_HEIGHT = 220
    TITLE_VERTICAL_PADDING = 40

    def _crop_to_aspect_ratio(self, img):

        width, height = img.size

        target_height = width * self.HEIGHT / self.WIDTH

        if height > target_height:

            target_height = round(target_height)

            crop_amount = height - target_height

            top = round(crop_amount * self.TOP_CROP_BIAS)
            bottom = height - (crop_amount - top)

            img = img.crop((0, top, width, bottom))

        return img

    def render(self, story, assets):

        input_image = assets.get_cover_path()
        output_image = assets.get_final_cover_path()

        title = story.story_info.title

        img = Image.open(input_image)
        img = self._crop_to_aspect_ratio(img)
        img = img.resize((self.WIDTH, self.HEIGHT))

        overlay = Image.new(
            "RGBA",
            img.size,
            (0, 0, 0, 0),
        )

        draw = ImageDraw.Draw(overlay)

        font_path = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "fonts"
            / "Poppins-Bold.ttf"
        )

        max_title_width = self.WIDTH - (2 * self.TITLE_MARGIN_X)
        max_title_area_height = self.HEIGHT * self.TITLE_MAX_AREA_RATIO

        font, lines, line_height = fit_text_block(
            draw=draw,
            text=title,
            font_path=font_path,
            max_width=max_title_width,
            max_height=max_title_area_height,
            start_size=self.TITLE_MAX_SIZE,
            min_size=self.TITLE_MIN_SIZE,
        )

        text_block_height = line_height * len(lines)

        overlay_height = max(
            self.TITLE_MIN_OVERLAY_HEIGHT,
            round(text_block_height + (2 * self.TITLE_VERTICAL_PADDING)),
        )

        # Never let the overlay exceed the safe title area, guaranteeing
        # the title bar (and therefore the text inside it) always stays
        # fully within the image canvas.
        overlay_height = min(
            overlay_height,
            round(max_title_area_height + (2 * self.TITLE_VERTICAL_PADDING)),
        )

        draw.rectangle(
            [
                (0, self.HEIGHT - overlay_height),
                (self.WIDTH, self.HEIGHT),
            ],
            fill=(0, 0, 0, 120),
        )

        text_start_y = (
            self.HEIGHT
            - overlay_height
            + ((overlay_height - text_block_height) / 2)
        )

        draw_text_block(
            draw=draw,
            lines=lines,
            font=font,
            x=self.TITLE_MARGIN_X,
            y=text_start_y,
            line_height=line_height,
            fill="white",
        )

        result = Image.alpha_composite(
            img.convert("RGBA"),
            overlay,
        )

        result.save(output_image)

        print(f"✅ Final cover saved: {output_image}")

        return output_image
