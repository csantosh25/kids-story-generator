"""Tests for services/instagram_publisher.py and publish_instagram.py.

Every Instagram/Cloudinary interaction goes through fakes -- nothing in
this file can reach a real API. FakeGraph models Meta's container
lifecycle (FINISHED -> PUBLISHED) closely enough to exercise duplicate
protection and uncertain-result recovery.
"""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import publish_instagram
from services.content_library_service import ContentLibraryService
from services.instagram_graph_client import InstagramApiError, InstagramUncertainError
from services.instagram_publication_store import (
    InMemoryPublicationStore,
    StateConflictError,
)
from services.instagram_publisher import (
    CLAIMED,
    FAILED,
    PUBLISH_REQUESTED,
    PUBLISHED,
    ConfigurationError,
    InstagramPublisher,
    PublisherConfig,
)

from tests.test_instagram_carousel import make_story_json, write_story_folder


NOW = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)
TODAY = "2026-10-20"


class FakeGraph:

    def __init__(self, account=None):
        self.account = account or {
            "user_id": "1784", "username": "bedtime01fables", "account_type": "MEDIA_CREATOR",
        }
        self.calls = []
        self.status = {}
        self.captions = {}
        self.media = []
        self.publish_calls = []
        self.publish_behaviour = []
        self.carousel_status = "FINISHED"
        self.item_status = "FINISHED"
        self.status_error = None
        self.status_down_after_publish = False
        self.media_down = False
        self._next = 0

    def _id(self, prefix):
        self._next += 1
        return f"{prefix}{self._next}"

    def get_account(self):
        self.calls.append("get_account")
        return self.account

    def get_publishing_limit(self):
        return {"quota_usage": 0, "config": {"quota_total": 100}}

    def create_carousel_item(self, image_url):
        self.calls.append(("item", image_url))
        cid = self._id("item")
        self.status[cid] = self.item_status
        return cid

    def create_carousel(self, children, caption):
        self.calls.append(("carousel", tuple(children)))
        cid = self._id("carousel")
        self.status[cid] = self.carousel_status
        self.captions[cid] = caption
        return cid

    def get_container_status(self, container_id):
        if self.status_error:
            raise self.status_error
        return self.status.get(container_id), None

    def _really_publish(self, creation_id):
        if self.status.get(creation_id) == "PUBLISHED":
            raise AssertionError("container published twice")
        self.status[creation_id] = "PUBLISHED"
        media_id = self._id("media")
        self.media.insert(0, {"id": media_id, "caption": self.captions[creation_id]})
        return media_id

    def publish(self, creation_id):
        self.publish_calls.append(creation_id)
        behaviour = self.publish_behaviour.pop(0) if self.publish_behaviour else "ok"

        if self.status_down_after_publish:
            self.status_error = InstagramUncertainError("GET container: no response")

        if behaviour == "ok":
            return self._really_publish(creation_id)
        if behaviour == "timeout_after_success":
            self._really_publish(creation_id)
            raise InstagramUncertainError("POST media_publish: no response (ReadTimeout)")
        if behaviour == "timeout_before":
            raise InstagramUncertainError("POST media_publish: no response (ConnectTimeout)")
        if behaviour == "reject":
            raise InstagramApiError("HTTP 400 code=9004 media not ready", status=400)
        raise AssertionError(behaviour)

    def list_recent_media(self, limit=25):
        if self.media_down:
            raise InstagramUncertainError("GET media: no response")
        return list(self.media)

    def get_media(self, media_id):
        return {"id": media_id, "permalink": f"https://www.instagram.com/p/{media_id}/"}

    @property
    def published_posts(self):
        return len(self.media)


class FakeHosting:

    def __init__(self):
        self.uploads = []
        self.deleted = []
        self.fail_upload = False

    def public_id_for(self, content_id, index):
        return f"test/{content_id}/{index:02d}"

    def upload(self, path, public_id):
        from services.cloudinary_hosting_service import HostingError
        if self.fail_upload:
            raise HostingError("Cloudinary upload failed: HTTP 401 Invalid Signature")
        self.uploads.append((Path(path).name, public_id))
        return {"public_id": public_id, "url": f"https://res.example/{public_id}.jpg"}

    def wait_until_accessible(self, url):
        return True

    def delete(self, public_id):
        self.deleted.append(public_id)
        return True


