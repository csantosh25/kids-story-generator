from pathlib import Path

from services.openai_tts_service import OpenAITTSService


class NarrationService:

    def __init__(self):

        self.tts = OpenAITTSService()

    def generate(self, story, assets):

        lines = []

        # --------------------------------------------------
        # Story Title
        # --------------------------------------------------

        lines.append(story.story_info.title)
        lines.append("")

        # --------------------------------------------------
        # Story Subtitle
        # --------------------------------------------------

        if getattr(story.story_info, "subtitle", ""):
            lines.append(story.story_info.subtitle)
            lines.append("")

        # --------------------------------------------------
        # Story Slides
        # --------------------------------------------------

        for slide in story.slides:

            if getattr(slide, "title", ""):
                lines.append(slide.title)
                lines.append("")

            lines.append(slide.text)
            lines.append("")

        # --------------------------------------------------
        # Ending
        # --------------------------------------------------

        lines.append("The End.")
        lines.append("")
        lines.append(
            "If you enjoyed this story, come back tomorrow for another adventure!"
        )

        narration = "\n".join(lines)

        # --------------------------------------------------
        # Save narration text
        # --------------------------------------------------

        text_file = Path(assets.output_folder) / "narration.txt"

        text_file.write_text(
            narration,
            encoding="utf-8",
        )

        print(f"✅ Narration text saved: {text_file}")

        # --------------------------------------------------
        # Generate MP3 using OpenAI TTS
        # --------------------------------------------------

        mp3_file = Path(assets.output_folder) / "narration.mp3"

        self.tts.generate(
            text=narration,
            output_file=mp3_file,
        )

        return mp3_file