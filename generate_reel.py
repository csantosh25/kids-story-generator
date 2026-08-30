import argparse

from services.reel_service import (
    ReelService,
    check_ffmpeg_available,
    list_music_tracks,
    probe_video_metadata,
    FFmpegNotAvailableError,
    MissingStoryAssetsError,
    ReelGenerationError,
)


def parse_args(argv=None):

    parser = argparse.ArgumentParser(description="Kids Story Reel Generator")

    selection = parser.add_mutually_exclusive_group()

    selection.add_argument(
        "--content-id",
        dest="content_id",
        default=None,
        help=(
            "Content Library ID of the story to turn into a Reel. "
            "Runs non-interactively (no prompts, no music selection) and "
            "never posts to Instagram."
        ),
    )

    selection.add_argument(
        "--latest",
        dest="latest",
        action="store_true",
        help=(
            "Select the most recently created eligible story (by "
            "created_date, not just the highest Content ID) instead of a "
            "specific Content ID. Same non-interactive, no-music-prompt, "
            "never-posts-to-Instagram behaviour as --content-id."
        ),
    )

    return parser.parse_args(argv)


def run_interactive(service):

    eligible_stories = service.list_reel_eligible_stories()

    if not eligible_stories:
        print("No stories with the required assets (cover + slide images) were")
        print("found in the Content Library. Generate a story via the daily")
        print("pipeline first.")
        raise SystemExit(0)

    print("Available stories:")
    print()

    for index, entry in enumerate(eligible_stories, start=1):

        reel_state = entry.get("reel", {})
        status = " (Reel already generated)" if reel_state.get("generated") else ""

        print(f"{index}. {entry['title']} ({entry['content_id']}){status}")

    print()

    try:
        choice = int(input("Select a story number: "))
        selected = eligible_stories[choice - 1]
    except (ValueError, IndexError):
        print("Invalid selection.")
        raise SystemExit(1)

    print()
    print("Selected Story")
    print("-----------------------")
    print(selected["title"])
    print(selected["folder"])
    print()

    overwrite = False

    existing_video = selected.get("reel", {}).get("video", "")

    if existing_video:
        answer = input(
            f"A Reel already exists for this story ({existing_video}). "
            f"Overwrite it? [y/N]: "
        ).strip().lower()
        if answer != "y":
            print("Cancelled. No changes made.")
            raise SystemExit(0)
        overwrite = True

    music_choice = None
    disable_music = False

    tracks = list_music_tracks()

    if tracks:
        print("Available background music (optional):")
        print("0. No music")
        for i, track in enumerate(tracks, start=1):
            print(f"{i}. {track.name}")
        print()
        music_input = input(
            "Select a track number, 0 for no music, or press Enter to "
            "auto-select a track for this story: "
        ).strip()
        if music_input.isdigit() and 1 <= int(music_input) <= len(tracks):
            music_choice = tracks[int(music_input) - 1].name
        elif music_input == "0":
            disable_music = True
        # Blank input: leave music_choice=None, disable_music=False --
        # ReelService.generate() auto-selects a track deterministically
        # by content_id (see select_music_track).
    else:
        print("No background music tracks found in assets/music/ -- continuing")
        print("with narration only.")

    print()
    print("🎬 Generating Reel...")
    print()

    try:

        video_path = service.generate(
            content_id=selected["content_id"],
            overwrite=overwrite,
            music_track=music_choice,
            disable_music=disable_music,
        )

    except FFmpegNotAvailableError as error:

        print(f"❌ {error}")
        raise SystemExit(1)

    except MissingStoryAssetsError as error:

        print(f"❌ {error}")
        raise SystemExit(1)

    except ReelGenerationError as error:

        print(f"❌ Reel generation failed: {error}")
        print("The Content Library was NOT updated -- this story is still")
        print("marked as not having a Reel.")
        raise SystemExit(1)

    print("✅ Reel generated successfully.")
    print(f"   {video_path}")
    print()
    print("Next steps:")
    print("1. Review the Reel yourself.")
    print("2. Post it to Instagram manually when you're ready.")
    print("   (This tool never posts automatically.)")