class Clock:

    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class PublisherTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.library = ContentLibraryService()
        self.library.file = self.tmp / "content_library.json"
        self.entries = []
        self.store = InMemoryPublicationStore()
        self.graph = FakeGraph()
        self.hosting = FakeHosting()
        self.clock = Clock()
        self.sleeps = []

    def tearDown(self):
        self._tmp.cleanup()

    def add_story(self, content_id, created_date=TODAY, slides=3, title=None):
        title = title or f"Story {content_id}"
        folder = write_story_folder(self.tmp / "output" / content_id, slides=slides,
                                    story=make_story_json(title))
        self.entries.append({
            "content_id": content_id,
            "created_date": created_date,
            "status": "completed",
            "title": title,
            "folder": str(folder),
            "instagram": {"posted": True, "post_url": ""},
        })
        self.library.file.write_text(json.dumps(self.entries), encoding="utf-8")
        return folder

    def publisher(self, live=True, run_id="run-1", graph=None, hosting=None, **overrides):
        config = PublisherConfig(live=live, run_id=run_id, ig_user_id="1784",
                                 poll_interval_seconds=0, poll_timeout_seconds=0, **overrides)
        return InstagramPublisher(
            config, self.store, self.library,
            graph=self.graph if graph is None and live else graph,
            hosting=self.hosting if hosting is None and live else hosting,
            now=self.clock, sleep=self.sleeps.append, log=lambda *_: None,
        )

    def record(self, content_id):
        return self.store.load()[0]["stories"].get(content_id)


# =====================================================================

class SuccessfulPublishTests(PublisherTestCase):

    def test_publishes_cover_and_slides_as_one_carousel_in_order(self):
        self.add_story("KS-000003", slides=3)

        outcomes = self.publisher().run()

        self.assertEqual([o.action for o in outcomes], ["published"])
        self.assertEqual(self.graph.published_posts, 1)
        self.assertEqual(
            [name for name, _ in self.hosting.uploads],
            ["01.jpg", "02.jpg", "03.jpg", "04.jpg"],
        )

        carousel_calls = [c for c in self.graph.calls if c[0] == "carousel"]
        item_calls = [c for c in self.graph.calls if c[0] == "item"]
        self.assertEqual(len(carousel_calls), 1)
        self.assertEqual(
            [url for _, url in item_calls],
            [f"https://res.example/test/KS-000003/{n:02d}.jpg" for n in range(1, 5)],
        )
        self.assertEqual(list(carousel_calls[0][1]), ["item1", "item2", "item3", "item4"])

        record = self.record("KS-000003")
        self.assertEqual(record["status"], PUBLISHED)
        self.assertEqual(record["media_id"], outcomes[0].media_id)
        self.assertTrue(record["permalink"].startswith("https://www.instagram.com/p/"))
        self.assertEqual(record["attempt_count"], 1)
        self.assertEqual(record["source_images"],
                         ["cover_final.png", "slide_1.png", "slide_2.png", "slide_3.png"])
        self.assertIn("published_at", record)
        self.assertEqual(self.store.load()[0]["live_enabled_on"], TODAY)

    def test_hosted_assets_deleted_only_after_publish(self):
        self.add_story("KS-000003")
        self.publisher().run()

        self.assertEqual(sorted(self.hosting.deleted),
                         sorted(pid for _, pid in self.hosting.uploads))
        self.assertIsNotNone(self.record("KS-000003")["hosted_assets_deleted_at"])

    def test_deterministic_order_and_per_run_limit(self):
        self.add_story("KS-000005", created_date=TODAY)
        self.add_story("KS-000004", created_date=TODAY)
        self.add_story("KS-000003", created_date="2026-10-19")
        self.store = InMemoryPublicationStore({"live_enabled_on": "2026-10-01", "stories": {}})

        outcomes = self.publisher(max_posts_per_run=2).run()
        by_id = {o.content_id: o.action for o in outcomes}

        self.assertEqual(by_id, {"KS-000003": "published", "KS-000004": "published",
                                 "KS-000005": "skip-deferred"})
        published_order = [m["caption"].splitlines()[0] for m in reversed(self.graph.media)]
        self.assertEqual(published_order, ["📖 Story KS-000003", "📖 Story KS-000004"])
        self.assertEqual(self.record("KS-000005"), None)


