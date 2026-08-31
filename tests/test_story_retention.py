"""Tests for strict rolling story retention (services/story_retention_
service.py, apply_retention.py) and its Reel-workflow impact.

Retention rule, with NO exceptions: keep only the newest
DEFAULT_RETENTION_COUNT (10) Content Library entries by created_date
(tie-broken by content_id); delete every older story's output folder
and remove its library entry -- regardless of reel/instagram/pinterest/
youtube posted-status.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services.content_library_service import ContentLibraryService
from services.reel_service import ReelService
from services.story_retention_service import (
    DEFAULT_RETENTION_COUNT,
    StoryRetentionError,
    apply_retention,
    determine_retained_and_expired,
    validate_post_retention_state,
    _is_safe_story_folder,
)

from tests.test_reel_service_generate import _write_minimal_story_assets


def _story(content_id, created_date, folder, reel=False, ig=False, pin=False, yt=False,
           title=None):
    return {
        "content_id": content_id,
        "created_date": created_date,
        "status": "completed",
        "title": title or f"Story {content_id}",
        "theme": "Test",
        "character": {"name": "Test", "species": "Test"},
        "folder": str(folder),
        "instagram": {"posted": ig, "post_url": ""},
        "pinterest": {"posted": pin, "pin_url": ""},
        "reel": {"generated": reel, "video": ""},
        "youtube": {"posted": yt, "url": ""},
    }


def _make_content_library(tmp_path, initial_stories):
    """A real ContentLibraryService pointed at an isolated temp file --
    ContentLibraryService's own `.file` path is hardcoded at
    construction time, so this overrides it afterward rather than
    touching the real repo's data/content_library.json."""

    service = ContentLibraryService()
    service.file = tmp_path / "content_library.json"
    service.file.write_text(json.dumps(initial_stories), encoding="utf-8")
    return service


def _make_story_folder(output_root: Path, name: str, with_reel_assets=True):
    """A real story output folder under an isolated temp output/ root.
    with_reel_assets=True writes everything ReelService.list_reel_
    eligible_stories() requires (cover + slides), for the Reel-impact
    integration tests; False just makes a marker file, enough to prove
    deletion/retention alone."""

    folder = output_root / name

    if with_reel_assets:
        _write_minimal_story_assets(folder)
    else:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "story.json").write_text("{}", encoding="utf-8")

    return folder


