import json

from agents.story_agent import StoryAgent
from agents.story_reviewer import StoryReviewer

story = StoryAgent().generate_story("Helping Friends")

review = StoryReviewer().review(
    json.dumps(story.model_dump(), indent=2)
)

print(review)