#!/usr/bin/env python3
"""Offline tests for the standard-library-only bot core."""

import unittest

import bot


class LinkAndMediaTests(unittest.TestCase):
    def test_extracts_supported_status_links(self):
        links = [
            "https://x.com/SpaceX/status/1049383091683319808",
            "https://twitter.com/SpaceX/status/1049383091683319808?s=20",
            "https://mobile.twitter.com/SpaceX/status/1049383091683319808/photo/1",
        ]
        for link in links:
            with self.subTest(link=link):
                self.assertEqual(bot.extract_status_id(link), "1049383091683319808")

    def test_rejects_non_status_links(self):
        self.assertIsNone(bot.extract_status_id("https://x.com/SpaceX"))
        self.assertIsNone(bot.extract_status_id("https://example.com/a/status/123"))

    def test_orders_unique_mp4_candidates_by_bitrate(self):
        video = {
            "url": "https://video.example/best.mp4?tag=1",
            "format": "video/mp4",
            "formats": [
                {"url": "https://video.example/stream.m3u8", "container": "m3u8"},
                {"url": "https://video.example/low.mp4", "container": "mp4", "bitrate": 256000},
                {"url": "https://video.example/best.mp4?tag=1", "container": "mp4", "bitrate": 2176000},
                {"url": "https://video.example/mid.mp4", "container": "mp4", "bitrate": 832000},
            ],
        }
        self.assertEqual(
            bot.video_candidates(video),
            [
                (2176000, "https://video.example/best.mp4?tag=1"),
                (832000, "https://video.example/mid.mp4"),
                (256000, "https://video.example/low.mp4"),
            ],
        )

    def test_extracts_only_video_media(self):
        status = {
            "media": {
                "videos": [{"type": "video", "url": "https://video.example/a.mp4"}],
                "all": [{"type": "photo", "url": "https://image.example/a.jpg"}],
            }
        }
        self.assertEqual(len(bot.extract_videos(status)), 1)
        self.assertEqual(bot.extract_videos(status)[0]["type"], "video")

    def test_parses_allowed_users(self):
        self.assertEqual(bot.parse_allowed_users("1, 20,300"), {1, 20, 300})
        self.assertEqual(bot.parse_allowed_users(""), set())
        with self.assertRaises(SystemExit):
            bot.parse_allowed_users("one")


if __name__ == "__main__":
    unittest.main(verbosity=2)
