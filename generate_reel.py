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

    parser.add_argument(
        "--content-id",
        dest="content_id",
        default=None,
        help=(
            "Content Library ID of the story to turn into a Reel. "
            "Runs non-interactively (no prompts, no music selection) and "
            "never posts to Instagram."
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

    tracks = list_music_tracks()

    if tracks:
        print("Available background music (optional):")
        print("0. No music")
        for i, track in enumerate(tracks, start=1):
            print(f"{i}. {track.name}")
        print()
        music_input = input("Select a track number (or press Enter for no music): ").strip()
        if music_input.isdigit() and 1 <= int(music_input) <= len(tracks):
            music_choice = tracks[int(music_input) - 1].name
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


def run_non_interactive(service, content_id):

    print("Mode: Non-interactive")
    print(f"Content ID: {content_id}")
    print()
    print("This run operates ONLY on the existing Content Library entry")
    print("above. No new story, cover, or slides will be generated, and no")
    print("email will be sent.")
    print()

    entry = service.library.get_story(content_id)

    if entry is None:
        print(f"❌ No story found in the Content Library for content ID: {content_id}")
        raise SystemExit(1)

    print("Selected Story (non-interactive)")
    print("-----------------------")
    print(entry["title"])
    print(entry["folder"])
    print()

    try:

        # No music prompt in non-interactive mode; overwrite=True since
        # there is no one to confirm a replacement, and this mode exists
        # specifically to (re-)generate the Reel for a given content ID.
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
    print(f"Story Title: {entry['title']}")
    print(f"Output Path: {video_path}")

    if "duration_seconds" in metadata:
        print(f"Video Duration: {metadata['duration_seconds']}s")

    if "width" in metadata and "height" in metadata:
        print(f"Video Dimensions: {metadata['width']}x{metadata['height']}")

    print()
    print("This tool never posts to Instagram automatically.")


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
    else:
        run_interactive(service)


if __name__ == "__main__":
    main()
