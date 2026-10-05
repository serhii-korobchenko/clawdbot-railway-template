import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from reel_analyzer.post_download import PostExtractionError, download_post_images, download_post_media, extract_post_images, extract_post_media
from reel_analyzer.reel_download import InvalidReelUrl, canonicalize_instagram_url
from reel_analyzer.reel_analyzer import ReelAnalyzer, summarize_post
from reel_analyzer.reel_vision import describe_post_images


def embed(nodes):
    sidecar = json.dumps({"edges": [{"node": node} for node in nodes]})
    return 'prefix edge_sidecar_to_children\\":' + sidecar.replace('"', '\\"') + ' suffix'


def node(i, *, video=False, url=None):
    return {"is_video": video, "display_url": url or f"https:\\/\\/scontent.cdninstagram.com\\/image{i}.jpg"}


class PostUrlTests(unittest.TestCase):
    def test_selected_slide_and_tracking_removed(self):
        self.assertEqual(
            canonicalize_instagram_url("https://www.oginstagram.com/p/ABC123/?img_index=2&stkn=x"),
            ("https://www.instagram.com/p/ABC123/?img_index=2", "p", 2),
        )

    def test_rejects_unsafe_host_and_invalid_index(self):
        for url in ("https://example.com/p/ABC/", "https://instagram.com.evil.test/p/ABC/",
                    "https://instagram.com/p/ABC/?img_index=0"):
            with self.subTest(url=url), self.assertRaises(InvalidReelUrl):
                canonicalize_instagram_url(url)


class PostExtractorTests(unittest.TestCase):
    def session(self, nodes):
        session = Mock()
        response = Mock(status_code=200, text=embed(nodes))
        response.raise_for_status.return_value = None
        session.get.return_value = response
        return session

    def test_extracts_distinct_slides_and_selected_index(self):
        session = self.session([node(1), node(2), node(3)])
        urls, selected = extract_post_images("https://instagram.com/p/ABC/?img_index=2", session=session)
        self.assertEqual(selected, 2)
        self.assertEqual(urls, [f"https://scontent.cdninstagram.com/image{i}.jpg" for i in range(1, 4)])

    def test_single_photo_embed_fixture(self):
        session = Mock()
        session.get.return_value = Mock(text='prefix display_url\\\\":\\\\"https:\\\\/\\\\/scontent.cdninstagram.com\\\\/single.jpg\\\\" suffix')
        urls, selected = extract_post_images("https://instagram.com/p/ABC/", session=session)
        self.assertEqual(urls, ["https://scontent.cdninstagram.com/single.jpg"])
        self.assertIsNone(selected)

    def test_rejects_out_of_range_selection(self):
        with self.assertRaises(PostExtractionError):
            extract_post_images("https://instagram.com/p/ABC/?img_index=4", session=self.session([node(1)]))

    def test_mixed_carousel_fixture_preserves_order(self):
        nodes = [node(1), {**node(2, video=True), "video_url": "https:\\/\\/scontent.cdninstagram.com\\/clip.mp4"}, node(3)]
        session = self.session(nodes)
        media, selected = extract_post_media("https://instagram.com/p/ABC/?img_index=2", session=session)
        self.assertEqual([item["kind"] for item in media], ["image", "video", "image"])
        self.assertEqual(selected, 2)
        self.assertEqual(media[1]["url"], "https://scontent.cdninstagram.com/clip.mp4")
        with self.assertRaisesRegex(PostExtractionError, "Mixed/video"):
            extract_post_images("https://instagram.com/p/ABC/?img_index=2", session=self.session(nodes))
        # Video media requires a separate, validated downloader and frame extraction.
        # Do not silently analyze its thumbnail as if it were the video.

    def test_rejects_video_and_untrusted_cdn(self):
        for bad in (node(1, video=True), node(1, url="https:\\/\\/cdninstagram.com.evil.test\\/x.jpg")):
            with self.subTest(bad=bad), self.assertRaises(PostExtractionError):
                extract_post_images("https://instagram.com/p/ABC/", session=self.session([bad]))

    @patch("reel_analyzer.post_download.extract_post_images")
    def test_downloads_images(self, extract):
        extract.return_value = (["https://scontent.cdninstagram.com/1.jpg", "https://scontent.cdninstagram.com/2.jpg"], 2)
        session = Mock()
        session.get.side_effect = [Mock(content=b"one", headers={"Content-Type": "image/jpeg"}),
                                   Mock(content=b"two", headers={"Content-Type": "image/jpeg"})]
        with tempfile.TemporaryDirectory() as tmp:
            paths, selected = download_post_images("https://instagram.com/p/ABC/", Path(tmp), session=session)
            self.assertEqual([p.read_bytes() for p in paths], [b"one", b"two"])
            self.assertEqual(selected, 2)



    @patch("reel_analyzer.reel_media._probe_duration", return_value=12.0)
    @patch("reel_analyzer.post_download.subprocess.run")
    @patch("reel_analyzer.post_download.extract_post_media")
    def test_mixed_media_download_and_frame(self, extract, run, probe):
        extract.return_value = (
            [{"kind": "image", "url": "https://scontent.cdninstagram.com/1.jpg"},
             {"kind": "video", "url": "https://scontent.cdninstagram.com/2.mp4"},
             {"kind": "image", "url": "https://scontent.cdninstagram.com/3.jpg"}], 2)
        session = Mock()
        responses = []
        for data, mime in ((b"photo1", "image/jpeg"), (b"video2", "video/mp4"), (b"photo3", "image/jpeg")):
            response = Mock(status_code=200, headers={"Content-Type": mime})
            response.iter_content.return_value = iter([data])
            responses.append(response)
        session.get.side_effect = responses

        def ffmpeg(args, **kwargs):
            Path(args[-1]).write_bytes(b"frame")
            return Mock(returncode=0)
        run.side_effect = ffmpeg
        with tempfile.TemporaryDirectory() as tmp:
            paths, selected, kinds = download_post_media("https://instagram.com/p/ABC/?img_index=2", Path(tmp), session=session)
            self.assertEqual(kinds, ["image", "video", "image"])
            self.assertEqual(selected, 2)
            self.assertEqual([p.read_bytes() for p in paths], [b"photo1", b"frame", b"photo3"])
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(run.call_count, 1)
            self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in session.get.call_args_list))

    @patch("reel_analyzer.post_download.extract_post_media")
    def test_rejects_oversized_video_stream(self, extract):
        extract.return_value = ([{"kind": "video", "url": "https://scontent.cdninstagram.com/2.mp4"}], None)
        response = Mock(status_code=200, headers={"Content-Type": "video/mp4"})
        response.iter_content.return_value = iter([b"x" * (50 * 1024 * 1024 + 1)])
        session = Mock()
        session.get.return_value = response
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(PostExtractionError, "size limit"):
            download_post_media("https://instagram.com/p/ABC/", Path(tmp), session=session)

    @patch("reel_analyzer.reel_media._probe_duration", return_value=12.0)
    @patch("reel_analyzer.post_download.subprocess.run", return_value=Mock(returncode=1))
    @patch("reel_analyzer.post_download.extract_post_media")
    def test_rejects_failed_video_frame_extraction(self, extract, run, probe):
        extract.return_value = ([{"kind": "video", "url": "https://scontent.cdninstagram.com/2.mp4"}], None)
        response = Mock(status_code=200, headers={"Content-Type": "video/mp4"})
        response.iter_content.return_value = iter([b"video"])
        session = Mock()
        session.get.return_value = response
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(PostExtractionError, "Could not extract frame"):
            download_post_media("https://instagram.com/p/ABC/", Path(tmp), session=session)

