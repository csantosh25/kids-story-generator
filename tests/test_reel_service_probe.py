import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.reel_service import probe_video_metadata


class TestProbeVideoMetadata(unittest.TestCase):
    """probe_video_metadata is best-effort diagnostic info gathered via
    ffprobe; subprocess is always mocked here so no real ffprobe binary is
    invoked during unit tests."""

    @patch("services.reel_service.shutil.which", return_value=None)
    def test_returns_empty_dict_when_ffprobe_missing(self, mock_which):

        self.assertEqual(probe_video_metadata(Path("reel.mp4")), {})

    @patch("services.reel_service.subprocess.run")
    @patch("services.reel_service.shutil.which", return_value="/usr/bin/ffprobe")
    def test_parses_duration_and_dimensions(self, mock_which, mock_run):

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"streams": [{"width": 1080, "height": 1920}], "format": {"duration": "27.531000"}}',
        )

        metadata = probe_video_metadata(Path("reel.mp4"))

        self.assertEqual(metadata["width"], 1080)
        self.assertEqual(metadata["height"], 1920)
        self.assertEqual(metadata["duration_seconds"], 27.53)

    @patch("services.reel_service.subprocess.run")
    @patch("services.reel_service.shutil.which", return_value="/usr/bin/ffprobe")
    def test_returns_empty_dict_on_nonzero_exit(self, mock_which, mock_run):

        mock_run.return_value = MagicMock(returncode=1, stdout="")

        self.assertEqual(probe_video_metadata(Path("reel.mp4")), {})

    @patch("services.reel_service.subprocess.run", side_effect=OSError("boom"))
    @patch("services.reel_service.shutil.which", return_value="/usr/bin/ffprobe")
    def test_returns_empty_dict_on_exception(self, mock_which, mock_run):

        self.assertEqual(probe_video_metadata(Path("reel.mp4")), {})

    @patch("services.reel_service.subprocess.run")
    @patch("services.reel_service.shutil.which", return_value="/usr/bin/ffprobe")
    def test_real_shaped_output_detects_video_and_audio_streams(self, mock_which, mock_run):
        """Real ffprobe output (unlike the older mocks above) includes
        codec_type on every stream and both a video and an audio stream --
        this must correctly pick the video stream for width/height and
        detect that an audio stream is present."""

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                '{"streams": ['
                '{"codec_type": "video", "width": 1080, "height": 1920}, '
                '{"codec_type": "audio"}'
                '], "format": {"duration": "25.10"}}'
            ),
        )

        metadata = probe_video_metadata(Path("reel.mp4"))

        self.assertEqual(metadata["width"], 1080)
        self.assertEqual(metadata["height"], 1920)
        self.assertEqual(metadata["duration_seconds"], 25.1)
        self.assertTrue(metadata["has_audio"])

    @patch("services.reel_service.subprocess.run")
    @patch("services.reel_service.shutil.which", return_value="/usr/bin/ffprobe")
    def test_video_only_stream_reports_no_audio(self, mock_which, mock_run):

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                '{"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], '
                '"format": {"duration": "25.10"}}'
            ),
        )

        metadata = probe_video_metadata(Path("reel.mp4"))

        self.assertFalse(metadata["has_audio"])


if __name__ == "__main__":
    unittest.main()
