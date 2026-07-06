from datetime import datetime


class ReportService:

    def generate(self, story, assets):

        report = assets.output_folder / "report.md"

        word_count = sum(
            len(slide.text.split())
            for slide in story.slides
        )

        content = f"""# Story Generation Report

## Story Information

Title:
{story.story_info.title}

Subtitle:
{story.story_info.subtitle}

Theme:
{story.story_info.theme}

Moral:
{story.story_info.moral}

Reading Time:
{story.story_info.reading_time}

Target Age:
{story.story_info.target_age}

---

## Character

Name:
{story.character_sheet.main_character.name}

Species:
{story.character_sheet.main_character.species}

Personality:
{story.character_sheet.main_character.personality}

---

## Statistics

Slides:
{len(story.slides)}

Total Words:
{word_count}

Generated:
{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

## Output Files

✓ cover.png

✓ cover_final.png

✓ publish.md

✓ story.json

✓ Carousel Slides

---

Generation completed successfully.
"""

        report.write_text(
            content,
            encoding="utf-8",
        )

        print(f"✅ Report saved: {report}")

        return report