def _print_selection_summary(mode_label, entry):
    """Prints the "which story is this run using, and why" summary
    shared by both non-interactive selection modes (a specific Content
    ID, or the latest eligible story) -- see generate_reel.py's own
    "Selection mode" header format. `entry` is a Content Library dict
    (see ContentLibraryService); missing optional fields print blank
    rather than erroring."""

    character = entry.get("character") or {}
    character_label = f"{character.get('name', '')} {character.get('species', '')}".strip()

    print(f"Selection mode: {mode_label}")
    print()
    print(f"Content ID : {entry.get('content_id', '')}")
    print(f"Title      : {entry.get('title', '')}")
    print(f"Created    : {entry.get('created_date', '')}")
    print(f"Character  : {character_label}")
    print()
    print("Using existing story assets.")
    print("No new story will be generated.")
    print("No email will be sent.")
    print()


def _generate_and_report(service, content_id, entry):
    """Shared "run the existing Reel generation pipeline and report the
    result" core for both non-interactive selection modes -- exactly one
    place calls service.generate(), so a specific-Content-ID run and a
    latest-eligible-story run behave identically once a content_id has
    been resolved."""

    try:

        # No music prompt in non-interactive mode -- music_track=None (the
        # default) lets ReelService.generate() auto-select a track
        # deterministically by content_id when assets/music/ has valid
        # tracks (see select_music_track), or continue narration-only
        # otherwise. overwrite=True since there is no one to confirm a
        # replacement, and this mode exists specifically to (re-)generate
        # the Reel for a given content ID.
        video_path = service.generate(
            content_id=content_id,
            overwrite=True,
            music_track=None,
        )

    except FFmpegNotAvailableError as error:

        print(f"❌ {error}")
        raise SystemExit(1)

    except MissingStoryAssetsError as error:

        print(f"❌ {error}")
        raise SystemExit(1)

    except ReelGenerationError as error:

        print(f"❌ Reel generation failed: {error}")
        print("The Content Library was NOT updated -- this story is still")
        print("marked as not having a Reel.")
        raise SystemExit(1)

    metadata = probe_video_metadata(video_path)

    print("✅ Reel generated successfully.")
    print()
    print(f"Content ID: {content_id}")
    print(f"Story Title: {entry.get('title', '')}")
    print(f"Output Path: {video_path}")

    if "duration_seconds" in metadata:
        print(f"Video Duration: {metadata['duration_seconds']}s")

    if "width" in metadata and "height" in metadata:
        print(f"Video Dimensions: {metadata['width']}x{metadata['height']}")

    print()
    print("This tool never posts to Instagram automatically.")


def run_non_interactive(service, content_id):
    """Specific-Content-ID path: uses exactly the supplied content_id,
    never silently substituting a different story. Fails clearly if it
    doesn't exist in the Content Library."""

    entry = service.library.get_story(content_id)

    if entry is None:
        print(f"❌ No story found in the Content Library for content ID: {content_id}")
        raise SystemExit(1)

    _print_selection_summary("Specific Content ID", entry)
    _generate_and_report(service, content_id, entry)


def run_latest_non_interactive(service):
    """Latest-eligible-story path: picks the most recently created
    (by created_date, not the highest Content ID) story that already has
    everything a Reel needs on disk -- see ReelService.get_latest_
    eligible_story(), which reuses the same eligibility check as the
    interactive picker (list_reel_eligible_stories()) rather than a
    separate set of rules. An incomplete newer story is skipped
    automatically, since it's simply not in that eligible list."""

    entry = service.get_latest_eligible_story()

    if entry is None:
        print("❌ No eligible story found in the Content Library.")
        print("A story needs story.json, a cover image, and its carousel")
        print("slide images on disk before it can be turned into a Reel.")
        print("Generate a story via the daily pipeline first.")
        raise SystemExit(1)

    _print_selection_summary("Latest eligible story", entry)
    _generate_and_report(service, entry["content_id"], entry)


def main(argv=None):

    args = parse_args(argv)

    print("=" * 70)
    print("🎬 Kids Story Reel Generator")
    print("=" * 70)
    print()
    print("This is a MANUAL, on-demand tool. It does not run automatically,")
    print("does not touch the daily story pipeline, and does not post to")
    print("Instagram -- it only creates reel.mp4 for you to review and post")
    print("yourself.")
    print()

    if not check_ffmpeg_available():
        print("❌ ffmpeg was not found on this machine.")
        print()
        print("Reel generation requires ffmpeg to be installed and on PATH.")
        print("Verify your installation with:")
        print()
        print("    ffmpeg -version")
        print()
        print("If that fails, install ffmpeg first, then run this tool again.")
        raise SystemExit(1)

    service = ReelService()

    if args.content_id:
        run_non_interactive(service, args.content_id)
    elif args.latest:
        run_latest_non_interactive(service)
    else:
        run_interactive(service)


if __name__ == "__main__":
    main()
