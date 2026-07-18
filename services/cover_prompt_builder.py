class CoverPromptBuilder:

    @staticmethod
    def build(story):

        character = story.character_sheet.main_character
        first_slide = story.slides[0]

        prompt = f"""
        Create an award-winning children's storybook cover.

        STYLE
        Pixar-quality 3D illustration.
        Premium children's book artwork.
        Professional Disney-inspired lighting.
        Rich textures.
        Ultra detailed.

        FORMAT

        Instagram portrait (4:5).

        MAIN CHARACTER

        Name:
        {character.name}

        Animal:
        {character.species}

        Appearance:
        {character.appearance}

        Personality:
        {character.personality}

        BOOK TITLE

        {story.story_info.title}

        SUBTITLE

        {story.story_info.subtitle}

        SCENE

        {first_slide.text}

        VISUAL GOAL

        Create an image that immediately catches attention while scrolling Instagram.

        The character should show a strong happy emotion with large expressive eyes and a joyful smile.

        The character must occupy about 60–70% of the image.

        BACKGROUND

        Simple.
        Bright.
        Colourful.
        Clean.
        Not crowded.

        Use only a few supporting objects.

        LIGHTING

        Warm morning sunlight or soft golden afternoon light depending on the story.

        COLOURS

        Bright
        Cheerful
        High contrast
        Kid friendly

        COMPOSITION

        Character in the foreground.

        Background softly blurred.

        Leave clear empty space at the top for the title.

        QUALITY

        Ultra detailed
        Pixar quality
        Storybook illustration
        Professional children's publishing quality

        NEGATIVE PROMPT

        No text
        No watermark
        No logo
        No extra characters unless required
        No blurry face
        No cropped face
        No dark image
        """

        return prompt.strip()