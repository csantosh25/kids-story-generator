from reportlab.pdfgen import canvas
from PIL import Image


class PDFService:

    def generate(self, story, assets):

        output_file = assets.output_folder / "story_book.pdf"

        pdf = canvas.Canvas(str(output_file))

        # Cover page
        cover = assets.get_final_cover_path()

        if cover.exists():

            img = Image.open(cover)

            width, height = img.size

            pdf.setPageSize((width, height))

            pdf.drawImage(
                str(cover),
                0,
                0,
                width=width,
                height=height,
            )

            pdf.showPage()

        # Story slides
        for slide in story.slides:

            image = assets.get_slide_image_path(slide.page)

            if image.exists():

                img = Image.open(image)

                width, height = img.size

                pdf.setPageSize((width, height))

                pdf.drawImage(
                    str(image),
                    0,
                    0,
                    width=width,
                    height=height,
                )

                pdf.showPage()

        pdf.save()

        print(f"✅ Story PDF saved: {output_file}")

        return output_file