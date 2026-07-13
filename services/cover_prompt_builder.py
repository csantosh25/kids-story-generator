class CoverPromptBuilder:

    @staticmethod
    def build(story):

        character = story.character_sheet.main_character
        first_slide = story.slides[0]

        prompt = f"""
Create a premium children's storybook cover for the Wonderwood Valley universe.

--------------------------------------------------
STYLE
--------------------------------------------------

Pixar-quality 3D illustration.

Disney-quality cinematic lighting.

Highly detailed.

Professional children's book illustration.

Warm, colourful, magical and family-friendly.

--------------------------------------------------
BOOK
--------------------------------------------------

Title:
{story.story_info.title}

Subtitle:
{story.story_info.subtitle}

--------------------------------------------------
MAIN CHARACTER
--------------------------------------------------

Name:
{character.name}

Species:
{character.species}

Appearance:
{character.appearance}

Personality:
{character.personality}

This character is a recurring character in the Wonderwood Valley story universe.

The appearance must remain consistent with previous illustrations.

Do not redesign the character.

--------------------------------------------------
SCENE
--------------------------------------------------

Illustrate this moment:

{first_slide.text}

Show only ONE clear scene.

Avoid multiple actions.

--------------------------------------------------
BACKGROUND
--------------------------------------------------

Rich colourful environment.

Depth.

Flowers.

Trees.

Soft clouds.

Storybook atmosphere.

--------------------------------------------------
MOOD
--------------------------------------------------

Warm

Happy

Comforting

Magical

Hopeful

--------------------------------------------------
LIGHTING
--------------------------------------------------

Golden Hour.

Soft cinematic lighting.

Volumetric light rays.

Gentle shadows.

--------------------------------------------------
COMPOSITION
--------------------------------------------------

Vertical portrait.

4:5 aspect ratio.

Character centred.

Eye contact with viewer.

Large expressive eyes.

Cute proportions.

Leave generous empty space at the top for title overlay.

--------------------------------------------------
QUALITY
--------------------------------------------------

Ultra detailed.

Professional illustration.

High colour harmony.

Pixar-quality rendering.

No text.

No watermark.

No logo.

No border.

No collage.

No extra characters unless described in the story.

No scary expressions.

No horror.

No violence.

Suitable for ages 3-8.
"""

        return prompt.strip()