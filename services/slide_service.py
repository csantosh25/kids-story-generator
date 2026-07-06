from pathlib import Path


class SlideService:

    def __init__(self):
        self.template = Path(
            "templates/slide_template.html"
        ).read_text(encoding="utf-8")

    def create_slide(
        self,
        title,
        story,
        page,
        background,
        output_file,
    ):

        html = self.template

        html = html.replace(
            "{{title}}",
            title,
        )

        html = html.replace(
            "{{story}}",
            story,
        )

        html = html.replace(
            "{{page}}",
            str(page),
        )

        html = html.replace(
            "{{background_color}}",
            background,
        )

        Path(output_file).write_text(
            html,
            encoding="utf-8",
        )