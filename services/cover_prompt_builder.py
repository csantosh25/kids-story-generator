class CoverPromptBuilder:

    @staticmethod
    def _build_supporting_character_section(supporting):

        if supporting is None:
            return """
        SUPPORTING CHARACTERS

        This story has no important supporting character for the cover. Show
        only the main character. Do not invent an additional character.
        """

        return f"""
        IMPORTANT SUPPORTING CHARACTER

        Name:
        {supporting.name}

        Animal:
        {supporting.species}

        Appearance:
        {supporting.appearance}

        Role in this story:
        {supporting.role or "a close friend in this story"}

        Include this supporting character alongside the main character,
        interacting naturally with them (for example: helping, side by side,
        handing something over, or reacting together).

        Preserve this exact appearance for the supporting character. Do not
        change their species, colors, or defining features.

        Do not invent any other characters beyond the main character and
        this one supporting character.
        """

    @staticmethod
    def _build_central_idea(story, supporting):

        parts = [story.story_info.theme]

        if story.story_info.moral:
            parts.append(story.story_info.moral)

        if supporting is not None and supporting.role:
            parts.append(supporting.role)

        return " — ".join(part for part in parts if part)

    @staticmethod
    def build(story):

        character = story.character_sheet.main_character
        first_slide = story.slides[0]

        supporting_characters = story.character_sheet.supporting_characters
        supporting = supporting_characters[0] if supporting_characters else None

        supporting_section = CoverPromptBuilder._build_supporting_character_section(supporting)
        central_idea = CoverPromptBuilder._build_central_idea(story, supporting)

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

        MAIN CHARACTER (must remain the visually dominant character)

        Name:
        {character.name}

        Animal:
        {character.species}

        Appearance:
        {character.appearance}

        Personality:
        {character.personality}

        {supporting_section}

        BOOK TITLE (for context only — do NOT draw this as text in the image)

        {story.story_info.title}

        SUBTITLE (for context only — do NOT draw this as text in the image)

        {story.story_info.subtitle}

        CENTRAL STORY IDEA

        {central_idea}

        The cover should visually communicate this central idea, not just
        show the main character alone.

        STORY ACTION

        Scene description:
        {first_slide.text}

        Visual theme:
        {first_slide.visual_theme}

        Visual motif:
        {first_slide.icon}

        Show the character actively performing the central action from this scene.

        Do not show the character simply standing, smiling, facing the camera, or posing for a portrait.

        Do not invent unrelated story events.

        STORY ENVIRONMENT

        Show the actual environment and location suggested by the scene description above.

        The environment must be recognizable and relevant to what is happening.

        Avoid generic or empty backgrounds.

        STORY ELEMENTS

        Include approximately 2-5 meaningful visual elements that are actually supported by the scene
        (for example: food, flowers, books, toys, leaves, baskets, trees, furniture, other story
        characters, or objects involved in the action).

        Do not add random decorative objects simply to fill space.

        COMPOSITION

        Use a children's storybook composition with visual depth across three layers:

        Foreground:
        small story-relevant details or objects.

        Middle ground:
        the main character (and the supporting character, if specified above) performing the main action.

        Background:
        the story environment, with enough detail to establish place and context.

        CHARACTER SCALE

        The main character should be visually important, clearly recognizable, and remain the
        dominant figure in the composition. A supporting character, if present, should be smaller
        or positioned so it does not compete with the main character for visual weight.

        The main character should occupy a natural proportion of the scene so that the environment
        and story action remain visible.

        Do not let any character fill the entire frame.

        LIGHTING

        Warm morning sunlight or soft golden afternoon light depending on the story.

        COLOURS

        Bright
        Cheerful
        High contrast
        Kid friendly

        BACKGROUND

        Keep the background sufficiently detailed to communicate the story setting, while maintaining
        clear visual hierarchy so the main character performing the action remains the focal point.

        Do not blur or soften the background into emptiness.

        TITLE SPACE

        The application overlays the story title in a bar across the LOWER portion of the final
        image. Keep the lower portion of the composition relatively calm and visually clean so the
        title overlay remains legible, without turning the overall image into a portrait.

        VISUAL STYLE

        The final result should feel like a premium children's storybook cover illustrating a moment
        from the story, not a character portrait.

        NO TEXT IN THE IMAGE (STRICT)

        Generate ONLY the illustration itself. The title, subtitle, and all branding are added
        separately by the application after this image is generated.

        The image must contain absolutely no letters, words, numbers, titles, subtitles, captions,
        labels, signage, book covers, logos, or watermarks of any kind, even as background details
        (for example: no signs, no open books with visible writing, no readable text on objects).

        QUALITY

        Ultra detailed
        Pixar quality
        Storybook illustration
        Professional children's publishing quality

        NEGATIVE PROMPT

        No text
        No title text
        No subtitle text
        No letters or numbers of any kind
        No signage or readable text on objects
        No book covers or pages with visible writing
        No watermark
        No logo
        No characters beyond the main character and the one specified supporting character (if any)
        No blurry face
        No cropped face
        No dark image
        No static portrait pose
        No character facing camera without action
        No plain or empty background
        No generic background
        No excessive close-up
        No character filling the entire frame
        No random unrelated objects
        No changes to any character's species, colors, or defining features
        """

        return prompt.strip()
