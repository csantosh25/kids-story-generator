"""Publishes newly generated stories to Instagram as image carousels.

    python publish_instagram.py              # dry run (default)
    python publish_instagram.py --live       # live, only if enabled
    python publish_instagram.py --content-id KS-000012

Live publishing requires BOTH --live AND INSTAGRAM_AUTO_PUBLISH_ENABLED=
true; anything else is a dry run, which reads the publication history
but makes no hosting/Instagram calls and writes nothing.

Configuration (environment; names only -- never print values):
  secrets   INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_USER_ID,
            CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
  variables INSTAGRAM_AUTO_PUBLISH_ENABLED (default false),
            INSTAGRAM_EXPECTED_USERNAME (default bedtime01fables),
            INSTAGRAM_GRAPH_API_VERSION (default: services/
            instagram_graph_client.DEFAULT_API_VERSION),
            INSTAGRAM_MAX_STORY_AGE_DAYS (default 1),
            INSTAGRAM_MAX_POSTS_PER_RUN (default 3)

See docs/INSTAGRAM_PUBLISHING.md.
"""
import argparse
import os
import uuid

from services.content_library_service import ContentLibraryService
from services.instagram_publication_store import (
    GitBranchPublicationStore,
    InMemoryPublicationStore,
    StateStoreError,
)
from services.instagram_publisher import (
    ConfigurationError,
    InstagramPublisher,
    PublisherConfig,
)


REQUIRED_SECRETS = (
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_USER_ID",
    "CLOUDINARY_CLOUD_NAME",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
)


def _flag(name, default=False):
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        raise ConfigurationError(f"{name} must be a whole number.") from None


def missing_secrets(env=None):
    env = os.environ if env is None else env
    return [name for name in REQUIRED_SECRETS if not (env.get(name) or "").strip()]


def build_config(live_requested):

    enabled = _flag("INSTAGRAM_AUTO_PUBLISH_ENABLED")
    live = live_requested and enabled

    run_id = os.environ.get("GITHUB_RUN_ID")
    run_id = (
        f"{run_id}.{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
        if run_id else f"local-{uuid.uuid4().hex[:8]}"
    )

    config = PublisherConfig(
        live=live,
        run_id=run_id,
        expected_username=os.environ.get("INSTAGRAM_EXPECTED_USERNAME", "").strip() or "bedtime01fables",
        ig_user_id=os.environ.get("INSTAGRAM_USER_ID", "").strip(),
        max_story_age_days=_int("INSTAGRAM_MAX_STORY_AGE_DAYS", 1),
        max_posts_per_run=_int("INSTAGRAM_MAX_POSTS_PER_RUN", 3),
    )

    if live_requested and not enabled:
        reason = "INSTAGRAM_AUTO_PUBLISH_ENABLED is not 'true'"
    elif not live_requested:
        reason = "--live was not requested"
    else:
        reason = ""

    return config, reason


def build_live_clients(config):

    from services.cloudinary_hosting_service import CloudinaryHostingService
    from services.instagram_graph_client import InstagramGraphClient

    graph = InstagramGraphClient(
        access_token=os.environ["INSTAGRAM_ACCESS_TOKEN"].strip(),
        ig_user_id=config.ig_user_id,
        api_version=os.environ.get("INSTAGRAM_GRAPH_API_VERSION", "").strip() or None,
    )

    hosting = CloudinaryHostingService(
        cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"].strip(),
        api_key=os.environ["CLOUDINARY_API_KEY"].strip(),
        api_secret=os.environ["CLOUDINARY_API_SECRET"].strip(),
    )

    return graph, hosting


def write_summary(config, reason, outcomes):

    mode = "LIVE" if config.live else f"DRY RUN ({reason})"
    lines = [
        "## Instagram carousel publishing",
        "",
        f"Mode: **{mode}**  ·  Run: `{config.run_id}`",
        "",
        "| Story | Title | Result | Detail |",
        "|---|---|---|---|",
    ]

    for o in outcomes:
        detail = (o.permalink or o.detail or "").replace("|", "/").replace("\n", " ")
        lines.append(f"| {o.content_id} | {o.title} | {o.action} | {detail} |")

    if not outcomes:
        lines.append("| - | - | nothing to do | no stories in the Content Library |")

    for o in outcomes:
        if o.action == "dry-run":
            lines += ["", f"### {o.content_id} -- carousel order", ""]
            lines += [f"{i}. {image}" for i, image in enumerate(o.images, start=1)]
            lines += ["", "Caption:", "", "```", o.caption, "```"]

    text = "\n".join(lines) + "\n"
    print(text)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv=None):

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--content-id", default="")
    parser.add_argument(
        "--no-state", action="store_true",
        help="dry run against an empty in-memory history (no git access)",
    )
    args = parser.parse_args(argv)

    try:
        config, reason = build_config(args.live)

        graph = hosting = None

        if config.live:
            missing = missing_secrets()
            if missing:
                raise ConfigurationError(
                    "Live publishing is enabled but these secrets are missing: "
                    + ", ".join(missing)
                )
            graph, hosting = build_live_clients(config)

        if args.no_state and not config.live:
            store = InMemoryPublicationStore()
        else:
            store = GitBranchPublicationStore()

        publisher = InstagramPublisher(config, store, ContentLibraryService(), graph, hosting)
        outcomes = publisher.run(content_id=args.content_id.strip() or None)

    except (ConfigurationError, StateStoreError) as error:
        print(f"❌ {error}")
        raise SystemExit(1)

    write_summary(config, reason, outcomes)

    if any(o.failed for o in outcomes):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