class EligibilityTests(PublisherTestCase):

    def test_historical_stories_never_published_on_enablement(self):
        self.add_story("KS-000001", created_date="2026-10-18")
        self.add_story("KS-000002", created_date="2026-10-19")
        self.add_story("KS-000003", created_date=TODAY)

        outcomes = {o.content_id: o for o in self.publisher(max_story_age_days=5).run()}

        self.assertTrue(outcomes["KS-000001"].action.startswith("skip"))
        self.assertIn("historical", outcomes["KS-000002"].detail)
        self.assertEqual(outcomes["KS-000003"].action, "published")
        self.assertEqual(self.graph.published_posts, 1)

    def test_stories_older_than_window_skipped(self):
        self.store = InMemoryPublicationStore({"live_enabled_on": "2026-01-01", "stories": {}})
        self.add_story("KS-000001", created_date="2026-10-10")

        outcome, = self.publisher().run()

        self.assertIn("older than", outcome.detail)
        self.assertEqual(self.graph.published_posts, 0)

    def test_incomplete_story_assets_skipped(self):
        folder = self.add_story("KS-000003")
        for slide in folder.glob("slide_*.png"):
            slide.unlink()

        outcome, = self.publisher().run()

        self.assertIn("assets incomplete", outcome.detail)
        self.assertEqual(self.hosting.uploads, [])

    def test_content_library_instagram_posted_flag_is_ignored(self):
        """ContentLibraryService.add_story always writes
        instagram.posted=True; it must neither block nor allow
        publishing -- only the publication store decides."""

        self.add_story("KS-000003")
        self.add_story("KS-000004")
        self.entries[0]["instagram"]["posted"] = True
        self.entries[1]["instagram"]["posted"] = False
        self.library.file.write_text(json.dumps(self.entries), encoding="utf-8")

        outcomes = self.publisher().run()

        self.assertEqual([o.action for o in outcomes], ["published", "published"])

        outcomes = self.publisher(run_id="run-2").run()
        self.assertTrue(all("already published" in o.detail for o in outcomes))
        self.assertEqual(self.graph.published_posts, 2)

    def test_unknown_content_id(self):
        self.add_story("KS-000003")
        outcome, = self.publisher().run(content_id="KS-999999")
        self.assertIn("not found", outcome.detail)


