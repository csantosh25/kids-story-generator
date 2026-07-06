from agents.story_agent import StoryAgent
from agents.slide_agent import SlideAgent

story = StoryAgent().generate_story(
    "A sleepy bunny searching for his favourite pillow"
)

folder = SlideAgent().create_story_slides(story)

print(folder)