class DetermineRetainedAndExpiredTests(unittest.TestCase):
    """Pure computation -- no files, no library, no deletion."""

    def _stories(self, n, dates=None):
        dates = dates or [f"2026-01-{i+1:02d}" for i in range(n)]
        return [
            _story(f"KS-{i+1:06d}", dates[i], f"output/story{i+1}")
            for i in range(n)
        ]

    def test_zero_stories_nothing_expires(self):
        retained, expired = determine_retained_and_expired([])
        self.assertEqual(retained, [])
        self.assertEqual(expired, [])

    def test_one_to_nine_stories_nothing_expires(self):
        for n in (1, 5, 9):
            with self.subTest(n=n):
                retained, expired = determine_retained_and_expired(self._stories(n))
                self.assertEqual(len(retained), n)
                self.assertEqual(expired, [])

    def test_exactly_ten_stories_nothing_expires(self):
        retained, expired = determine_retained_and_expired(self._stories(10))
        self.assertEqual(len(retained), 10)
        self.assertEqual(expired, [])

    def test_eleven_stories_oldest_one_expires(self):
        stories = self._stories(11)
        retained, expired = determine_retained_and_expired(stories)
        self.assertEqual(len(retained), 10)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["content_id"], "KS-000001")  # oldest date

    def test_twelve_stories_oldest_two_expire(self):
        stories = self._stories(12)
        retained, expired = determine_retained_and_expired(stories)
        self.assertEqual(len(retained), 10)
        self.assertEqual(len(expired), 2)
        self.assertEqual(
            {e["content_id"] for e in expired}, {"KS-000001", "KS-000002"},
        )

    def test_twenty_stories_oldest_ten_expire(self):
        stories = self._stories(20)
        retained, expired = determine_retained_and_expired(stories)
        self.assertEqual(len(retained), 10)
        self.assertEqual(len(expired), 10)
        expired_ids = {e["content_id"] for e in expired}
        retained_ids = {r["content_id"] for r in retained}
        self.assertEqual(expired_ids, {f"KS-{i:06d}" for i in range(1, 11)})
        self.assertEqual(retained_ids, {f"KS-{i:06d}" for i in range(11, 21)})

    def test_ordering_is_by_created_date_not_content_id_alone(self):
        """Deliberately out-of-order: the HIGHER content_id has the
        OLDER date, and vice versa -- retention must follow the date."""

        stories = [
            _story("KS-000099", "2026-01-01", "output/a"),  # high ID, old date
            _story("KS-000001", "2026-08-20", "output/b"),  # low ID, new date
        ]

        retained, expired = determine_retained_and_expired(stories, keep=1)

        self.assertEqual(retained[0]["content_id"], "KS-000001")
        self.assertEqual(expired[0]["content_id"], "KS-000099")

    def test_ties_on_created_date_break_on_content_id(self):

        stories = [
            _story("KS-000005", "2026-08-20", "output/a"),
            _story("KS-000007", "2026-08-20", "output/b"),  # same date, higher ID
        ]

        retained, expired = determine_retained_and_expired(stories, keep=1)

        self.assertEqual(retained[0]["content_id"], "KS-000007")
        self.assertEqual(expired[0]["content_id"], "KS-000005")

    def test_posted_status_never_protects_an_old_story(self):
        """The four posted-status combinations, each on the OLDEST
        story alongside 10 newer unposted ones -- must still expire."""

        for kwargs in [
            {"reel": True}, {"ig": True}, {"pin": True}, {"yt": True},
            {"reel": True, "ig": True, "pin": True, "yt": True},
        ]:
            with self.subTest(kwargs=kwargs):
                stories = [_story("KS-000001", "2026-01-01", "output/old", **kwargs)]
                stories += [
                    _story(f"KS-{i:06d}", f"2026-02-{i:02d}", f"output/s{i}")
                    for i in range(2, 12)
                ]
                retained, expired = determine_retained_and_expired(stories)
                self.assertIn("KS-000001", [e["content_id"] for e in expired])
                self.assertNotIn("KS-000001", [r["content_id"] for r in retained])