class DuplicatePreventionTests(PublisherTestCase):

    def test_already_published_story_is_skipped_on_rerun(self):
        self.add_story("KS-000003")
        self.publisher(run_id="run-1").run()

        for run in ("run-1", "run-2", "run-3"):
            outcome, = self.publisher(run_id=run).run()
            self.assertEqual(outcome.action, "skip")
            self.assertIn("already published", outcome.detail)

        outcome, = self.publisher(run_id="run-4").run(content_id="KS-000003")
        self.assertEqual(outcome.action, "skip")
        self.assertEqual(self.graph.published_posts, 1)
        self.assertEqual(len(self.graph.publish_calls), 1)

    def test_published_record_survives_retention_removing_the_story(self):
        folder = self.add_story("KS-000003")
        self.publisher().run()

        # Retention deletes the folder and library entry, then the same
        # content_id somehow reappears pointing at a new folder.
        self.entries[0]["folder"] = str(self.tmp / "output" / "other")
        write_story_folder(Path(self.entries[0]["folder"]))
        self.library.file.write_text(json.dumps(self.entries), encoding="utf-8")

        outcome, = self.publisher(run_id="run-2").run(content_id="KS-000003")

        self.assertEqual(outcome.action, "skip")
        self.assertEqual(self.graph.published_posts, 1)
        self.assertEqual(self.record("KS-000003")["folder"], str(folder))

    def test_record_for_a_different_folder_blocks_reuse_of_content_id(self):
        self.add_story("KS-000003")
        self.store = InMemoryPublicationStore({"live_enabled_on": TODAY, "stories": {
            "KS-000003": {"status": FAILED, "folder": "output/old_story", "attempt_count": 1},
        }})

        outcome, = self.publisher().run()

        self.assertIn("different story folder", outcome.detail)
        self.assertEqual(self.graph.publish_calls, [])

    def test_concurrent_run_with_active_claim_is_skipped(self):
        self.add_story("KS-000003")
        folder = self.entries[0]["folder"]
        self.store = InMemoryPublicationStore({"live_enabled_on": TODAY, "stories": {
            "KS-000003": {"status": CLAIMED, "folder": folder, "attempt_count": 1,
                          "claimed_by": "run-A",
                          "claimed_at": (NOW - timedelta(minutes=5)).isoformat()},
        }})

        outcome, = self.publisher(run_id="run-B").run()

        self.assertIn("in progress", outcome.detail)
        self.assertEqual(self.hosting.uploads, [])
        self.assertEqual(self.graph.publish_calls, [])

    def test_two_runs_racing_for_the_same_story_publish_once(self):
        """Run B reads the state before run A writes its claim; B's
        compare-and-swap then fails, it re-reads, sees A's claim and
        backs off."""

        self.add_story("KS-000003")
        self.store = InMemoryPublicationStore({"live_enabled_on": TODAY, "stories": {}})
        store = self.store
        publisher_a = self.publisher(run_id="run-A")
        publisher_b = self.publisher(run_id="run-B")

        original_save = store.save
        raced = []

        def save_with_interleaving(state, version, message):
            # The first time B tries to write its claim, let A run fully.
            if "claim" in message and not raced and state["stories"]["KS-000003"]["claimed_by"] == "run-B":
                raced.append(True)
                publisher_a.run()
            return original_save(state, version, message)

        store.save = save_with_interleaving
        outcome_b, = publisher_b.run()

        self.assertTrue(raced)
        self.assertEqual(outcome_b.action, "skip")
        self.assertEqual(self.graph.published_posts, 1)
        self.assertEqual(self.record("KS-000003")["claimed_by"], "run-A")

    def test_never_publishes_if_publish_requested_cannot_be_persisted(self):
        self.add_story("KS-000003")
        store = self.store
        original_save = store.save

        def failing_save(state, version, message):
            if "publish requested" in message:
                raise StateConflictError("always conflicting")
            return original_save(state, version, message)

        store.save = failing_save
        outcome, = self.publisher().run()

        self.assertEqual(outcome.action, "failed")
        self.assertEqual(self.graph.publish_calls, [])


