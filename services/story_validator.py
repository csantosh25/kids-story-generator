import re


class StoryValidator:

    MIN_SLIDES = 4
    MAX_SLIDES = 6
    MIN_WORDS = 30
    MAX_WORDS = 90

    MAX_SENTENCE_WORDS = 14

    MAX_SUPPORTING_CHARACTERS = 2
    MIN_SUPPORTING_APPEARANCE_LENGTH = 10

    # Soft "too advanced" word list, mirroring prompts/writing_rules.txt.
    # A hit here fails validation (triggering the existing regenerate/retry
    # loop in StoryAgent) rather than rewriting text ourselves.
    COMPLEX_WORDS = [
        "wondered", "marvelled", "marveled", "gazed", "extraordinary",
        "magnificent", "splendid", "delighted", "astonished", "graceful",
        "enchanted", "bewildered", "remarkable", "sparkling", "shimmering",
        "nestled", "wandered", "enormous", "exhausted", "discovered",
        "hurried", "frightened", "gathered", "peaceful", "curious",
    ]

    BEDTIME_WORDS = [
        "moon", "moonlight", "star", "stars", "sleep", "sleepy", "asleep",
        "dream", "dreams", "dreamed", "dreamt", "bedtime", "pajama",
        "pyjama", "tucked in", "goodnight", "good night",
    ]

    POSITIVE_WORDS = [
        "happy", "smile", "peace", "love", "sleep", "dream", "hug",
        "safe", "kind", "proud", "excited", "warm", "glad", "together",
    ]

    REQUIRED_HASHTAG_COUNT = 5

    @staticmethod
    def validate(story, day=None):

        errors = []

        # ---------- Story Info ----------

        if not story.story_info.title.strip():
            errors.append("Story title is missing.")

        if not story.story_info.subtitle.strip():
            errors.append("Story subtitle is missing.")

        if not story.story_info.moral.strip():
            errors.append("Story moral is missing.")

        # ---------- Slide Count ----------

        slide_count = len(story.slides)

        if not (StoryValidator.MIN_SLIDES <= slide_count <= StoryValidator.MAX_SLIDES):
            errors.append(
                f"Story must contain between {StoryValidator.MIN_SLIDES} and "
                f"{StoryValidator.MAX_SLIDES} slides (found {slide_count})."
            )

        pages = sorted(slide.page for slide in story.slides)

        if pages != list(range(1, slide_count + 1)):
            errors.append(
                "Slide page numbers must be sequential starting at 1 and "
                "match the total slide count."
            )

        # ---------- Slides ----------

        seen_titles = set()

        character_name = story.character_sheet.main_character.name.lower()

        character_mentions = 0

        complex_word_hits = []

        long_sentence_hits = []

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

            # ---- Simple English: banned complex vocabulary ----

            text_lower = slide.text.lower()

            found_words = [
                word for word in StoryValidator.COMPLEX_WORDS
                if re.search(rf"\b{re.escape(word)}\b", text_lower)
            ]

            if found_words:
                complex_word_hits.append((slide.page, found_words))

            # ---- Simple English: sentence length ----

            sentences = re.split(r"(?<=[.!?])\s+", slide.text.strip())

            for sentence in sentences:

                sentence_words = len(sentence.split())

                if sentence_words > StoryValidator.MAX_SENTENCE_WORDS:
                    long_sentence_hits.append((slide.page, sentence_words))

        if complex_word_hits:

            for page, words in complex_word_hits:
                errors.append(
                    f"Slide {page} uses advanced vocabulary not suitable "
                    f"for a young child: {', '.join(words)}."
                )

        if long_sentence_hits:

            for page, sentence_words in long_sentence_hits:
                errors.append(
                    f"Slide {page} has a sentence with {sentence_words} words "
                    f"(max {StoryValidator.MAX_SENTENCE_WORDS}); simplify it "
                    f"into shorter sentences."
                )

        # ---------- Character ----------

        if character_mentions == 0:
            errors.append(
                "Main character never appears in the story."
            )

        # ---------- Supporting Characters ----------

        supporting_characters = story.character_sheet.supporting_characters

        if len(supporting_characters) > StoryValidator.MAX_SUPPORTING_CHARACTERS:
            errors.append(
                f"Too many supporting characters ({len(supporting_characters)}); "
                f"only include characters who are central to the story "
                f"(max {StoryValidator.MAX_SUPPORTING_CHARACTERS})."
            )

        for supporting in supporting_characters:

            if not supporting.name.strip():
                errors.append(
                    "A supporting character is missing a name."
                )

            if not supporting.species.strip():
                errors.append(
                    f"Supporting character '{supporting.name}' is missing a species."
                )

            if len(supporting.appearance.strip()) < StoryValidator.MIN_SUPPORTING_APPEARANCE_LENGTH:
                errors.append(
                    f"Supporting character '{supporting.name}' needs a more "
                    f"descriptive appearance so it can be drawn consistently."
                )

        # ---------- Ending ----------

        last_slide = story.slides[-1].text.lower()

        if not any(word in last_slide for word in StoryValidator.POSITIVE_WORDS):
            errors.append(
                "Story ending does not appear to have a positive resolution."
            )

        # ---------- Bedtime-Only Plot Check ----------

        if day and day != "Sunday":

            bedtime_hits = [
                word for word in StoryValidator.BEDTIME_WORDS
                if word in last_slide
            ]

            if bedtime_hits:
                errors.append(
                    "Story ends with a bedtime/sleep/moon/star theme, but "
                    "today's category is not Sunday's Bedtime category. "
                    f"Vary the ending (found: {', '.join(bedtime_hits)})."
                )

        # ---------- Publishing: Hashtags ----------

        hashtags = story.publishing.hashtags

        if len(hashtags) != StoryValidator.REQUIRED_HASHTAG_COUNT:
            errors.append(
                f"Publishing hashtags must contain exactly "
                f"{StoryValidator.REQUIRED_HASHTAG_COUNT} hashtags "
                f"(found {len(hashtags)})."
            )

        lowered_hashtags = [tag.lower() for tag in hashtags]

        if len(lowered_hashtags) != len(set(lowered_hashtags)):
            errors.append(
                "Publishing hashtags contain duplicates."
            )

        return (
            len(errors) == 0,
            errors,
        )
