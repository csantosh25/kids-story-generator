"""Strict rolling retention for generated stories.

The Daily Story workflow runs on an ephemeral GitHub Actions runner --
whatever it generates only persists if the workflow commits it back to
the repository (see .github/workflows/daily-story.yml). To keep that
sustainable, ONLY the newest DEFAULT_RETENTION_COUNT stories (by
data/content_library.json's own `created_date`) are kept: every older
story's output/<folder>/ directory is deleted and its Content Library
entry is removed.

STRICT, NO EXCEPTIONS: a story's reel/instagram/pinterest/youtube
posted-status never protects it from expiring. Retention is ordering-
only, exactly as specified -- "latest 10 = keep, everything older =
delete".

data/content_library.json (via ContentLibraryService) is the source of
truth for both the stories to consider AND their ordering -- this
module never scans output/ and sorts folder names.
"""
import shutil
from datetime import datetime
from pathlib import Path

from services.content_library_service import ContentLibraryService

DEFAULT_RETENTION_COUNT = 10

# A story's output folder must be a direct child of this directory --
# see _is_safe_story_folder(). Defined as a module-level constant (not
# hardcoded inline) so tests can point it at an isolated temp directory
# instead of the real repo's output/, matching this project's existing
# pattern for MUSIC_DIR in services/reel_service.py.
OUTPUT_ROOT = Path("output")


class StoryRetentionError(RuntimeError):
    pass


def _sort_key(entry):
    """Newest-first ordering key: (created_date, content_id).

    created_date is date-only precision ("%Y-%m-%d" -- see
    ContentLibraryService.add_story), so multiple stories generated on
    the same calendar day are a real possibility, not just a
    theoretical edge case -- the Daily workflow could run more than
    once in a day (a manual workflow_dispatch alongside the scheduled
    run, for example). content_id is the deterministic tiebreaker:
    ContentLibraryService.next_content_id() assigns these strictly
    increasing at creation time, so a higher content_id was always
    created later, same day or not. A missing/malformed created_date
    sorts as the oldest possible date, so a bad entry is treated as
    dispensable rather than accidentally protected."""

    try:
        created = datetime.strptime(entry.get("created_date", ""), "%Y-%m-%d")
    except (TypeError, ValueError):
        created = datetime.min

    return (created, entry.get("content_id", ""))


def determine_retained_and_expired(stories, keep=DEFAULT_RETENTION_COUNT):
    """Splits `stories` (Content Library entries) into (retained,
    expired): retained is the `keep` newest by _sort_key, expired is
    everything else. Purely a computation -- touches no files, reads no
    library, deletes nothing. STRICT: no entry is exempted from
    expiring regardless of its reel/instagram/pinterest/youtube status
    -- ordering is the only criterion."""

    ordered = sorted(stories, key=_sort_key, reverse=True)

    retained = ordered[:keep]
    expired = ordered[keep:]

    return retained, expired


def _is_safe_story_folder(folder: Path) -> bool:
    """True only if `folder` resolves to a direct child of OUTPUT_ROOT
    -- never OUTPUT_ROOT itself, never something reached by path
    traversal outside it, and never a directory nested more than one
    level deep (which would mean treating some story's SUBdirectory as
    if it were a whole story folder). This is the hard safety boundary
    that keeps retention from ever deleting "arbitrary directories
    under output/" or anything outside it."""

    output_root = OUTPUT_ROOT.resolve()

    try:
        resolved = folder.resolve()
    except OSError:
        return False

    if resolved == output_root:
        return False

    try:
        resolved.relative_to(output_root)
    except ValueError:
        return False

    return resolved.parent == output_root


def apply_retention(content_library=None, keep=DEFAULT_RETENTION_COUNT):
    """Deletes every expired story's output folder and removes its
    Content Library entry, keeping only the newest `keep` stories.
    STRICT: reel/instagram/pinterest/youtube posted-status never
    protects an expired story.

    Safety ordering: the full expired set is computed and every one of
    its folder paths is validated as safe to delete BEFORE any deletion
    happens (all-or-nothing) -- so a bad/malformed entry aborts the
    whole operation with nothing deleted, rather than deleting some
    stories and then failing partway through.

    Returns a dict: {"retained": [...], "expired": [...],
    "deleted_folders": [str, ...], "removed_content_ids": [str, ...]}.

    Raises StoryRetentionError -- without deleting or removing
    anything -- if the Content Library can't be read, or if any expired
    entry's folder path isn't safe to delete."""

    content_library = content_library or ContentLibraryService()

    try:
        stories = content_library.get_all_stories()
    except Exception as error:
        raise StoryRetentionError(
            f"Could not read the Content Library: {error}"
        ) from error

    retained, expired = determine_retained_and_expired(stories, keep=keep)

    if not expired:
        return {
            "retained": retained,
            "expired": [],
            "deleted_folders": [],
            "removed_content_ids": [],
        }

    for entry in expired:

        folder_value = entry.get("folder", "")

        if not folder_value:
            continue

        if not _is_safe_story_folder(Path(folder_value)):
            raise StoryRetentionError(
                f"Refusing to apply retention: expired story "
                f"{entry.get('content_id')!r} has an unsafe folder path "
                f"{folder_value!r} (not a direct child of {OUTPUT_ROOT}). "
                f"Nothing was deleted."
            )

    deleted_folders = []

    for entry in expired:

        folder_value = entry.get("folder", "")

        if not folder_value:
            continue

        folder = Path(folder_value)

        if folder.exists():
            shutil.rmtree(folder)
            deleted_folders.append(str(folder))

    removed_content_ids = [entry["content_id"] for entry in expired]

    content_library.remove_stories(removed_content_ids)

    return {
        "retained": retained,
        "expired": expired,
        "deleted_folders": deleted_folders,
        "removed_content_ids": removed_content_ids,
    }


def validate_post_retention_state(content_library=None, keep=DEFAULT_RETENTION_COUNT):
    """Post-cleanup sanity check, run AFTER apply_retention(): re-reads
    the Content Library fresh and confirms the resulting state is
    actually consistent -- at most `keep` entries remain, and every
    remaining entry's output folder still exists on disk. Deletion has
    already happened by the time this runs, so this can't undo
    anything -- its purpose is to make a broken result LOUD (so a
    calling script/workflow can stop before committing/pushing it)
    rather than silent.

    Raises StoryRetentionError if anything looks wrong. Returns True
    otherwise."""

    content_library = content_library or ContentLibraryService()

    stories = content_library.get_all_stories()

    if len(stories) > keep:
        raise StoryRetentionError(
            f"Post-retention validation failed: {len(stories)} "
            f"Content Library entries remain, expected at most {keep}."
        )

    missing_folders = [
        entry.get("content_id")
        for entry in stories
        if not Path(entry.get("folder", "")).exists()
    ]

    if missing_folders:
        raise StoryRetentionError(
            f"Post-retention validation failed: retained entries with "
            f"missing output folders: {missing_folders}."
        )

    return True