class FailureAndRetryTests(PublisherTestCase):

    def test_container_processing_error_marks_failed_then_retry_succeeds(self):
        self.add_story("KS-000003")
        self.graph.carousel_status = "ERROR"

        outcome, = self.publisher(run_id="run-1").run()

        self.assertEqual(outcome.action, "failed")
        self.assertIn("ERROR", outcome.detail)
        self.assertEqual(self.graph.publish_calls, [])
        record = self.record("KS-000003")
        self.assertEqual(record["status"], FAILED)
        self.assertIn("ERROR", record["last_error"])
        # Failed-story assets are kept for now (retention window).
        self.assertEqual(self.hosting.deleted, [])

        self.graph.carousel_status = "FINISHED"
        outcome, = self.publisher(run_id="run-2").run()

        self.assertEqual(outcome.action, "published")
        self.assertEqual(self.graph.published_posts, 1)
        self.assertEqual(self.record("KS-000003")["attempt_count"], 2)

    def test_item_container_never_ready_times_out(self):
        self.add_story("KS-000003")
        self.graph.item_status = "IN_PROGRESS"

        outcome, = self.publisher().run()

        self.assertEqual(outcome.action, "failed")
        self.assertIn("not ready", outcome.detail)
        self.assertEqual(self.graph.publish_calls, [])

    def test_definitive_publish_rejection_is_retryable(self):
        self.add_story("KS-000003")
        self.graph.publish_behaviour = ["reject"]

        outcome, = self.publisher(run_id="run-1").run()
        self.assertEqual(outcome.action, "failed")
        self.assertEqual(self.record("KS-000003")["status"], FAILED)

        outcome, = self.publisher(run_id="run-2").run()
        self.assertEqual(outcome.action, "published")
        self.assertEqual(self.graph.published_posts, 1)

    def test_hosting_failure_marks_failed_without_instagram_calls(self):
        self.add_story("KS-000003")
        self.hosting.fail_upload = True

        outcome, = self.publisher().run()

        self.assertEqual(outcome.action, "failed")
        self.assertIn("Cloudinary", outcome.detail)
        self.assertFalse([c for c in self.graph.calls if c != "get_account"])

    def test_max_attempts_stops_automatic_retries(self):
        self.add_story("KS-000003")
        self.graph.carousel_status = "ERROR"

        for run in range(1, 5):
            self.publisher(run_id=f"run-{run}", max_attempts=3).run()

        record = self.record("KS-000003")
        self.assertEqual(record["attempt_count"], 3)
        self.assertEqual(record["status"], FAILED)

    def test_stale_failed_hosted_assets_are_cleaned_up_later(self):
        self.add_story("KS-000003")
        self.graph.carousel_status = "ERROR"
        self.publisher(run_id="run-1", max_attempts=1).run()
        self.assertEqual(self.hosting.deleted, [])

        self.clock.now = NOW + timedelta(hours=49)
        self.publisher(run_id="run-2", max_attempts=1).run()

        self.assertEqual(len(self.hosting.deleted), 4)

    def test_interrupted_run_with_expired_claim_is_retried(self):
        self.add_story("KS-000003")
        folder = self.entries[0]["folder"]
        self.store = InMemoryPublicationStore({"live_enabled_on": TODAY, "stories": {
            "KS-000003": {"status": CLAIMED, "folder": folder, "attempt_count": 1,
                          "claimed_by": "crashed-run",
                          "claimed_at": (NOW - timedelta(hours=3)).isoformat()},
        }})

        outcome, = self.publisher(run_id="run-2").run()

        self.assertEqual(outcome.action, "published")
        self.assertEqual(self.graph.published_posts, 1)