class PostVisionTests(unittest.TestCase):
    @patch("reel_analyzer.reel_vision.subprocess.run")
    def test_video_frame_is_labeled_and_selected(self, run):
        run.return_value = Mock(returncode=0, stdout=json.dumps({"outputs": [{"text": "Деталі кадру"}]}))
        result = describe_post_images([Path("/tmp/photo.jpg"), Path("/tmp/frame.jpg")],
                                      selected=2, kinds=["image", "video"])
        self.assertIn("Слайд 1 (фото)", result)
        self.assertIn("Слайд 2 (відео: один кадр) (обраний)", result)
        self.assertIn("not its full content or audio", run.call_args_list[1].args[0][run.call_args_list[1].args[0].index("--prompt") + 1])

    def test_rejects_mismatched_media_types(self):
        with self.assertRaises(ValueError):
            describe_post_images([Path("/tmp/photo.jpg")], kinds=["image", "video"])


class PostSummaryTests(unittest.TestCase):
    @patch("reel_analyzer.reel_analyzer.OpenAI")
    @patch.dict("os.environ", {"OPENAI_API_KEY": "test"})
    def test_summary_is_generated_from_slide_facts(self, client):
        choice = Mock(message=Mock(content="Публікація пояснює використання ШІ для програмування."))
        client.return_value.chat.completions.create.return_value = Mock(choices=[choice])
        result = summarize_post("Слайд 1: AI coding")
        self.assertIn("Публікація пояснює", result)
        kwargs = client.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["messages"][1]["content"], "Слайд 1: AI coding")
        self.assertIn("Do not list slides", kwargs["messages"][0]["content"])

    @patch("reel_analyzer.reel_analyzer.OpenAI", side_effect=RuntimeError("unavailable"))
    @patch.dict("os.environ", {"OPENAI_API_KEY": "test"})
    def test_summary_failure_does_not_abort(self, client):
        self.assertEqual(summarize_post("Слайд 1: text"), "")

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_key_falls_back(self):
        self.assertEqual(summarize_post("Слайд 1: text"), "")


class PostOrchestrationTests(unittest.TestCase):
    @patch("reel_analyzer.reel_analyzer.summarize_post", return_value="Змістовне резюме.")
    @patch("reel_analyzer.reel_analyzer.describe_post_images", return_value="Слайд 2")
    @patch("reel_analyzer.reel_analyzer.download_post_media", return_value=([Path("/tmp/1.jpg"), Path("/tmp/2.jpg")], 2, ["image", "video"]))
    def test_post_pipeline(self, download, vision, summary):
        result = ReelAnalyzer().analyze("https://instagram.com/p/ABC/?img_index=2")
        self.assertEqual(result["media_type"], "post")
        self.assertEqual(result["visual_facts"], "Слайд 2")
        self.assertEqual(result["transcript"], "")
        self.assertEqual(result["summary"], "Змістовне резюме.")
        summary.assert_called_once_with("Слайд 2")
        self.assertEqual(vision.call_args.kwargs["selected"], 2)
        self.assertEqual(vision.call_args.kwargs["kinds"], ["image", "video"])


if __name__ == "__main__":
    unittest.main()
