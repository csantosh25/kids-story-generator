"""Publishes newly generated stories to Instagram as image CAROUSELS.

Never publishes Reels/videos and never touches story generation,
retention or the Reel pipeline -- it only READS data/content_library.json
and the story's existing output/<folder>/ images.

Per-story state machine (persisted in services/instagram_publication_
store.py BEFORE each externally visible step, so an interrupted run can
always be recovered from):

    (none) -> claimed -> containers_ready -> publish_requested -> published
                 \\              \\                    \\
                  +------------->+---------> failed     +-> (recovery)

publish_requested is written and pushed BEFORE media_publish is called.
If that call then times out / drops / returns 5xx, the result is
UNCERTAIN and media_publish is NEVER simply retried: the carousel
container's own status_code is checked first --

    PUBLISHED        -> the post exists; record it as published
    FINISHED         -> definitely not published; safe to publish it
    EXPIRED / ERROR  -> definitely not published (unless our caption is
                        found on the account's recent media)
    unknown          -> left as publish_requested; the next run checks again

Eligibility (all must hold):
  * not already published / in progress in the publication store;
  * created on or after the date live publishing was first enabled
    (`live_enabled_on`, recorded by the first live run) -- so enabling
    never back-fills historical stories;
  * created within max_story_age_days of today (UTC), unless a specific
    content_id was requested;
  * the story folder has a cover and contiguous slides.
Order: oldest first by (created_date, content_id) -- the same ordering
key retention uses.
"""
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.instagram_carousel_service import (
    CarouselAssetError,
    build_caption,
    caption_fingerprint,
    load_story_json,
    prepare_carousel_jpegs,
    select_carousel_images,
)
from services.instagram_graph_client import InstagramApiError, InstagramUncertainError
from services.cloudinary_hosting_service import HostingError
from services.instagram_publication_store import SkipUpdate


CLAIMED = "claimed"
CONTAINERS_READY = "containers_ready"
PUBLISH_REQUESTED = "publish_requested"
PUBLISHED = "published"
FAILED = "failed"

IN_PROGRESS_STATES = (CLAIMED, CONTAINERS_READY, PUBLISH_REQUESTED)

PUBLISHABLE_ACCOUNT_TYPES = ("BUSINESS", "MEDIA_CREATOR")

MAX_HISTORY = 20
MAX_ERROR_CHARS = 500


class ConfigurationError(RuntimeError):
    pass


class ContainerError(RuntimeError):
    pass


class LostClaimError(RuntimeError):
    pass


@dataclass
class PublisherConfig:
    live: bool = False
    run_id: str = "local"
    expected_username: str = "bedtime01fables"
    ig_user_id: str = ""
    max_story_age_days: int = 1
    max_posts_per_run: int = 3
    max_attempts: int = 3
    claim_lease_minutes: int = 60
    poll_interval_seconds: int = 30
    poll_timeout_seconds: int = 300
    failed_asset_retention_hours: int = 48


@dataclass
class StoryOutcome:
    content_id: str
    title: str = ""
    action: str = ""
    detail: str = ""
    media_id: str = ""
    permalink: str = ""
    images: list = field(default_factory=list)
    caption: str = ""

    @property
    def failed(self):
        return self.action in ("failed", "uncertain")


def _utcnow():
    return datetime.now(timezone.utc)


def _safe_error(error):
    text = f"{type(error).__name__}: {error}"
    return text[:MAX_ERROR_CHARS]