class UncertainPublishRecoveryTests(PublisherTestCase):

    def test_timeout_after_successful_publish_is_recorded_not_retried(self):
        self.add_story("KS-000003")
        self.graph.publish_behaviour = ["timeout_after_success"]

        outcome, = self.publisher().run()

        self.assertEqual(outcome.action, "published")
        self.assertEqual(len(self.graph.publish_calls), 1)
        self.assertEqual(self.graph.published_posts, 1)
        record = self.record("KS-000003")
        self.assertEqual(record["status"], PUBLISHED)
        self.assertEqual(record["media_id"], self.graph.media[0]["id"])
        self.assertTrue(any(h["event"] == "published" and "container PUBLISHED" in h["detail"]
                            for h in record["history"]))

    def test_timeout_before_publish_publishes_existing_container_once(self):
        self.add_story("KS-000003")
        self.graph.publish_behaviour = ["timeout_before"]

        outcome, = self.publisher(run_id="run-1").run()

        # Container still FINISHED -> provably not published -> the SAME
        # single-use container is published once more.
        self.assertEqual(outcome.action, "published")
        self.assertEqual(self.graph.published_posts, 1)
        self.assertEqual(len(self.graph.publish_calls), 2)
        self.assertEqual(len(set(self.graph.publish_calls)), 1)

    def test_unresolvable_status_never_blindly_retries(self):
        self.add_story("KS-000003")
        self.graph.publish_behaviour = ["timeout_before"]
        self.graph.status_down_after_publish = True

        outcome, = self.publisher(run_id="run-1").run()

        self.assertEqual(outcome.action, "uncertain")
        self.assertEqual(self.record("KS-000003")["status"], PUBLISH_REQUESTED)
        self.assertEqual(len(self.graph.publish_calls), 1)

        # Later runs while status is still unknown: still no publish call.
        for hours in (2, 4):
            self.clock.now = NOW + timedelta(hours=hours)
            outcome, = self.publisher(run_id=f"run-{hours}").run()
            self.assertEqual(outcome.action, "uncertain")
        self.assertEqual(len(self.graph.publish_calls), 1)

        # Status becomes readable and shows the earlier call DID publish.
        self.graph.status_down_after_publish = False
        self.graph.status_error = None
        self.graph._really_publish(self.graph.publish_calls[0])
        self.clock.now = NOW + timedelta(hours=6)
        outcome, = self.publisher(run_id="run-6").run()

        self.assertEqual(outcome.action, "published")
        self.assertEqual(self.graph.published_posts, 1)
        self.assertEqual(len(self.graph.publish_calls), 1)

    def test_recovery_from_abandoned_publish_requested_record(self):
        """A run crashed right after persisting publish_requested and
        calling media_publish (which succeeded)."""

        self.add_story("KS-000003")
        self.graph.publish_behaviour = ["timeout_after_success"]
        self.graph.status_down_after_publish = True
        self.graph.media_down = True

        self.publisher(run_id="run-1").run()
        self.assertEqual(self.record("KS-000003")["status"], PUBLISH_REQUESTED)

        self.graph.status_down_after_publish = False
        self.graph.status_error = None
        self.graph.media_down = False
        self.clock.now = NOW + timedelta(hours=2)
        outcome, = self.publisher(run_id="run-2").run()

        self.assertEqual(outcome.action, "published")
        self.assertEqual(len(self.graph.publish_calls), 1)

    def test_expired_container_without_post_is_retried_fresh(self):
        self.add_story("KS-000003")
        self.graph.publish_behaviour = ["timeout_before"]
        self.graph.status_down_after_publish = True
        self.publisher(run_id="run-1").run()
        first_container = self.graph.publish_calls[0]

        self.graph.status_down_after_publish = False
        self.graph.status_error = None
        self.graph.status[first_container] = "EXPIRED"
        self.clock.now = NOW + timedelta(hours=25)
        outcome, = self.publisher(run_id="run-2").run(content_id="KS-000003")

        self.assertEqual(outcome.action, "published")
        self.assertEqual(self.graph.published_posts, 1)
        self.assertNotEqual(self.graph.publish_calls[-1], first_container)


class AccountValidationTests(PublisherTestCase):

    def test_wrong_username_rejected_before_anything_happens(self):
        self.add_story("KS-000003")
        self.graph.account["username"] = "someone_else"

        with self.assertRaisesRegex(ConfigurationError, "expected @bedtime01fables"):
            self.publisher().run()

        self.assertEqual(self.store.writes, 0)
        self.assertEqual(self.hosting.uploads, [])

    def test_personal_account_rejected(self):
        self.add_story("KS-000003")
        self.graph.account["account_type"] = "PERSONAL"

        with self.assertRaisesRegex(ConfigurationError, "Business or Creator"):
            self.publisher().run()

    def test_mismatched_user_id_rejected(self):
        self.add_story("KS-000003")
        self.graph.account["user_id"] = "999"

        with self.assertRaisesRegex(ConfigurationError, "INSTAGRAM_USER_ID"):
            self.publisher().run()

    def test_invalid_token_rejected(self):
        self.add_story("KS-000003")

        def bad_token():
            raise InstagramApiError("GET /v26.0/me: HTTP 400 code=190 Invalid OAuth access token")

        self.graph.get_account = bad_token

        with self.assertRaisesRegex(ConfigurationError, "access token"):
            self.publisher().run()


