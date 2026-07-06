class CoverPromptBuilder:

    @staticmethod
    def build(story):

        character = story.character_sheet.main_character
        first_slide = story.slides[0]

        prompt = f"""
Create a premium children's bedtime storybook cover.

STYLE
Pixar-quality 3D illustration.

COMPOSITION
Vertical portrait (4:5).

CHARACTER

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

MOOD

Warm
Magical
Comforting
Bedtime

LIGHTING

Soft golden evening light.

REQUIREMENTS

• Character centered
• Large expressive eyes
• Cute proportions
• Rich colorful environment
• Vibrant colors
• High detail
• Storybook illustration
• Leave empty space at the top for title overlay
• No text
• No watermark
• No logo
"""

        return prompt.strip()