class IsSafeStoryFolderTests(unittest.TestCase):
    """Item 18: no deletion outside output/<story-folder>."""

    def test_direct_child_of_output_root_is_safe(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "output"
            root.mkdir()
            child = root / "some_story"
            child.mkdir()
            with patch("services.story_retention_service.OUTPUT_ROOT", root):
                self.assertTrue(_is_safe_story_folder(child))

    def test_output_root_itself_is_not_safe(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "output"
            root.mkdir()
            with patch("services.story_retention_service.OUTPUT_ROOT", root):
                self.assertFalse(_is_safe_story_folder(root))

    def test_nested_subdirectory_is_not_safe(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "output"
            nested = root / "story" / "nested"
            nested.mkdir(parents=True)
            with patch("services.story_retention_service.OUTPUT_ROOT", root):
                self.assertFalse(_is_safe_story_folder(nested))

    def test_path_outside_output_root_is_not_safe(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "output"
            root.mkdir()
            outside = Path(tmp) / "not_output" / "story"
            outside.mkdir(parents=True)
            with patch("services.story_retention_service.OUTPUT_ROOT", root):
                self.assertFalse(_is_safe_story_folder(outside))

    def test_traversal_outside_output_root_is_not_safe(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "output"
            root.mkdir()
            (Path(tmp) / "secret").mkdir()
            traversal = root / ".." / "secret"
            with patch("services.story_retention_service.OUTPUT_ROOT", root):
                self.assertFalse(_is_safe_story_folder(traversal))


class ApplyRetentionTests(unittest.TestCase):

    def test_deleted_folders_are_actually_removed_from_disk(self):

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()

            old_folder = _make_story_folder(output_root, "old", with_reel_assets=False)
            new_folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=False)
                for i in range(1, 11)
            ]

            stories = [_story("KS-000001", "2026-01-01", old_folder)]
            stories += [
                _story(f"KS-{i+1:06d}", f"2026-02-{i+1:02d}", new_folders[i])
                for i in range(10)
            ]

            library = _make_content_library(Path(tmp), stories)

            with patch("services.story_retention_service.OUTPUT_ROOT", output_root):
                result = apply_retention(library)

            self.assertFalse(old_folder.exists())
            self.assertEqual(result["deleted_folders"], [str(old_folder)])

    def test_retained_folders_remain_untouched(self):

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()

            old_folder = _make_story_folder(output_root, "old", with_reel_assets=False)
            new_folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=False)
                for i in range(1, 11)
            ]

            stories = [_story("KS-000001", "2026-01-01", old_folder)]
            stories += [
                _story(f"KS-{i+1:06d}", f"2026-02-{i+1:02d}", new_folders[i])
                for i in range(10)
            ]

            library = _make_content_library(Path(tmp), stories)

            with patch("services.story_retention_service.OUTPUT_ROOT", output_root):
                apply_retention(library)

            for folder in new_folders:
                self.assertTrue(folder.exists())
                self.assertTrue((folder / "story.json").exists())

    def test_expired_entries_removed_from_content_library(self):

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()

            folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=False)
                for i in range(1, 13)
            ]
            stories = [
                _story(f"KS-{i+1:06d}", f"2026-01-{i+1:02d}", folders[i])
                for i in range(12)
            ]

            library = _make_content_library(Path(tmp), stories)

            with patch("services.story_retention_service.OUTPUT_ROOT", output_root):
                result = apply_retention(library)

            remaining_ids = {s["content_id"] for s in library.get_all_stories()}

            self.assertEqual(len(remaining_ids), 10)
            self.assertEqual(set(result["removed_content_ids"]), {"KS-000001", "KS-000002"})
            self.assertNotIn("KS-000001", remaining_ids)
            self.assertNotIn("KS-000002", remaining_ids)

    def test_nothing_deleted_when_ten_or_fewer(self):

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()

            folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=False)
                for i in range(1, 6)
            ]
            stories = [
                _story(f"KS-{i+1:06d}", f"2026-01-{i+1:02d}", folders[i])
                for i in range(5)
            ]

            library = _make_content_library(Path(tmp), stories)

            with patch("services.story_retention_service.OUTPUT_ROOT", output_root):
                result = apply_retention(library)

            self.assertEqual(result["deleted_folders"], [])
            self.assertEqual(result["removed_content_ids"], [])
            self.assertEqual(len(library.get_all_stories()), 5)
            for folder in folders:
                self.assertTrue(folder.exists())

    def test_content_library_read_failure_deletes_nothing(self):
        """Item 17 (content-library-failure analog at the code level):
        if the library can't be read at all, apply_retention must raise
        without touching any file."""

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()
            folder = _make_story_folder(output_root, "s1", with_reel_assets=False)

            library = _make_content_library(
                Path(tmp), [_story("KS-000001", "2026-01-01", folder)],
            )
            library.file.write_text("not valid json{{{", encoding="utf-8")

            with patch("services.story_retention_service.OUTPUT_ROOT", output_root):
                with self.assertRaises(StoryRetentionError):
                    apply_retention(library)

            self.assertTrue(folder.exists())

    def test_unsafe_folder_path_aborts_before_deleting_anything(self):
        """All-or-nothing safety: one expired entry with an unsafe
        folder path must prevent ANY deletion, including other,
        genuinely-safe expired entries."""

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()

            safe_old_folder = _make_story_folder(output_root, "safe_old", with_reel_assets=False)
            new_folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=False)
                for i in range(1, 11)
            ]

            stories = [
                _story("KS-000001", "2026-01-01", safe_old_folder),
                _story("KS-000002", "2026-01-02", Path(tmp) / "outside_output"),
            ]
            stories += [
                _story(f"KS-{i+3:06d}", f"2026-02-{i+1:02d}", new_folders[i])
                for i in range(10)
            ]

            library = _make_content_library(Path(tmp), stories)

            with patch("services.story_retention_service.OUTPUT_ROOT", output_root):
                with self.assertRaises(StoryRetentionError):
                    apply_retention(library)

            self.assertTrue(safe_old_folder.exists())
            self.assertEqual(len(library.get_all_stories()), 12)


