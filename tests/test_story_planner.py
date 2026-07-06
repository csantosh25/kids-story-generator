from agents.story_planner import StoryPlanner
from services.character_service import CharacterService

planner = StoryPlanner()

character = CharacterService().random_character()

outline = planner.create_outline(
    "Learning to Share",
    character,
)

print(outline)