"""
Post-render safeguard for the Reel pipeline.

A real production defect (V3: multiple `zoompan` filter instances sharing
one ffmpeg filter_complex + concat collapsed every scene after the first
into the first scene's content) reached production despite 92 passing
unit tests, because none of them executed real ffmpeg end to end -- every
test mocked ffmpeg entirely. V4's rendering architecture (per-scene clips
-> concat demuxer -> final assembly; see render_reel_video() in
reel_service.py) fixes the specific defect, but this check remains as a
cheap, permanent, automated guard against the whole *class* of regression:
it verifies the actual rendered video shows different content at
different timestamps, not just that ffmpeg's inputs were correct.
"""
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path


def _sha256(path: Path) -> str:

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_rendered_video_has_scene_changes(output_path: Path, sample_times=(0, 5, 10, 15, 20, 25)):
    """Extracts real frames from the actual rendered reel.mp4 at several
    timestamps and confirms they are not all pixel-identical.

    Raises RuntimeError (does not delete output_path -- caller decides)
    if every sampled frame is identical. Best-effort: skipped (prints a
    warning, does not raise) if ffmpeg is unavailable for frame
    extraction, or if the video is too short to sample at least 2 of the
    given timestamps."""

    ffmpeg_bin = shutil.which("ffmpeg")

    if ffmpeg_bin is None:
        print("Post-render scene-change check: ffmpeg not available -- skipping.")
        return

    with tempfile.TemporaryDirectory() as tmp:

        tmp = Path(tmp)
        hashes = []

        for t in sample_times:

            frame_path = tmp / f"frame_{t:02d}.png"

            result = subprocess.run(
                [ffmpeg_bin, "-y", "-ss", str(t), "-i", str(output_path),
                 "-frames:v", "1", str(frame_path)],
                capture_output=True, text=True, timeout=30,
            )

            if result.returncode != 0 or not frame_path.exists():
                continue

            hashes.append(_sha256(frame_path))

        if len(hashes) >= 2 and len(set(hashes)) == 1:
            raise RuntimeError(
                f"Every sampled frame of the rendered {output_path} is "
                f"pixel-identical across {sample_times} -- the video "
                "shows no scene changes at all despite multiple distinct "
                "scene images being provided to the renderer."
            )