class DryRunTests(PublisherTestCase):

    def test_dry_run_makes_no_network_calls_and_writes_nothing(self):
        self.add_story("KS-000003", slides=5)

        class Exploding:
            def __getattr__(self, name):
                raise AssertionError(f"dry run touched {name}")

        publisher = self.publisher(live=False, graph=Exploding(), hosting=Exploding())
        outcome, = publisher.run()

        self.assertEqual(outcome.action, "dry-run")
        self.assertEqual(len(outcome.images), 6)
        self.assertTrue(outcome.images[0].startswith("cover_final.png -> 432x540 JPEG"))
        self.assertTrue(outcome.images[-1].startswith("slide_5.png"))
        self.assertIn("🌟 Lesson:", outcome.caption)
        self.assertEqual(self.store.writes, 0)
        self.assertIsNone(self.store.load()[0]["live_enabled_on"])

    def test_dry_run_still_honours_publication_history(self):
        self.add_story("KS-000003")
        self.store = InMemoryPublicationStore({"live_enabled_on": TODAY, "stories": {
            "KS-000003": {"status": PUBLISHED, "media_id": "m1",
                          "folder": self.entries[0]["folder"]}}})

        outcome, = self.publisher(live=False).run()

        self.assertIn("already published", outcome.detail)


class CliTests(unittest.TestCase):

    BASE_ENV = {"INSTAGRAM_AUTO_PUBLISH_ENABLED": "", "GITHUB_STEP_SUMMARY": ""}

    def test_live_requires_enable_flag(self):
        with patch.dict(os.environ, {**self.BASE_ENV, "INSTAGRAM_AUTO_PUBLISH_ENABLED": "false"}):
            config, reason = publish_instagram.build_config(live_requested=True)
        self.assertFalse(config.live)
        self.assertIn("INSTAGRAM_AUTO_PUBLISH_ENABLED", reason)

        with patch.dict(os.environ, {**self.BASE_ENV, "INSTAGRAM_AUTO_PUBLISH_ENABLED": "true"}):
            self.assertTrue(publish_instagram.build_config(live_requested=True)[0].live)
            self.assertFalse(publish_instagram.build_config(live_requested=False)[0].live)

    def test_flag_defaults_to_disabled(self):
        env = {k: v for k, v in os.environ.items() if k != "INSTAGRAM_AUTO_PUBLISH_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(publish_instagram.build_config(live_requested=True)[0].live)

    def test_missing_credentials_fail_before_any_work_and_only_names_are_printed(self):
        env = {**self.BASE_ENV, "INSTAGRAM_AUTO_PUBLISH_ENABLED": "true",
               "INSTAGRAM_ACCESS_TOKEN": "SECRET-TOKEN-VALUE", "INSTAGRAM_USER_ID": "",
               "CLOUDINARY_CLOUD_NAME": "", "CLOUDINARY_API_KEY": "", "CLOUDINARY_API_SECRET": ""}

        with patch.dict(os.environ, env), \
                patch.object(publish_instagram, "GitBranchPublicationStore") as store, \
                patch.object(publish_instagram, "InstagramPublisher") as publisher:
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as exit_info:
                publish_instagram.main(["--live"])

        self.assertEqual(exit_info.exception.code, 1)
        self.assertIn("INSTAGRAM_USER_ID", output.getvalue())
        self.assertIn("CLOUDINARY_API_SECRET", output.getvalue())
        self.assertNotIn("SECRET-TOKEN-VALUE", output.getvalue())
        store.assert_not_called()
        publisher.assert_not_called()

    def test_dry_run_cli_needs_no_credentials(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("INSTAGRAM_", "CLOUDINARY_"))}
        env["GITHUB_STEP_SUMMARY"] = ""

        with patch.dict(os.environ, env, clear=True), \
                patch.object(publish_instagram, "ContentLibraryService") as library_cls, \
                patch("services.instagram_graph_client.InstagramGraphClient") as graph_cls, \
                patch("services.cloudinary_hosting_service.CloudinaryHostingService") as host_cls:
            library_cls.return_value.get_all_stories.return_value = []
            with redirect_stdout(io.StringIO()):
                publish_instagram.main(["--no-state"])

        graph_cls.assert_not_called()
        host_cls.assert_not_called()


