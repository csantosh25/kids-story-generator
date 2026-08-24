class CoverPromptBuilder:

    MAX_COVER_SUPPORTING_CHARACTERS = 2

    FALLBACK_WORD_LIMIT = 15

    @staticmethod
    def _supporting_brief(supporting_characters):

        selected = supporting_characters[:CoverPromptBuilder.MAX_COVER_SUPPORTING_CHARACTERS]

        if not selected:
            return (
                "No important supporting character for this cover. Show "
                "only the main character. Do not invent one."
            )

        sections = []

        for supporting in selected:

            role = supporting.role or "a close friend in this story"

            sections.append(
                f"Name: {supporting.name}\n"
                f"Species: {supporting.species}\n"
                f"Appearance: {supporting.appearance}\n"
                f"Role: {role}"
            )

        return (
            "Include this/these supporting character(s), interacting "
            "naturally with the main character. Preserve their exact "
            "appearance, species, and colors. Do not invent any character "
            "beyond those listed here.\n\n" + "\n\n".join(sections)
        )

    @staticmethod
    def _visual_action(story):

        action = (story.cover.visual_action or "").strip()

        if action:
            return action

        # Defensive fallback only, for older-style story data that predates
        # the concise cover fields: a short excerpt, never the full slide.
        words = story.slides[0].text.split()

        excerpt = " ".join(words[:CoverPromptBuilder.FALLBACK_WORD_LIMIT])

        if len(words) > CoverPromptBuilder.FALLBACK_WORD_LIMIT:
            excerpt += "..."

        return excerpt

    @staticmethod
    def build(story):
        """Builds the primary, concise cover visual brief. Deliberately
        does NOT include full slide narrative text — only short, structured
        fields needed to draw the scene."""

        character = story.character_sheet.main_character
        supporting_characters = story.character_sheet.supporting_characters
        first_slide = story.slides[0]

        setting = (story.cover.setting or first_slide.visual_theme or "a friendly outdoor setting").strip()
        visual_action = CoverPromptBuilder._visual_action(story)
        visual_object = (story.cover.visual_object or first_slide.icon or "").strip()
        mood = (story.cover.mood or "warm and cheerful").strip()

        supporting_brief = CoverPromptBuilder._supporting_brief(supporting_characters)

        prompt = f"""
        Create a children's storybook illustration for a book cover.

        CHILDREN'S STORYBOOK ILLUSTRATION

        Friendly animal characters. Wholesome, everyday setting. Warm,
        colorful, Pixar-quality 3D storybook style suitable for young
        children aged 4-7.

        MAIN CHARACTER (visually dominant)

        Name: {character.name}
        Species: {character.species}
        Appearance: {character.appearance}

        SUPPORTING CHARACTER(S)

        {supporting_brief}

        SETTING

        {setting}

        MAIN VISUAL ACTION

        {visual_action}

        IMPORTANT STORY OBJECT

        {visual_object or "none"}

        MOOD

        {mood}

        COMPOSITION

        Children's storybook composition with foreground, middle-ground and
        background depth. The main character is visually dominant and
        performing the main action, in natural proportion — do not let any
        character fill the entire frame. Keep the lower portion of the
        image visually calm and clean; the application adds a title bar
        there afterward.

        STRICT CONTENT REQUIREMENTS

        - Children's storybook illustration only, in a wholesome everyday setting.
        - Friendly animal characters, smiling and safe.
        - No text, no title, no subtitle, no words, no letters, no numbers, no logo, no watermark.
        - No scary content, no violence, no injury, no weapons, no dangerous behavior, no adult themes.
        - Nothing frightening, threatening, or unsafe for a young child.

        NEGATIVE PROMPT

        text, title, subtitle, words, letters, numbers, logo, watermark,
        scary content, violence, weapons, blood, injury, dangerous
        behavior, adult content, static portrait pose, character facing
        camera without action, character filling the entire frame, plain
        empty background, extra unrelated characters, random unrelated
        objects
        """

        return prompt.strip()

    @staticmethod
    def build_fallback(story):
        """A substantially simpler, more generic prompt used only after the
        primary prompt's OUTPUT was blocked by the provider's moderation
        system. Deliberately avoids proper names and story specifics to
        maximize the chance of a safe, benign result."""

        character = story.character_sheet.main_character
        supporting_characters = story.character_sheet.supporting_characters

        supporting_clause = ""

        if supporting_characters:

            supporting = supporting_characters[0]

            supporting_clause = f", with a friendly {supporting.species.lower()} nearby"

        return (
            f"A wholesome children's storybook illustration of a friendly "
            f"{character.species.lower()}{supporting_clause}, in a bright "
            f"natural setting. The characters are smiling and safe. Warm "
            f"daylight. Soft, colorful 3D storybook style. No text, no "
            f"words, no letters, no numbers, no logo, no watermark. No "
            f"scary content, no violence, no weapons."
        )
