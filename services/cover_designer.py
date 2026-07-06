from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class CoverDesigner:

    def render(self, story, assets):

        input_image = assets.get_cover_path()
        output_image = assets.get_final_cover_path()

        title = story.story_info.title

        img = Image.open(input_image)
        img = img.resize((1080, 1920))

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        draw.rectangle(
            [(0, 1450), (1080, 1920)],
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
            90,
        )

        draw.text(
            (70, 1550),
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