import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reel_analyzer.reel_analyzer import ReelAnalyzer, ReelAnalysisError
from reel_analyzer.reel_download import InvalidReelUrl, canonicalize_reel_url
from reel_analyzer.reel_media import _representative_timestamps


class ReelUrlTests(unittest.TestCase):
    def test_canonicalizes_tracking_query(self):
        self.assertEqual(
            canonicalize_reel_url("https://www.instagram.com/reel/Dc6Ar0BNoLx/?stkn=abc"),
            "https://www.instagram.com/reel/Dc6Ar0BNoLx/",
        )

    def test_rejects_non_reel_url(self):
        with self.assertRaises(InvalidReelUrl):
            canonicalize_reel_url("https://www.instagram.com/p/example/")

    def test_rejects_other_host(self):
        with self.assertRaises(InvalidReelUrl):
            canonicalize_reel_url("https://example.com/reel/Dc6Ar0BNoLx/")


class ReelMediaTests(unittest.TestCase):
    def test_representative_timestamps_use_segment_midpoints(self):
        self.assertEqual(
            _representative_timestamps(60.0, 6),
            [5.0, 15.0, 25.0, 35.0, 45.0, 55.0],
        )


class ReelOrchestrationTests(unittest.TestCase):
    @patch("reel_analyzer.reel_analyzer.describe_contact_sheet", return_value="visual")
    @patch("reel_analyzer.reel_analyzer.extract_contact_sheet")
    @patch("reel_analyzer.reel_analyzer.extract_audio")
    @patch("reel_analyzer.reel_analyzer.download_public_reel")
    @patch.object(ReelAnalyzer, "_transcribe", return_value="speech")
    def test_combines_audio_and_visual(self, transcribe, download, audio, sheet, vision):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "source.mp4"
            media.write_bytes(b"x")
            download.return_value = (media, {"title": "Example", "duration": 37})
            audio.side_effect = lambda _media, output: output
            sheet.side_effect = lambda _media, output: output
            result = ReelAnalyzer().analyze("https://instagram.com/reel/ABC123/")
        self.assertEqual(result["transcript"], "speech")
        self.assertEqual(result["visual_facts"], "visual")
        self.assertEqual(result["duration"], 37)
        self.assertEqual(result["url"], "https://www.instagram.com/reel/ABC123/")

    @patch("reel_analyzer.reel_analyzer._probe_duration", return_value=37.5)
    @patch("reel_analyzer.reel_analyzer.describe_contact_sheet", return_value="visual")
    @patch("reel_analyzer.reel_analyzer.extract_contact_sheet")
    @patch("reel_analyzer.reel_analyzer.extract_audio")
    @patch("reel_analyzer.reel_analyzer.download_public_reel")
    @patch.object(ReelAnalyzer, "_transcribe", return_value="speech")
    def test_falls_back_to_media_duration(self, transcribe, download, audio, sheet, vision, probe):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "source.mp4"
            media.write_bytes(b"x")
            download.return_value = (media, {"title": "Example", "duration": None})
            audio.side_effect = lambda _media, output: output
            sheet.side_effect = lambda _media, output: output
            result = ReelAnalyzer().analyze("https://instagram.com/reel/ABC123/")
        self.assertEqual(result["duration"], 37.5)
        probe.assert_called_once_with(media)

    @patch("reel_analyzer.reel_analyzer.download_public_reel", side_effect=RuntimeError("blocked"))
    def test_wraps_pipeline_failure(self, _download):
        with self.assertRaises(ReelAnalysisError):
            ReelAnalyzer().analyze("https://instagram.com/reel/ABC123/")


if __name__ == "__main__":
    unittest.main()
