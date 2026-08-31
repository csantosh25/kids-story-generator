"""Applies strict rolling retention to the Content Library: keeps only
the newest DEFAULT_RETENTION_COUNT stories (by created_date, tie-broken
by content_id), deleting every older story's output/<folder>/ directory
and removing its Content Library entry.

Run this AFTER a successful `python run_daily.py` -- see
.github/workflows/daily-story.yml, which only reaches this step if
story generation (and its own Content Library update) already
succeeded. If retention itself fails for any reason, nothing is
deleted (see services/story_retention_service.apply_retention) and this
script exits non-zero, so the workflow's subsequent commit/push step
never runs on a bad state.

This is intentionally a separate script/step from run_daily.py, not
folded into the story pipeline itself -- retention is a persistence/
housekeeping concern, not part of story generation.
"""
from services.content_library_service import ContentLibraryService
from services.story_retention_service import (
    apply_retention,
    validate_post_retention_state,
    DEFAULT_RETENTION_COUNT,
    StoryRetentionError,
)


def main():

    print("=" * 70)
    print(f"Story Retention (keep newest {DEFAULT_RETENTION_COUNT})")
    print("=" * 70)
    print()

    content_library = ContentLibraryService()

    try:
        result = apply_retention(content_library, keep=DEFAULT_RETENTION_COUNT)
    except StoryRetentionError as error:
        print(f"Retention failed -- nothing was deleted: {error}")
        raise SystemExit(1)

    print(f"Retained: {len(result['retained'])} stories")
    print(f"Expired : {len(result['expired'])} stories")
    print()

    for content_id in result["removed_content_ids"]:
        print(f"  removed from Content Library: {content_id}")

    for folder in result["deleted_folders"]:
        print(f"  deleted output folder: {folder}")

    if not result["expired"]:
        print("  (nothing to remove)")

    print()

    try:
        validate_post_retention_state(content_library, keep=DEFAULT_RETENTION_COUNT)
    except StoryRetentionError as error:
        print(f"Post-retention validation failed: {error}")
        raise SystemExit(1)

    print("Retention complete and validated.")


if __name__ == "__main__":
    main()