class GraphClientRedactionTests(unittest.TestCase):

    def test_token_never_appears_in_errors(self):
        import requests
        from services.instagram_graph_client import InstagramGraphClient

        class Response:
            status_code = 400
            def json(self):
                return {"error": {"code": 190, "message": "bad token SECRET123"}}

        class Session:
            def get(self, url, params=None, timeout=None):
                return Response()
            def post(self, url, data=None, timeout=None):
                raise requests.ReadTimeout(f"timed out {url}?access_token=SECRET123")

        client = InstagramGraphClient("SECRET123", "1784", session=Session())

        with self.assertRaises(InstagramApiError) as api_error:
            client.get_account()
        self.assertNotIn("SECRET123", str(api_error.exception))
        self.assertEqual(api_error.exception.code, 190)

        with self.assertRaises(InstagramUncertainError) as uncertain:
            client.publish("c1")
        self.assertNotIn("SECRET123", str(uncertain.exception))

    def test_api_version_is_configurable(self):
        from services.instagram_graph_client import DEFAULT_API_VERSION, InstagramGraphClient

        seen = []

        class Response:
            status_code = 200
            def json(self):
                return {"user_id": "1"}

        class Session:
            def get(self, url, params=None, timeout=None):
                seen.append(url)
                return Response()

        InstagramGraphClient("t", "1", session=Session()).get_account()
        InstagramGraphClient("t", "1", api_version="v99.0", session=Session()).get_account()

        self.assertEqual(seen, [f"https://graph.instagram.com/{DEFAULT_API_VERSION}/me",
                                "https://graph.instagram.com/v99.0/me"])


if __name__ == "__main__":
    unittest.main()


class CloudinaryHostingTests(unittest.TestCase):

    def test_signature_matches_cloudinary_documented_example(self):
        from services.cloudinary_hosting_service import CloudinaryHostingService

        hosting = CloudinaryHostingService("demo", "key", "abcd")
        self.assertEqual(
            hosting._sign({"public_id": "sample_image", "timestamp": 1315060510}),
            "b4ad47fb4e25c7bf5f92a20089f9db59bc302313",
        )

    def test_waits_until_url_serves_jpeg_then_gives_up_cleanly(self):
        from services.cloudinary_hosting_service import CloudinaryHostingService, HostingError

        class Response:
            def __init__(self, code, kind):
                self.status_code, self.headers = code, {"Content-Type": kind}

        class Session:
            def __init__(self, responses):
                self.responses = responses
            def head(self, url, timeout=None, allow_redirects=None):
                return self.responses.pop(0)

        sleeps = []
        ok = CloudinaryHostingService("c", "k", "s", sleep=sleeps.append, session=Session(
            [Response(404, "text/html"), Response(200, "image/jpeg")]))
        self.assertTrue(ok.wait_until_accessible("https://x/1.jpg"))
        self.assertEqual(len(sleeps), 1)

        never = CloudinaryHostingService("c", "k", "s", sleep=lambda _: None, session=Session(
            [Response(404, "text/html")] * 3))
        with self.assertRaises(HostingError):
            never.wait_until_accessible("https://x/1.jpg", attempts=3)

    def test_deterministic_public_ids_and_secret_not_sent(self):
        from services.cloudinary_hosting_service import CloudinaryHostingService

        sent = []

        class Response:
            status_code = 200
            def json(self):
                return {"result": "not found"}

        class Session:
            def post(self, url, data=None, timeout=None):
                sent.append(data)
                return Response()

        hosting = CloudinaryHostingService("c", "k", "TOPSECRET", session=Session())
        self.assertEqual(hosting.public_id_for("KS-000003", 2),
                         "bedtime01fables/instagram-carousel/KS-000003/02")
        self.assertTrue(hosting.delete("x"))   # "not found" counts as deleted
        self.assertNotIn("TOPSECRET", str(sent))
