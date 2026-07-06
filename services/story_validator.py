import re


class StoryValidator:

    MIN_SLIDES = 5
    MIN_WORDS = 20
    MAX_WORDS = 80

    @staticmethod
    def validate(story):

        errors = []

        # ---------- Story Info ----------

        if not story.story_info.title.strip():
            errors.append("Story title is missing.")

        if not story.story_info.subtitle.strip():
            errors.append("Story subtitle is missing.")

        if not story.story_info.moral.strip():
            errors.append("Story moral is missing.")

        # ---------- Slides ----------

        if len(story.slides) < StoryValidator.MIN_SLIDES:
            errors.append(
                f"Story must contain at least {StoryValidator.MIN_SLIDES} slides."
            )

        seen_titles = set()

        character_name = story.character_sheet.main_character.name.lower()

        character_mentions = 0

        for slide in story.slides:

            if not slide.title.strip():
                errors.append(
                    f"Slide {slide.page} has no title."
                )

            title = slide.title.strip().lower()

            if title in seen_titles:
                errors.append(
                    f"Duplicate slide title: '{slide.title}'."
                )

            seen_titles.add(title)

            words = len(slide.text.split())

            if words < StoryValidator.MIN_WORDS:
                errors.append(
                    f"Slide {slide.page} is too short ({words} words)."
                )

            if words > StoryValidator.MAX_WORDS:
                errors.append(
                    f"Slide {slide.page} is too long ({words} words)."
                )

            if character_name in slide.text.lower():
                character_mentions += 1

            if not re.match(r"^#[0-9A-Fa-f]{6}$", slide.background_color):
                errors.append(
                    f"Slide {slide.page} has an invalid background color."
                )

        # ---------- Character ----------

        if character_mentions == 0:
            errors.append(
                "Main character never appears in the story."
            )

        # ---------- Ending ----------

        last_slide = story.slides[-1].text.lower()

        positive_words = [
            "happy",
            "smile",
            "peace",
            "love",
            "sleep",
            "dream",
            "hug",
            "safe",
            "kind",
        ]

        if not any(word in last_slide for word in positive_words):
            errors.append(
                "Story ending does not appear to have a positive resolution."
            )

        return (
            len(errors) == 0,
            errors,
        )