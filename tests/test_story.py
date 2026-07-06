from agents.story_agent import StoryAgent

story = StoryAgent().generate_story(
    "A sleepy bunny looking for his favourite pillow"
)

print()

print(story.story_info.title)

print()

print(story.story_info.subtitle)

print()

print(story.character_sheet.main_character.name)

print()

print(story.cover.prompt)

print()

print(story.instagram.caption)

print()

print(story.email.subject)

print()

print(story.slides[0].text)