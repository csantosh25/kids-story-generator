from pipelines.story_pipeline import StoryPipeline
from services.theme_service import ThemeService

print("=" * 70)
print("📚 Kids Story Generator")
print("=" * 70)

# Get today's scheduled story context
theme_service = ThemeService()
story_context = theme_service.get_story_context()

print(f"\n📅 Day           : {story_context['day']}")
print(f"🎯 Category      : {story_context['category']}")
print(f"📖 Theme         : {story_context['topic']}")
print(f"🧠 Learning Goal : {story_context['learning_goal']}")
print(f"🎨 Story Style   : {story_context['story_style']}")
print(f"🐻 Character     : {story_context['character']['name']}")
print()

pipeline = StoryPipeline()

# The pipeline now uses the story context internally
result = pipeline.run()

print("\n")
print("=" * 70)
print("✅ Story Generated Successfully")
print("=" * 70)

print(f"Title : {result.story.story_info.title}")
print(f"Theme : {result.story.story_info.theme}")
print(f"Folder: {result.output_folder}")