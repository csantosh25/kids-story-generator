from services.content_library_service import ContentLibraryService

print("=" * 70)
print("🎬 Kids Story Reel Generator")
print("=" * 70)

library = ContentLibraryService()

stories = library.get_all_stories()

if not stories:
    print("No stories found.")
    raise SystemExit()

print()

for index, story in enumerate(stories, start=1):

    print(
        f"{index}. "
        f"{story['title']} "
        f"({story['content_id']})"
    )

print()

choice = int(input("Select story number: "))

selected = stories[choice - 1]

print()

print("Selected Story")
print("-----------------------")
print(selected["title"])
print(selected["folder"])

print()

print("Next step:")
print("Generate narration")