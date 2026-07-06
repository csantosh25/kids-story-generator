from pipelines.story_pipeline import StoryPipeline
from services.theme_service import ThemeService

print("=" * 70)
print("📚 Kids Story Generator")
print("=" * 70)

theme = ThemeService().get_theme()

print(f"\nToday's Theme: {theme}\n")

pipeline = StoryPipeline()

result = pipeline.run(theme)

print("\n")
print("=" * 70)
print("✅ Story Generated Successfully")
print("=" * 70)

print(f"Title : {result.story.story_info.title}")
print(f"Theme : {result.story.story_info.theme}")
print(f"Folder: {result.output_folder}")