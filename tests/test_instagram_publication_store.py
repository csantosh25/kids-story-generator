"""Tests for services/instagram_publication_store.py against REAL git
repositories (a local bare "origin" plus independent clones standing in
for separate GitHub Actions runners), and for the publication history's
independence from the 10-story retention rule."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services import story_retention_service
from services.content_library_service import ContentLibraryService
from services.instagram_publication_store import (
    DEFAULT_BRANCH,
    DEFAULT_PATH,
    GitBranchPublicationStore,
    InMemoryPublicationStore,
    SkipUpdate,
    StateConflictError,
    StateStoreError,
)
from services.instagram_publisher import PUBLISHED
from services.story_retention_service import DEFAULT_RETENTION_COUNT, apply_retention

from tests.test_instagram_publisher import NOW, Clock, FakeGraph, FakeHosting, TODAY
from services.instagram_publisher import InstagramPublisher, PublisherConfig
from tests.test_instagram_carousel import make_story_json, write_story_folder


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@unittest.skipUnless(shutil.which("git"), "git is required")
class GitBranchStoreTests(unittest.TestCase):

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.origin = root / "origin.git"
        git(root, "init", "--bare", "-q", str(self.origin))

        # "main" exists on origin, like the real repository.
        seed = root / "seed"
        git(root, "clone", "-q", str(self.origin), str(seed))
        (seed / "README.md").write_text("main\n", encoding="utf-8")
        git(seed, "add", "README.md")
        git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
        git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
        self.main_head = git(seed, "rev-parse", "HEAD").stdout.strip()

        self.runner_a = root / "runner_a"
        self.runner_b = root / "runner_b"
        git(root, "clone", "-q", "--depth=1", self.origin.as_uri(), str(self.runner_a))
        git(root, "clone", "-q", "--depth=1", self.origin.as_uri(), str(self.runner_b))

    def tearDown(self):
        self._tmp.cleanup()

    def store(self, runner):
        return GitBranchPublicationStore(repo_dir=runner)

    def test_missing_branch_loads_empty_state(self):
        state, version = self.store(self.runner_a).load()
        self.assertEqual(state["stories"], {})
        self.assertIsNone(version)

    def test_state_persists_across_independent_runners(self):
        def mutate(state):
            state["stories"]["KS-000003"] = {"status": PUBLISHED, "media_id": "m1"}

        _, written = self.store(self.runner_a).update(mutate, "test write")
        self.assertTrue(written)

        state, _ = self.store(self.runner_b).load()
        self.assertEqual(state["stories"]["KS-000003"]["media_id"], "m1")

        # main is untouched and the main checkout's working tree is clean.
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/main").stdout.strip(), self.main_head)
        self.assertEqual(git(self.runner_a, "status", "--porcelain").stdout, "")
        files = git(self.origin, "ls-tree", "--name-only", DEFAULT_BRANCH).stdout.split()
        self.assertEqual(sorted(files), sorted(["README.md", DEFAULT_PATH]))

    def test_stale_writer_is_rejected_compare_and_swap(self):
        a, b = self.store(self.runner_a), self.store(self.runner_b)

        state_a, version_a = a.load()
        state_b, version_b = b.load()

        state_a["stories"]["KS-1"] = {"status": "claimed", "claimed_by": "A"}
        a.save(state_a, version_a, "A claims")

        state_b["stories"]["KS-1"] = {"status": "claimed", "claimed_by": "B"}
        with self.assertRaises(StateConflictError):
            b.save(state_b, version_b, "B claims")

        self.assertEqual(b.load()[0]["stories"]["KS-1"]["claimed_by"], "A")

        # Same for a stale writer once the branch already has history.
        state_a, version_a = a.load()
        state_b, version_b = b.load()
        a.save(state_a, version_a, "A again")
        with self.assertRaises(StateConflictError):
            b.save(state_b, version_b, "B stale")

    def test_update_rereads_and_rechecks_preconditions_after_conflict(self):
        a, b = self.store(self.runner_a), self.store(self.runner_b)
        attempts = []

        def claim_for(run):
            def mutate(state):
                attempts.append(run)
                record = state["stories"].get("KS-1")
                if record:
                    raise SkipUpdate()
                state["stories"]["KS-1"] = {"claimed_by": run}
                if run == "B" and attempts.count("B") == 1:
                    # A sneaks in between B's read and B's push.
                    a.update(claim_for("A"), "A claims")
            return mutate

        _, written = b.update(claim_for("B"), "B claims")

        self.assertFalse(written)
        self.assertEqual(attempts.count("B"), 2)
        self.assertEqual(a.load()[0]["stories"]["KS-1"]["claimed_by"], "A")

    def test_corrupt_state_is_an_error_not_an_empty_history(self):
        seed = Path(self._tmp.name) / "corrupt"
        git(Path(self._tmp.name), "clone", "-q", str(self.origin), str(seed))
        git(seed, "checkout", "-q", "--orphan", DEFAULT_BRANCH)
        git(seed, "rm", "-r", "-f", "--quiet", "--ignore-unmatch", ".")
        (seed / DEFAULT_PATH).write_text("{not json", encoding="utf-8")
        git(seed, "add", DEFAULT_PATH)
        git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "bad")
        git(seed, "push", "-q", "origin", f"HEAD:refs/heads/{DEFAULT_BRANCH}")

        with self.assertRaises(StateStoreError):
            self.store(self.runner_a).load()

    def test_end_to_end_publish_then_rerun_from_another_runner(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            folder = write_story_folder(tmp / "output" / "s", story=make_story_json("Story X"))
            library = ContentLibraryService()
            library.file = tmp / "content_library.json"
            library.file.write_text(json.dumps([{
                "content_id": "KS-000003", "created_date": TODAY, "title": "Story X",
                "folder": str(folder),
            }]), encoding="utf-8")

            graph, hosting = FakeGraph(), FakeHosting()

            def publisher(runner, run_id):
                return InstagramPublisher(
                    PublisherConfig(live=True, run_id=run_id, ig_user_id="1784",
                                    poll_interval_seconds=0, poll_timeout_seconds=0),
                    self.store(runner), library, graph, hosting,
                    now=Clock(NOW), sleep=lambda _: None, log=lambda *_: None,
                )

            first, = publisher(self.runner_a, "100.1").run()
            rerun, = publisher(self.runner_b, "100.2").run()

            self.assertEqual(first.action, "published")
            self.assertEqual(rerun.action, "skip")
            self.assertEqual(graph.published_posts, 1)


class RetentionIndependenceTests(unittest.TestCase):
    """The existing strict 10-story retention is unchanged and cannot
    erase publication history."""

    def test_retention_rule_unchanged(self):
        self.assertEqual(DEFAULT_RETENTION_COUNT, 10)

        stories = [
            {"content_id": f"KS-{n:06d}", "created_date": f"2026-10-{n:02d}",
             "instagram": {"posted": n % 2 == 0}}
            for n in range(1, 13)
        ]
        retained, expired = story_retention_service.determine_retained_and_expired(stories)

        self.assertEqual(len(retained), 10)
        self.assertEqual([s["content_id"] for s in expired], ["KS-000002", "KS-000001"])

    def test_retention_removes_story_but_publication_record_remains(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            output_root = tmp / "output"
            entries = []

            for n in range(1, 12):
                folder = output_root / f"story_{n}"
                folder.mkdir(parents=True)
                (folder / "story.json").write_text("{}", encoding="utf-8")
                entries.append({"content_id": f"KS-{n:06d}", "created_date": f"2026-10-{n:02d}",
                                "folder": str(folder)})

            library = ContentLibraryService()
            library.file = tmp / "content_library.json"
            library.file.write_text(json.dumps(entries), encoding="utf-8")

            store = InMemoryPublicationStore({"live_enabled_on": "2026-10-01", "stories": {
                "KS-000001": {"status": PUBLISHED, "media_id": "m1", "folder": entries[0]["folder"]},
            }})

            with patch.object(story_retention_service, "OUTPUT_ROOT", output_root):
                result = apply_retention(library, keep=10)

            self.assertEqual(result["removed_content_ids"], ["KS-000001"])
            self.assertFalse(Path(entries[0]["folder"]).exists())
            self.assertEqual(store.load()[0]["stories"]["KS-000001"]["status"], PUBLISHED)
            self.assertEqual(store.writes, 0)


if __name__ == "__main__":
    unittest.main()
