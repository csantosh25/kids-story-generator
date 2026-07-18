from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class CoverDesigner:

    WIDTH = 1080
    HEIGHT = 1350

    def render(self, story, assets):

        input_image = assets.get_cover_path()
        output_image = assets.get_final_cover_path()

        title = story.story_info.title

        img = Image.open(input_image)
        img = img.resize((self.WIDTH, self.HEIGHT))

        overlay = Image.new(
            "RGBA",
            img.size,
            (0, 0, 0, 0),
        )

        draw = ImageDraw.Draw(overlay)

        # Bottom overlay
        overlay_height = 220

        draw.rectangle(
            [
                (0, self.HEIGHT - overlay_height),
                (self.WIDTH, self.HEIGHT),
            ],
            fill=(0, 0, 0, 120),
        )

        font_path = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "fonts"
            / "Poppins-Bold.ttf"
        )

        font = ImageFont.truetype(
            str(font_path),
            70,
        )

        draw.text(
            (
                60,
                self.HEIGHT - 170,
            ),
            title,
            font=font,
            fill="white",
        )

        result = Image.alpha_composite(
            img.convert("RGBA"),
            overlay,
        )

        result.save(output_image)

        print(f"✅ Final cover saved: {output_image}")

        return output_image