class ValidatePostRetentionStateTests(unittest.TestCase):

    def test_valid_state_passes(self):

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()
            folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=False)
                for i in range(3)
            ]
            stories = [
                _story(f"KS-{i+1:06d}", f"2026-01-{i+1:02d}", folders[i])
                for i in range(3)
            ]
            library = _make_content_library(Path(tmp), stories)

            self.assertTrue(validate_post_retention_state(library))

    def test_too_many_entries_fails(self):

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()
            folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=False)
                for i in range(11)
            ]
            stories = [
                _story(f"KS-{i+1:06d}", f"2026-01-{i+1:02d}", folders[i])
                for i in range(11)
            ]
            library = _make_content_library(Path(tmp), stories)

            with self.assertRaises(StoryRetentionError):
                validate_post_retention_state(library)

    def test_missing_folder_for_a_retained_entry_fails(self):

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()
            stories = [_story("KS-000001", "2026-01-01", output_root / "does_not_exist")]
            library = _make_content_library(Path(tmp), stories)

            with self.assertRaises(StoryRetentionError):
                validate_post_retention_state(library)


class ReelImpactAfterRetentionTests(unittest.TestCase):
    """Items 19-20: the existing Reel selection logic (unchanged) still
    behaves correctly once retention has run."""

    def _make_reel_service(self):
        with patch("services.reel_service.ContentLibraryService"), \
             patch("services.reel_service.OpenAITTSService"), \
             patch("services.reel_service.ReelImageService"), \
             patch("services.reel_service.BrandLoader.load", return_value={}):
            return ReelService()

    def test_latest_selection_finds_newest_retained_story_after_retention(self):

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()

            old_folder = _make_story_folder(output_root, "old", with_reel_assets=True)
            newest_folder = _make_story_folder(output_root, "newest", with_reel_assets=True)
            middle_folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=True)
                for i in range(1, 10)
            ]

            stories = [_story("KS-000001", "2026-01-01", old_folder)]
            stories += [
                _story(f"KS-{i+1:06d}", f"2026-02-{i:02d}", middle_folders[i - 1])
                for i in range(1, 10)
            ]
            stories.append(_story("KS-000099", "2026-08-30", newest_folder, title="Newest Story"))

            library = _make_content_library(Path(tmp), stories)

            with patch("services.story_retention_service.OUTPUT_ROOT", output_root):
                apply_retention(library)

            reel_service = self._make_reel_service()
            reel_service.library.get_all_stories.side_effect = library.get_all_stories

            latest = reel_service.get_latest_eligible_story()

            self.assertEqual(latest["content_id"], "KS-000099")
            self.assertEqual(latest["title"], "Newest Story")

    def test_retained_content_id_still_resolvable_specifically(self):

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()
            folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=False)
                for i in range(11)
            ]
            stories = [
                _story(f"KS-{i+1:06d}", f"2026-01-{i+1:02d}", folders[i])
                for i in range(11)
            ]
            library = _make_content_library(Path(tmp), stories)

            with patch("services.story_retention_service.OUTPUT_ROOT", output_root):
                apply_retention(library)

            # KS-000011 (newest) survived retention -- still resolvable.
            self.assertIsNotNone(library.get_story("KS-000011"))

    def test_deleted_content_id_resolves_to_none_after_retention(self):
        """A Content ID that fell outside the newest 10 must correctly
        come back as "not found" -- exactly what generate_reel.py's
        existing run_non_interactive() already checks for and fails
        clearly on."""

        with TemporaryDirectory() as tmp:

            output_root = Path(tmp) / "output"
            output_root.mkdir()
            folders = [
                _make_story_folder(output_root, f"s{i}", with_reel_assets=False)
                for i in range(11)
            ]
            stories = [
                _story(f"KS-{i+1:06d}", f"2026-01-{i+1:02d}", folders[i])
                for i in range(11)
            ]
            library = _make_content_library(Path(tmp), stories)

            with patch("services.story_retention_service.OUTPUT_ROOT", output_root):
                apply_retention(library)

            # KS-000001 (oldest) was expired -- no longer resolvable.
            self.assertIsNone(library.get_story("KS-000001"))


if __name__ == "__main__":
    unittest.main()
