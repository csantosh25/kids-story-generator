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
        the main character performing the main action.

        Background:
        the story environment, with enough detail to establish place and context.

        CHARACTER SCALE

        The character should be visually important and clearly recognizable, but should occupy a
        natural proportion of the scene so that the environment and story action remain visible.

        Do not let the character fill the entire frame.

        LIGHTING

        Warm morning sunlight or soft golden afternoon light depending on the story.

        COLOURS

        Bright
        Cheerful
        High contrast
        Kid friendly

        BACKGROUND

        Keep the background sufficiently detailed to communicate the story setting, while maintaining
        clear visual hierarchy so the character performing the action remains the focal point.

        Do not blur or soften the background into emptiness.

        TITLE SPACE

        The application overlays the story title in a bar across the LOWER portion of the final
        image. Keep the lower portion of the composition relatively calm and visually clean so the
        title overlay remains legible, without turning the overall image into a portrait.

        VISUAL STYLE

        The final result should feel like a premium children's storybook cover illustrating a moment
        from the story, not a character portrait.

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
        No static portrait pose
        No character facing camera without action
        No plain or empty background
        No generic background
        No excessive close-up
        No character filling the entire frame
        No random unrelated objects
        """

        return prompt.strip()