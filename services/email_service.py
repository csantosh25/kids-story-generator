from pathlib import Path
from email.message import EmailMessage
import mimetypes
import smtplib
import ssl

from config.settings import (
    SMTP_SERVER,
    SMTP_PORT,
    EMAIL_USERNAME,
    EMAIL_PASSWORD,
    EMAIL_TO,
)


class EmailService:

    def send_story(self, story, assets):

        msg = EmailMessage()

        msg["Subject"] = (
            f"📚 {story.story_info.title} "
            f"is Ready to Publish"
        )

        msg["From"] = EMAIL_USERNAME
        msg["To"] = EMAIL_TO

        body = f"""
Hello,

Your daily story has been generated successfully.

Title:
{story.story_info.title}

Theme:
{story.story_info.theme}

Reading Time:
{story.story_info.reading_time}

Moral:
{story.story_info.moral}

Attachments include:

• Final Cover
• Carousel Slides
• story.json
• publish.md

Happy Posting!
"""

        msg.set_content(body)

        for file in sorted(assets.output_folder.iterdir()):

            if not file.is_file():
                continue

            mime_type, _ = mimetypes.guess_type(file)

            if mime_type:

                maintype, subtype = mime_type.split("/")

            else:

                maintype = "application"
                subtype = "octet-stream"

            with open(file, "rb") as f:

                msg.add_attachment(
                    f.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=file.name,
                )

        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(
            SMTP_SERVER,
            SMTP_PORT,
            context=context,
        ) as smtp:

            smtp.login(
                EMAIL_USERNAME,
                EMAIL_PASSWORD,
            )

            smtp.send_message(msg)

        print("✅ Email sent successfully.")