def _parse_date(value):
    try:
        return datetime.strptime(value or "", "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _sort_key(entry):
    return (_parse_date(entry.get("created_date")) or datetime.min.date(),
            entry.get("content_id", ""))


class InstagramPublisher:

    def __init__(self, config, store, library, graph=None, hosting=None,
                 now=_utcnow, sleep=time.sleep, log=print):

        self.config = config
        self.store = store
        self.library = library
        self.graph = graph
        self.hosting = hosting
        self.now = now
        self.sleep = sleep
        self.log = log

    # =================================================================
    # Entry point
    # =================================================================

    def run(self, content_id=None):

        if self.config.live:
            self._validate_live_setup()
            self._ensure_baseline()

        state, _ = self.store.load()

        decisions = self.select(self.library.get_all_stories(), state, content_id)
        outcomes = [d for d in decisions if d.action.startswith("skip")]
        eligible = [d for d in decisions if not d.action.startswith("skip")]

        for decision in eligible[: self.config.max_posts_per_run]:

            entry = self.library.get_story(decision.content_id)

            if not self.config.live:
                outcomes.append(self._dry_run(entry, decision))
                continue

            try:
                outcomes.append(self._publish_story(entry, decision))
            except Exception as error:
                self.log(f"❌ {decision.content_id}: {_safe_error(error)}")
                outcomes.append(StoryOutcome(
                    decision.content_id, decision.title, "failed", _safe_error(error)
                ))

        for decision in eligible[self.config.max_posts_per_run:]:
            decision.action = "skip-deferred"
            decision.detail = "per-run limit reached; next run will publish it"
            outcomes.append(decision)

        if self.config.live:
            self._cleanup_hosted_assets()

        return outcomes

    # =================================================================
    # Eligibility
    # =================================================================

    def select(self, stories, state, content_id=None):

        today = self.now().date()
        baseline = _parse_date(state.get("live_enabled_on")) or today
        records = state.get("stories", {})
        decisions = []

        for entry in sorted(stories, key=_sort_key):

            cid = entry.get("content_id", "")

            if content_id and cid != content_id:
                continue

            decision = StoryOutcome(cid, entry.get("title", ""))
            decision.action, decision.detail = self._eligibility(
                entry, records.get(cid), today, baseline, explicit=bool(content_id)
            )
            decisions.append(decision)

        if content_id and not decisions:
            decisions.append(StoryOutcome(
                content_id, "", "skip", "content_id not found in the Content Library"
            ))

        return decisions

    def _eligibility(self, entry, record, today, baseline, explicit):

        if record:

            if record.get("status") == PUBLISHED:
                return "skip", f"already published (media {record.get('media_id') or 'unknown'})"

            if record.get("folder") and record["folder"] != entry.get("folder"):
                return "skip", (
                    "publication record belongs to a different story folder; "
                    "refusing to reuse this content_id"
                )

            if record.get("status") in IN_PROGRESS_STATES and self._lease_active(record):
                return "skip", f"in progress in run {record.get('claimed_by')}"

            if record.get("status") == PUBLISH_REQUESTED:
                return "recover", "previous publish result is uncertain; checking status first"

            if record.get("attempt_count", 0) >= self.config.max_attempts:
                return "skip", (
                    f"failed {record.get('attempt_count')} times "
                    f"(max {self.config.max_attempts}); see docs to retry"
                )

        created = _parse_date(entry.get("created_date"))

        if created is None:
            return "skip", "invalid created_date"

        if not record and created < baseline:
            return "skip", f"historical (created before live publishing began on {baseline})"

        if not explicit and (today - created).days > self.config.max_story_age_days:
            return "skip", f"older than {self.config.max_story_age_days} day(s)"

        try:
            select_carousel_images(Path(entry.get("folder", "")))
        except CarouselAssetError as error:
            return "skip", f"assets incomplete: {error}"

        return ("retry" if record else "publish"), ""

    def _lease_active(self, record):

        if record.get("claimed_by") == self.config.run_id:
            return False

        try:
            claimed_at = datetime.fromisoformat(record.get("claimed_at"))
        except (TypeError, ValueError):
            return False

        return self.now() - claimed_at < timedelta(minutes=self.config.claim_lease_minutes)

    # =================================================================
    # Setup validation
    # =================================================================

    def _validate_live_setup(self):

        if self.graph is None or self.hosting is None:
            raise ConfigurationError("Live publishing needs an Instagram client and image hosting.")

        try:
            account = self.graph.get_account()
        except (InstagramApiError, InstagramUncertainError) as error:
            raise ConfigurationError(
                f"Could not verify the Instagram account (check the access token): {error}"
            ) from None

        username = (account.get("username") or "").lower()
        account_type = account.get("account_type")

        if username != self.config.expected_username.lower():
            raise ConfigurationError(
                f"Access token belongs to @{username or '?'}, expected "
                f"@{self.config.expected_username}."
            )

        if account_type not in PUBLISHABLE_ACCOUNT_TYPES:
            raise ConfigurationError(
                f"@{username} is a {account_type or 'non-professional'} account; "
                f"publishing needs a Business or Creator (professional) account."
            )

        if self.config.ig_user_id and str(account.get("user_id")) != str(self.config.ig_user_id):
            raise ConfigurationError(
                "INSTAGRAM_USER_ID does not match the account the access token belongs to."
            )

    def _ensure_baseline(self):

        today = self.now().date().isoformat()

        def mutate(state):
            if state.get("live_enabled_on"):
                raise SkipUpdate()
            state["live_enabled_on"] = today

        self.store.update(mutate, f"Enable Instagram publishing from {today}")

    # =================================================================
    # Dry run -- local only: no hosting, no Instagram, no state writes
    # =================================================================

    def _dry_run(self, entry, decision):

        folder = Path(entry["folder"])
        work = Path(tempfile.mkdtemp(prefix="ig-dryrun-"))

        try:
            images = select_carousel_images(folder)
            prepared = prepare_carousel_jpegs(images, work)
            caption = build_caption(load_story_json(folder))
        except (CarouselAssetError, OSError, ValueError) as error:
            return StoryOutcome(decision.content_id, decision.title, "failed", _safe_error(error))
        finally:
            shutil.rmtree(work, ignore_errors=True)

        return StoryOutcome(
            decision.content_id,
            decision.title,
            "dry-run",
            f"would {decision.action} a {len(images)}-image carousel",
            images=[
                f"{Path(p['source']).name} -> {p['size'][0]}x{p['size'][1]} JPEG "
                f"{p['bytes'] // 1024} KB" + (f" ({', '.join(p['adapted'])})" if p["adapted"] else "")
                for p in prepared
            ],
            caption=caption,
        )

    # =================================================================
    # Live publishing
    # =================================================================

    def _record_update(self, content_id, mutate, message, require_owner=True):
        """Applies mutate(record) to the story's record. With
        require_owner, raises LostClaimError (writing nothing) if this
        run no longer owns the claim."""

        lost = []

        def apply(state):

            lost.clear()
            record = state["stories"].get(content_id)

            if record is None:
                raise SkipUpdate()

            if require_owner and record.get("claimed_by") != self.config.run_id:
                lost.append(record.get("claimed_by"))
                raise SkipUpdate()

            mutate(record)
            record["updated_at"] = self.now().isoformat()

        state, written = self.store.update(apply, f"{content_id}: {message}")

        if lost or not written:
            raise LostClaimError(f"{content_id}: claim lost to run {lost[0] if lost else '?'}")

        return state["stories"][content_id]

    def _history(self, record, event, detail=""):
        record.setdefault("history", []).append(
            {"at": self.now().isoformat(), "run": self.config.run_id,
             "event": event, "detail": detail[:MAX_ERROR_CHARS]}
        )
        record["history"] = record["history"][-MAX_HISTORY:]

    def _claim(self, entry, caption):

        cid = entry["content_id"]
        now = self.now().isoformat()

        def mutate(state):

            record = state["stories"].get(cid)

            if record:
                if record.get("status") == PUBLISHED:
                    raise SkipUpdate()
                if record.get("status") in IN_PROGRESS_STATES and self._lease_active(record):
                    raise SkipUpdate()
                if record.get("status") == PUBLISH_REQUESTED:
                    raise SkipUpdate()   # must be recovered, never re-published blindly
                if record.get("attempt_count", 0) >= self.config.max_attempts:
                    raise SkipUpdate()
            else:
                record = state["stories"][cid] = {"content_id": cid, "attempt_count": 0}

            record.update({
                "title": entry.get("title", ""),
                "folder": entry.get("folder", ""),
                "created_date": entry.get("created_date", ""),
                "status": CLAIMED,
                "claimed_by": self.config.run_id,
                "claimed_at": now,
                "attempt_count": record.get("attempt_count", 0) + 1,
                "last_attempt_at": now,
                "caption": caption,
                "caption_fingerprint": caption_fingerprint(caption),
                "item_container_ids": [],
                "carousel_container_id": None,
                "last_error": None,
            })
            self._history(record, "claimed", f"attempt {record['attempt_count']}")

        # `written` (not a flag set inside mutate) is the only truth: a
        # mutation that lost a compare-and-swap is re-run or abandoned.
        _, written = self.store.update(mutate, f"{cid}: claim for publishing")
        return written

    def _take_over(self, content_id):
        """Adopts an abandoned publish_requested record (lease expired)
        for recovery, without changing its status."""

        def mutate(state):
            record = state["stories"].get(content_id)
            if not record or record.get("status") != PUBLISH_REQUESTED or self._lease_active(record):
                raise SkipUpdate()
            record["claimed_by"] = self.config.run_id
            record["claimed_at"] = self.now().isoformat()
            self._history(record, "recovery-started")

        _, written = self.store.update(mutate, f"{content_id}: recover uncertain publish")
        return written

    def _publish_story(self, entry, decision):

        cid = entry["content_id"]
        outcome = StoryOutcome(cid, entry.get("title", ""))

        if decision.action == "recover":

            if not self._take_over(cid):
                outcome.action, outcome.detail = "skip", "another run is recovering it"
                return outcome

            result = self._recover(cid)

            if result in ("published", "unresolved"):
                return self._outcome_from_record(cid, outcome, result)

            if result == "container_ready":
                record = self._current_record(cid)
                return self._publish_container(cid, record["carousel_container_id"], outcome)

            # "not_published": fall through to a fresh, duplicate-safe attempt.

        caption = build_caption(load_story_json(Path(entry["folder"])))

        if not self._claim(entry, caption):
            outcome.action, outcome.detail = "skip", "claimed by another run or not retryable"
            return outcome

        work = Path(tempfile.mkdtemp(prefix=f"ig-{cid}-"))

        try:
            carousel_id = self._prepare_containers(entry, caption, work)
        except LostClaimError:
            raise
        except (CarouselAssetError, HostingError, InstagramApiError,
                InstagramUncertainError, ContainerError, OSError) as error:
            # Nothing has been published: containers alone are never
            # visible and expire after 24h.
            self._mark_failed(cid, error)
            outcome.action, outcome.detail = "failed", _safe_error(error)
            return outcome
        finally:
            shutil.rmtree(work, ignore_errors=True)

        return self._publish_container(cid, carousel_id, outcome)

    def _prepare_containers(self, entry, caption, work):

        cid = entry["content_id"]
        images = select_carousel_images(Path(entry["folder"]))
        prepared = prepare_carousel_jpegs(images, work)

        hosted = []

        for index, item in enumerate(prepared, start=1):
            hosted.append(self.hosting.upload(item["path"], self.hosting.public_id_for(cid, index)))

        def record_assets(record):
            record["hosted_assets"] = [h["public_id"] for h in hosted]
            record["hosted_assets_deleted_at"] = None
            record["image_count"] = len(hosted)
            record["source_images"] = [Path(p).name for p in images]
            self._history(record, "uploaded", f"{len(hosted)} images")

        self._record_update(cid, record_assets, "hosted images uploaded")

        for item in hosted:
            self.hosting.wait_until_accessible(item["url"])

        children = [self.graph.create_carousel_item(item["url"]) for item in hosted]

        for child in children:
            self._wait_for_container(child)

        carousel_id = self.graph.create_carousel(children, caption)
        self._wait_for_container(carousel_id)

        def record_containers(record):
            record["status"] = CONTAINERS_READY
            record["item_container_ids"] = children
            record["carousel_container_id"] = carousel_id
            self._history(record, "containers-ready", carousel_id)

        self._record_update(cid, record_containers, "carousel container ready")

        limit = self.graph.get_publishing_limit()
        quota = limit.get("config", {}).get("quota_total")

        if quota is not None and limit.get("quota_usage", 0) >= quota:
            raise ContainerError(f"Instagram publishing limit reached ({limit.get('quota_usage')}/{quota}).")

        return carousel_id

    def _wait_for_container(self, container_id):

        deadline = time.monotonic() + self.config.poll_timeout_seconds
        status = None

        while True:

            try:
                status, detail = self.graph.get_container_status(container_id)
            except InstagramUncertainError:
                status, detail = None, "status check had no response"

            if status == "FINISHED":
                return status

            if status in ("ERROR", "EXPIRED"):
                raise ContainerError(f"container {container_id} {status}: {detail or ''}".strip())

            if time.monotonic() >= deadline:
                raise ContainerError(
                    f"container {container_id} not ready after "
                    f"{self.config.poll_timeout_seconds}s (last status {status})"
                )

            self.sleep(self.config.poll_interval_seconds)

    def _publish_container(self, cid, carousel_id, outcome):

        def mark_requested(record):
            record["status"] = PUBLISH_REQUESTED
            record["publish_requested_at"] = self.now().isoformat()
            self._history(record, "publish-requested", carousel_id)

        # Durable BEFORE the irreversible call. If this write fails, we
        # never call media_publish.
        self._record_update(cid, mark_requested, "publish requested")

        # A second call is only ever made after the container's own
        # status proved the first one did NOT publish (FINISHED), and it
        # reuses the same single-use container -- so it cannot create a
        # second post.
        for attempt in range(2):

            try:
                media_id = self.graph.publish(carousel_id)
            except InstagramApiError as error:
                # Definitive rejection: nothing was published.
                self._mark_failed(cid, error)
                outcome.action, outcome.detail = "failed", _safe_error(error)
                return outcome
            except InstagramUncertainError as error:
                self.log(f"⚠️ {cid}: publish result uncertain ({error}); checking container status.")
                self.sleep(self.config.poll_interval_seconds)
                result = self._recover(cid)
                if result == "container_ready" and attempt == 0:
                    continue
                if result == "container_ready":
                    result = "unresolved"
                return self._outcome_from_record(cid, outcome, result)

            self._mark_published(cid, media_id, "published")
            return self._outcome_from_record(cid, outcome, "published")

    # =================================================================
    # Recovery
    # =================================================================

    def _current_record(self, cid):
        state, _ = self.store.load()
        return state["stories"].get(cid, {})

    def _find_our_media(self, record):

        try:
            media = self.graph.list_recent_media()
        except (InstagramApiError, InstagramUncertainError):
            return None

        for item in media:
            if caption_fingerprint(item.get("caption")) == record.get("caption_fingerprint"):
                return item

        return None

    def _recover(self, cid):
        """Never calls media_publish. Returns one of: published,
        container_ready, not_published, unresolved."""

        record = self._current_record(cid)
        carousel_id = record.get("carousel_container_id")

        try:
            status, _ = self.graph.get_container_status(carousel_id)
        except (InstagramApiError, InstagramUncertainError):
            status = None

        if status == "PUBLISHED":
            media = self._find_our_media(record)
            self._mark_published(cid, media.get("id") if media else None, "recovered: container PUBLISHED")
            return "published"

        if status == "FINISHED":
            return "container_ready"

        media = self._find_our_media(record)

        if media:
            self._mark_published(cid, media.get("id"), "recovered: caption found on account")
            return "published"

        if status in ("EXPIRED", "ERROR"):
            self._mark_failed(cid, ContainerError(f"container {status}; not published"))
            return "not_published"

        def mark_unresolved(record):
            record["last_error"] = f"publish result unresolved (container status {status})"
            self._history(record, "unresolved", record["last_error"])

        self._record_update(cid, mark_unresolved, "publish result unresolved", require_owner=False)
        return "unresolved"

    # =================================================================
    # Terminal states
    # =================================================================

    def _mark_failed(self, cid, error):

        def mutate(record):
            record["status"] = FAILED
            record["last_error"] = _safe_error(error)
            self._history(record, "failed", record["last_error"])

        self._record_update(cid, mutate, "publish failed")

    def _mark_published(self, cid, media_id, how):

        permalink = ""

        if media_id:
            try:
                permalink = self.graph.get_media(media_id).get("permalink", "")
            except (InstagramApiError, InstagramUncertainError):
                pass

        def mutate(record):
            record["status"] = PUBLISHED
            record["media_id"] = media_id
            record["permalink"] = permalink
            record["published_at"] = self.now().isoformat()
            record["last_error"] = None
            self._history(record, "published", how)

        # Recording a real publication must never be blocked by claim
        # ownership.
        self._record_update(cid, mutate, "published", require_owner=False)

    def _outcome_from_record(self, cid, outcome, result):

        record = self._current_record(cid)
        outcome.media_id = record.get("media_id") or ""
        outcome.permalink = record.get("permalink") or ""

        if result == "published":
            outcome.action = "published"
            outcome.detail = record.get("history", [{}])[-1].get("detail", "")
        elif result == "unresolved":
            outcome.action = "uncertain"
            outcome.detail = "publish result could not be confirmed; next run re-checks (no blind retry)"
        else:
            outcome.action = "failed"
            outcome.detail = record.get("last_error") or ""

        return outcome

    # =================================================================
    # Hosted-asset retention
    # =================================================================

    def _cleanup_hosted_assets(self):

        state, _ = self.store.load()
        cutoff = self.now() - timedelta(hours=self.config.failed_asset_retention_hours)

        for cid, record in state["stories"].items():

            if not record.get("hosted_assets") or record.get("hosted_assets_deleted_at"):
                continue

            status = record.get("status")

            if status == FAILED:
                try:
                    stale = datetime.fromisoformat(record.get("last_attempt_at")) < cutoff
                except (TypeError, ValueError):
                    stale = False
                if not stale:
                    continue
            elif status != PUBLISHED:
                continue

            try:
                for public_id in record["hosted_assets"]:
                    self.hosting.delete(public_id)
            except HostingError as error:
                self.log(f"⚠️ {cid}: hosted image cleanup failed, will retry next run: {error}")
                continue

            def mutate(rec):
                rec["hosted_assets_deleted_at"] = self.now().isoformat()
                self._history(rec, "hosted-assets-deleted", f"{len(rec['hosted_assets'])} images")

            self._record_update(cid, mutate, "hosted images deleted", require_owner=False)
