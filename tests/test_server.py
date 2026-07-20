import gzip
import io
import json
import os
import threading
import time
import unittest
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import server


class FakeResponse:
    def getcode(self):
        return 200

    def close(self):
        return None


class FakeBytesResponse(FakeResponse):
    def __init__(self, body=b"", status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _size=-1):
        return self.body


class FakePlex:
    def __init__(self):
        self.xml_calls = []
        self.open_calls = []

    def xml(self, path, params=None):
        self.xml_calls.append((path, dict(params or {})))
        if path == "/library/sections":
            return ET.fromstring(
                '<MediaContainer size="1"><Directory key="7" title="Movies" type="movie" /></MediaContainer>'
            )
        if path.endswith("/genre"):
            return ET.fromstring(
                '<MediaContainer size="2">'
                '<Directory key="22" title="Drama" />'
                '<Directory key="11" title="Action" />'
                '</MediaContainer>'
            )
        if path.endswith("/collections"):
            return ET.fromstring(
                '<MediaContainer size="2" totalSize="9">'
                '<Directory ratingKey="101" key="/library/collections/101/children" type="collection" '
                'title="A Collection" childCount="4" thumb="/library/collections/101/composite/1?width=400&amp;height=600" />'
                '<Directory ratingKey="102" key="/library/collections/102/children" type="collection" '
                'title="B Collection" childCount="7" />'
                '</MediaContainer>'
            )
        if path.endswith("/all"):
            index = int((params or {}).get("X-Plex-Container-Start", 0))
            return ET.fromstring(
                f'<MediaContainer size="1" totalSize="3">'
                f'<Video ratingKey="{100 + index}" type="movie" title="Pick {index}" duration="600000">'
                f'<Media videoCodec="h264" audioCodec="aac"><Part key="/library/parts/{100 + index}/file.mp4" /></Media>'
                f'</Video></MediaContainer>'
            )
        if path.startswith("/library/metadata/"):
            rating_keys = path.rsplit("/", 1)[-1].split(",")
            videos = "".join(
                f'<Video ratingKey="{rating_key}" librarySectionID="{8 if rating_key == "202" else 7}" '
                f'type="movie" title="Test {rating_key}" duration="600000" />'
                for rating_key in rating_keys
            )
            return ET.fromstring(
                f'<MediaContainer size="{len(rating_keys)}">{videos}</MediaContainer>'
            )
        if path.endswith("/onDeck"):
            return ET.fromstring(
                '<MediaContainer size="2" totalSize="2">'
                '<Video ratingKey="41" type="movie" title="Continue" duration="600000" viewOffset="120000" />'
                '<Video ratingKey="42" type="movie" title="Finished" duration="600000" viewCount="1" />'
                '</MediaContainer>'
            )
        return ET.fromstring('<MediaContainer size="0" totalSize="0" />')

    def open(self, path, params=None, **kwargs):
        self.open_calls.append((path, dict(params or {}), dict(kwargs)))
        return FakeResponse()


class FakeEpisodePlex(FakePlex):
    def xml(self, path, params=None):
        self.xml_calls.append((path, dict(params or {})))
        if path == "/library/metadata/12":
            return ET.fromstring(
                '<MediaContainer size="1">'
                '<Video ratingKey="12" type="episode" title="Season Finale" index="2" parentIndex="1" '
                'parentRatingKey="20" grandparentRatingKey="10" grandparentTitle="Test Show" />'
                '</MediaContainer>'
            )
        if path == "/library/metadata/13":
            return ET.fromstring(
                '<MediaContainer size="1">'
                '<Video ratingKey="13" type="episode" title="New Season" index="1" parentIndex="2" '
                'parentRatingKey="21" grandparentRatingKey="10" grandparentTitle="Test Show" />'
                '</MediaContainer>'
            )
        if path == "/library/metadata/10/allLeaves":
            return ET.fromstring(
                '<MediaContainer size="3" librarySectionID="7">'
                '<Video ratingKey="11" type="episode" title="Pilot" index="1" parentIndex="1" '
                'parentRatingKey="20" grandparentRatingKey="10" grandparentTitle="Test Show" />'
                '<Video ratingKey="12" type="episode" title="Season Finale" index="2" parentIndex="1" '
                'parentRatingKey="20" grandparentRatingKey="10" grandparentTitle="Test Show" />'
                '<Video ratingKey="13" type="episode" title="New Season" index="1" parentIndex="2" '
                'parentRatingKey="21" grandparentRatingKey="10" grandparentTitle="Test Show" />'
                '</MediaContainer>'
            )
        return super().xml(path, params=params)


class FakeSubtitleSelectionPlex(FakePlex):
    def xml(self, path, params=None):
        self.xml_calls.append((path, dict(params or {})))
        if path == "/library/metadata/801":
            return ET.fromstring(
                '<MediaContainer size="1">'
                '<Video ratingKey="801" type="movie" title="Subtitle Test">'
                '<Media><Part id="901" key="/library/parts/901/file.mkv">'
                '<Stream id="1001" streamType="3" codec="srt" languageCode="ell" />'
                '<Stream id="1002" streamType="3" codec="srt" languageCode="eng" />'
                '</Part></Media></Video></MediaContainer>'
            )
        return super().xml(path, params=params)


class FakeCollectionPlex(FakePlex):
    def __init__(self, member=False):
        super().__init__()
        self.member_keys = {"101"} if member else set()
        self.collections = [
            {"ratingKey": "101", "title": "Manual Picks", "childCount": 4, "smart": False},
            {"ratingKey": "102", "title": "Automatic Picks", "childCount": 8, "smart": True},
        ]

    def xml(self, path, params=None):
        self.xml_calls.append((path, dict(params or {})))
        if path == "/":
            return ET.fromstring('<MediaContainer machineIdentifier="machine-123" />')
        if path == "/library/metadata/501":
            tags = "".join(
                f'<Collection id="tag-{item["ratingKey"]}" tag="{item["title"]}" />'
                for item in self.collections
                if item["ratingKey"] in self.member_keys
            )
            return ET.fromstring(
                '<MediaContainer size="1">'
                '<Video ratingKey="501" librarySectionID="7" type="movie" title="Collection Test">'
                f'{tags}</Video></MediaContainer>'
            )
        if path == "/library/metadata/601":
            return ET.fromstring(
                '<MediaContainer size="1">'
                '<Video ratingKey="601" librarySectionID="7" type="episode" title="Not a movie" />'
                '</MediaContainer>'
            )
        if path == "/library/sections/7/collections":
            directories = "".join(
                '<Directory ratingKey="{ratingKey}" key="/library/collections/{ratingKey}/children" '
                'type="collection" subtype="movie" title="{title}" childCount="{childCount}"{smart_attr} />'.format(
                    ratingKey=item["ratingKey"],
                    title=item["title"],
                    childCount=item["childCount"],
                    smart_attr=' smart="1"' if item["smart"] else "",
                )
                for item in self.collections
            )
            return ET.fromstring(
                f'<MediaContainer size="{len(self.collections)}">{directories}</MediaContainer>'
            )
        return ET.fromstring('<MediaContainer size="0" />')

    def open(self, path, params=None, **kwargs):
        response = super().open(path, params=params, **kwargs)
        method = kwargs.get("method")
        if path == "/library/collections/101/items" and method == "PUT":
            self.member_keys.add("101")
        elif path == "/library/collections/101/items/501" and method == "DELETE":
            self.member_keys.discard("101")
        elif path == "/library/collections" and method == "POST":
            self.collections.append(
                {
                    "ratingKey": "103",
                    "title": params["title"],
                    "childCount": 1,
                    "smart": False,
                }
            )
            self.member_keys.add("103")
        elif path == "/library/sections/7/all" and method == "PUT":
            target = next(item for item in self.collections if item["ratingKey"] == params["id"])
            target["title"] = params["title.value"]
        elif path.startswith("/library/collections/") and method == "DELETE":
            rating_key = path.rsplit("/", 1)[-1]
            self.collections = [item for item in self.collections if item["ratingKey"] != rating_key]
            self.member_keys.discard(rating_key)
        return response


class FakeMatchPlex(FakePlex):
    def __init__(self):
        super().__init__()
        self.current_guid = "plex://movie/current123"
        self.current_title = "Wrong Movie"
        self.current_year = "1999"
        self.current_thumb = "/library/metadata/701/thumb/1"

    def xml(self, path, params=None):
        self.xml_calls.append((path, dict(params or {})))
        if path == "/library/sections":
            return ET.fromstring(
                '<MediaContainer size="2"><Directory key="7" title="Movies" type="movie" '
                'agent="tv.plex.agents.movie" scanner="Plex Movie" language="en-US" />'
                '<Directory key="8" title="Shows" type="show" agent="tv.plex.agents.series" '
                'scanner="Plex TV Series" language="en-US" />'
                '</MediaContainer>'
            )
        if path == "/library/metadata/701":
            return ET.fromstring(
                '<MediaContainer size="1">'
                f'<Video ratingKey="701" librarySectionID="7" type="movie" guid="{self.current_guid}" '
                f'title="{self.current_title}" year="{self.current_year}" thumb="{self.current_thumb}" '
                f'summary="Current summary">'
                '<Media videoCodec="h264" audioCodec="aac"><Part key="/library/parts/701/file.mp4" /></Media>'
                '</Video></MediaContainer>'
            )
        if path == "/library/metadata/702":
            return ET.fromstring(
                '<MediaContainer size="1"><Video ratingKey="702" librarySectionID="7" '
                'type="episode" title="Episode" /></MediaContainer>'
            )
        if path == "/library/metadata/703":
            return ET.fromstring(
                '<MediaContainer size="1"><Directory ratingKey="703" librarySectionID="8" '
                'type="show" guid="plex://show/current789" title="Wrong Show" year="2010" />'
                '</MediaContainer>'
            )
        if path == "/library/metadata/701/matches":
            return ET.fromstring(
                '<MediaContainer size="2">'
                '<SearchResult guid="plex://movie/correct456" name="Correct Movie" year="2024" '
                'type="movie" summary="Correct summary" thumb="https://images.plex.tv/poster.jpg" />'
                '<SearchResult guid="plex://movie/current123" name="Wrong Movie" year="1999" '
                'type="movie" summary="Current summary" />'
                '</MediaContainer>'
            )
        if path == "/library/metadata/703/matches":
            return ET.fromstring(
                '<MediaContainer size="1"><SearchResult guid="plex://show/correct987" '
                'name="Correct Show" year="2011" type="show" summary="Correct show summary" />'
                '</MediaContainer>'
            )
        return ET.fromstring('<MediaContainer size="0" />')

    def open(self, path, params=None, **kwargs):
        self.open_calls.append((path, dict(params or {}), dict(kwargs)))
        if path == "/library/metadata/701/match" and kwargs.get("method") == "PUT":
            self.current_guid = params["guid"]
            self.current_title = params["name"]
            self.current_year = str(params.get("year") or "")
        elif path == "/library/metadata/701/posters" and kwargs.get("method") == "POST":
            self.current_thumb = "/library/metadata/701/thumb/2"
        return FakeResponse()


def handler_with_payload(payload):
    server.API_CACHE.clear()
    handler = object.__new__(server.AppHandler)
    responses = []
    handler.read_json = lambda: payload
    handler.send_json = lambda body, status=200, headers=None, **kwargs: responses.append((status, body))
    return handler, responses


class PerformancePathTests(unittest.TestCase):
    def setUp(self):
        server.API_CACHE.clear()

    def test_image_urls_request_right_sized_cacheable_artwork(self):
        url = server.image_url("/library/metadata/42/thumb/123")
        raw_path = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["path"][0]
        path, params = server.plex_image_request(raw_path)

        self.assertEqual("/library/metadata/42/thumb/123", path)
        self.assertEqual(str(server.POSTER_WIDTH), params["width"])
        self.assertEqual(str(server.POSTER_HEIGHT), params["height"])
        self.assertEqual("0", params["upscale"])
        upstream_path, upstream_params = server.plex_image_upstream_request(raw_path)
        self.assertEqual("/photo/:/transcode", upstream_path)
        self.assertEqual("/library/metadata/42/thumb/123", upstream_params["url"])

    def test_browse_items_skip_expensive_saved_status_and_guid_work(self):
        root = ET.fromstring(
            '<MediaContainer><Video ratingKey="42" type="movie" title="Fast">'
            '<Guid id="imdb://tt123" />'
            '<Media><Part key="/library/parts/42/file.mp4" /></Media>'
            '</Video></MediaContainer>'
        )
        with mock.patch.object(server, "saved_playback_status") as saved_status:
            item = server.items_from_container(root)[0]

        saved_status.assert_not_called()
        self.assertEqual({"state": "unknown", "ready": False}, item["savedPlayback"])
        self.assertEqual([], item["guids"])

    def test_result_cache_coalesces_concurrent_identical_loads(self):
        cache = server.TimedResultCache()
        barrier = threading.Barrier(6)
        lock = threading.Lock()
        calls = 0
        results = []

        def loader():
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.04)
            return {"ok": True}

        def worker():
            barrier.wait()
            results.append(cache.get_or_load("same", 1.0, loader))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)

        self.assertEqual(1, calls)
        self.assertEqual([{"ok": True}] * 6, results)

    def test_large_json_responses_are_gzipped_when_supported(self):
        handler = object.__new__(server.AppHandler)
        handler.headers = {"Accept-Encoding": "gzip, deflate"}
        handler.command = "GET"
        handler.wfile = io.BytesIO()
        response_headers = {}
        handler.send_response = lambda status: None
        handler.send_header = lambda key, value: response_headers.__setitem__(key, value)
        handler.end_headers = lambda: None

        handler.send_json({"items": ["x" * 3000]})

        self.assertEqual("gzip", response_headers["Content-Encoding"])
        self.assertEqual({"items": ["x" * 3000]}, json.loads(gzip.decompress(handler.wfile.getvalue())))

    def test_bootstrap_combines_server_libraries_and_my_list(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        handler.is_authenticated = lambda: True
        with mock.patch.object(server, "PLEX", plex), mock.patch.object(
            server, "my_list_keys", return_value=["101"]
        ):
            handler.api_bootstrap("GET", {})

        self.assertEqual(200, responses[0][0])
        self.assertEqual("0.20.0", responses[0][1]["version"])
        self.assertTrue(responses[0][1]["authenticated"])
        self.assertEqual(["101"], responses[0][1]["ratingKeys"])
        self.assertEqual("Movies", responses[0][1]["libraries"][0]["title"])
        self.assertEqual(["/library/sections", "/"], [call[0] for call in plex.xml_calls])

    def test_bootstrap_returns_auth_state_without_touching_plex_when_signed_out(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        handler.is_authenticated = lambda: False
        with mock.patch.object(server, "PLEX", plex):
            handler.api_bootstrap("GET", {"includeBrowse": ["1"]})

        self.assertEqual(200, responses[0][0])
        self.assertFalse(responses[0][1]["authenticated"])
        self.assertEqual("0.20.0", responses[0][1]["version"])
        self.assertEqual([], plex.xml_calls)


class PlaybackCompatibilityTests(unittest.TestCase):
    def tearDown(self):
        with server.PLEX_HLS_SESSIONS_LOCK:
            server.PLEX_HLS_SESSIONS.clear()

    def test_hevc_video_and_eac3_audio_use_the_full_compatibility_stream(self):
        playback = server.playback_info(
            "/library/parts/42/file.mkv",
            {"videoCodec": "hevc", "audioCodec": "eac3"},
        )

        self.assertTrue(playback["compatibilityTranscodeRequired"])
        self.assertTrue(playback["videoTranscodeRequired"])
        self.assertTrue(playback["audioTranscodeRequired"])
        self.assertIn("video=h264", playback["compatibleStreamUrl"])

    def test_hevc_video_with_aac_audio_still_uses_the_compatibility_stream(self):
        playback = server.playback_info(
            "/library/parts/42/file.mkv",
            {"videoCodec": "hevc", "audioCodec": "aac"},
        )

        self.assertTrue(playback["compatibilityTranscodeRequired"])
        self.assertTrue(playback["videoTranscodeRequired"])
        self.assertFalse(playback["audioTranscodeRequired"])
        self.assertIn("video=h264", playback["compatibleStreamUrl"])

    def test_compatibility_stream_carries_rating_key_for_seekable_vod(self):
        playback = server.playback_info(
            "/library/parts/42/file.mkv",
            {"videoCodec": "h264", "audioCodec": "ac3"},
            "701",
        )

        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(playback["compatibleStreamUrl"]).query
        )
        self.assertEqual(["701"], query["ratingKey"])

    def test_live_audio_transcode_uses_a_safari_compatible_mp4_header(self):
        with mock.patch.object(server.PLEX, "_url", return_value="http://plex/media"):
            command = server.compatible_stream_command("/library/parts/42/file.mkv")

        movflags = command[command.index("-movflags") + 1]
        self.assertIn("delay_moov", movflags)
        self.assertNotIn("empty_moov", movflags)
        self.assertEqual("copy", command[command.index("-c:v") + 1])
        self.assertEqual("aac", command[command.index("-c:a") + 1])
        self.assertEqual("pipe:1", command[-1])

    def test_remote_compatible_stream_still_transcodes_video_to_480p(self):
        with mock.patch.object(server.PLEX, "_url", return_value="http://plex/media"):
            command = server.compatible_stream_command("/library/parts/42/file.mkv", True)

        self.assertEqual("scale=-2:480", command[command.index("-vf") + 1])
        self.assertEqual("libx264", command[command.index("-c:v") + 1])
        self.assertEqual("96k", command[command.index("-b:a") + 1])

    def test_hls_stream_uses_bounded_event_segments_for_native_safari_playback(self):
        with mock.patch.object(server.PLEX, "_url", return_value="http://plex/media"):
            command = server.hls_stream_command(
                "/library/parts/42/file.mkv",
                Path("/tmp/hls-test"),
            )

        self.assertEqual("copy", command[command.index("-c:v") + 1])
        self.assertEqual("aac", command[command.index("-c:a") + 1])
        self.assertEqual("4", command[command.index("-hls_time") + 1])
        self.assertEqual("event", command[command.index("-hls_playlist_type") + 1])
        self.assertEqual("mpegts", command[command.index("-hls_segment_type") + 1])
        self.assertIn("temp_file", command[command.index("-hls_flags") + 1])

    def test_hls_stream_converts_unsupported_video_to_browser_safe_h264(self):
        with mock.patch.object(server.PLEX, "_url", return_value="http://plex/media"):
            command = server.hls_stream_command(
                "/library/parts/42/file.mkv",
                Path("/tmp/hls-test"),
                transcode_video=True,
            )

        self.assertEqual("libx264", command[command.index("-c:v") + 1])
        self.assertEqual("yuv420p", command[command.index("-pix_fmt") + 1])
        self.assertEqual("aac", command[command.index("-c:a") + 1])

    def test_hls_cache_separates_copied_and_transcoded_video(self):
        copied = server.hls_stream_id("/library/parts/42/file.mkv")
        transcoded = server.hls_stream_id(
            "/library/parts/42/file.mkv",
            transcode_video=True,
        )

        self.assertNotEqual(copied, transcoded)

    def test_hls_manifest_rewrites_only_valid_segment_paths(self):
        raw = "#EXTM3U\n#EXTINF:4.0,\nsegment-00000.ts\n#EXT-X-ENDLIST\n"
        manifest = server.hls_manifest_text("a" * 24, raw)

        self.assertIn("#EXT-X-START:TIME-OFFSET=0,PRECISE=YES", manifest)
        self.assertIn("/api/hls-segment?id=" + "a" * 24, manifest)
        self.assertIn("name=segment-00000.ts", manifest)
        with self.assertRaises(ValueError):
            server.hls_manifest_text("a" * 24, "#EXTM3U\n../outside.ts\n")

    def test_plex_hls_profile_requests_timestamp_aligned_h264_aac_vod(self):
        params = server.plex_hls_transcode_params(
            "701",
            "a" * 32,
            media_index=2,
            part_index=1,
        )

        self.assertEqual("/library/metadata/701", params["path"])
        self.assertEqual("2", params["mediaIndex"])
        self.assertEqual("1", params["partIndex"])
        self.assertEqual("hls", params["protocol"])
        self.assertEqual("1", params["fastSeek"])
        self.assertEqual("0", params["directStream"])
        self.assertEqual("0", params["directStreamAudio"])
        self.assertEqual("aac", params["audioCodec"])
        self.assertEqual("none", params["subtitles"])

    def test_media_part_indices_follow_the_selected_part(self):
        item = ET.fromstring(
            '<Video ratingKey="701">'
            '<Media><Part key="/library/parts/1/first.mkv" /></Media>'
            '<Media><Part key="/library/parts/2/second.mkv" /></Media>'
            '</Video>'
        )

        self.assertEqual(
            (1, 0),
            server.media_part_indices(item, "/library/parts/2/second.mkv"),
        )

    def test_plex_hls_manifests_are_rewritten_as_complete_vod(self):
        session_id = "a" * 32
        master, variants = server.plex_hls_master_text(
            session_id,
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\n"
            f"session/{session_id}/base/index.m3u8?X-Plex-Incomplete-Segments=1\n",
        )
        playlist = server.plex_hls_playlist_text(
            session_id,
            "base",
            "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10, nodesc\n"
            "00000.ts\n#EXT-X-ENDLIST\n",
        )

        self.assertEqual({"base"}, variants)
        self.assertIn("/api/plex-hls-playlist?id=", master)
        self.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", playlist)
        self.assertIn("/api/plex-hls-segment?id=", playlist)
        self.assertIn("name=00000.ts", playlist)
        self.assertIn("#EXT-X-ENDLIST", playlist)
        with self.assertRaises(RuntimeError):
            server.plex_hls_playlist_text(
                session_id,
                "base",
                "#EXTM3U\n#EXTINF:10,\n00000.ts\n",
            )
        with self.assertRaises(ValueError):
            server.plex_hls_playlist_text(
                session_id,
                "base",
                "#EXTM3U\n#EXTINF:10,\n../outside.ts\n#EXT-X-ENDLIST\n",
            )

    def test_plex_hls_session_runs_decision_before_start_and_reuses_result(self):
        class FakePlexHls:
            def __init__(self):
                self.calls = []

            def open(self, path, params=None, **kwargs):
                self.calls.append((path, dict(params or {}), dict(kwargs)))
                if path.endswith("/decision"):
                    return FakeBytesResponse(b'<MediaContainer transcodeDecisionCode="1001" />')
                if path.endswith("/start.m3u8"):
                    session_id = params["session"]
                    return FakeBytesResponse(
                        (
                            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\n"
                            f"session/{session_id}/base/index.m3u8\n"
                        ).encode()
                    )
                return FakeBytesResponse()

        plex = FakePlexHls()
        session_id = "b" * 32
        with mock.patch.object(server, "PLEX", plex):
            first = server.ensure_plex_hls_session(session_id, "701")
            second = server.ensure_plex_hls_session(session_id, "701")
            stopped = server.stop_plex_hls_session(session_id)

        self.assertIs(first, second)
        self.assertTrue(stopped)
        self.assertEqual(
            [
                "/video/:/transcode/universal/decision",
                "/video/:/transcode/universal/start.m3u8",
                "/video/:/transcode/universal/stop",
            ],
            [call[0] for call in plex.calls],
        )


class MediaMatchTests(unittest.TestCase):
    def setUp(self):
        server.API_CACHE.clear()

    def test_search_returns_ranked_plex_candidates_with_current_match(self):
        plex = FakeMatchPlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_media_match(
                "GET",
                {
                    "ratingKey": ["701"],
                    "title": ["Correct Movie"],
                    "year": ["2024"],
                    "language": ["en-US"],
                },
            )

        self.assertEqual(200, responses[0][0])
        payload = responses[0][1]
        self.assertEqual("plex://movie/current123", payload["currentGuid"])
        self.assertEqual(2, len(payload["results"]))
        self.assertTrue(payload["results"][0]["best"])
        self.assertFalse(payload["results"][0]["current"])
        self.assertTrue(payload["results"][1]["current"])
        self.assertEqual("https://images.plex.tv/poster.jpg", payload["results"][0]["posterUrl"])
        self.assertTrue(payload["results"][0]["posterCanApply"])
        _, params = next(call for call in plex.xml_calls if call[0].endswith("/matches"))
        self.assertEqual(1, params["manual"])
        self.assertEqual("Correct Movie", params["title"])
        self.assertEqual(2024, params["year"])
        self.assertEqual("tv.plex.agents.movie", params["agent"])
        self.assertEqual("en-US", params["language"])

    def test_apply_uses_selected_guid_and_returns_refreshed_metadata(self):
        plex = FakeMatchPlex()
        handler, responses = handler_with_payload(
            {
                "ratingKey": "701",
                "guid": "plex://movie/correct456",
                "name": "Correct Movie",
                "year": 2024,
            }
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_media_match("POST", {})

        self.assertEqual(200, responses[0][0])
        payload = responses[0][1]
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["pending"])
        self.assertEqual("plex://movie/correct456", payload["item"]["guid"])
        self.assertEqual("Correct Movie", payload["item"]["title"])
        path, params, kwargs = plex.open_calls[0]
        self.assertEqual("/library/metadata/701/match", path)
        self.assertEqual("plex://movie/correct456", params["guid"])
        self.assertEqual("Correct Movie", params["name"])
        self.assertEqual(2024, params["year"])
        self.assertEqual("PUT", kwargs["method"])

    def test_apply_rejects_a_match_for_the_wrong_media_type(self):
        plex = FakeMatchPlex()
        handler, responses = handler_with_payload(
            {
                "ratingKey": "701",
                "guid": "plex://show/wrongtype",
                "name": "Wrong Type",
                "year": 2024,
            }
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_media_match("POST", {})

        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_match", responses[0][1]["error"])
        self.assertEqual([], plex.open_calls)

    def test_apply_poster_changes_only_artwork_from_a_trusted_plex_result(self):
        plex = FakeMatchPlex()
        handler, responses = handler_with_payload(
            {
                "ratingKey": "701",
                "posterUrl": "https://images.plex.tv/poster.jpg",
            }
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_media_poster("POST")

        self.assertEqual(200, responses[0][0])
        payload = responses[0][1]
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["pending"])
        self.assertEqual("plex://movie/current123", payload["item"]["guid"])
        self.assertEqual("Wrong Movie", payload["item"]["title"])
        self.assertIn("thumb%2F2", payload["item"]["posterUrl"])
        path, params, kwargs = plex.open_calls[0]
        self.assertEqual("/library/metadata/701/posters", path)
        self.assertEqual("https://images.plex.tv/poster.jpg", params["url"])
        self.assertEqual("POST", kwargs["method"])

    def test_apply_poster_rejects_an_untrusted_artwork_host(self):
        plex = FakeMatchPlex()
        handler, responses = handler_with_payload(
            {
                "ratingKey": "701",
                "posterUrl": "https://images.plex.tv.example.com/poster.jpg",
            }
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_media_poster("POST")

        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_poster_url", responses[0][1]["error"])
        self.assertEqual([], plex.open_calls)

    def test_episode_matching_is_rejected_at_the_api_boundary(self):
        plex = FakeMatchPlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_media_match("GET", {"ratingKey": ["702"], "title": ["Episode"]})

        self.assertEqual(400, responses[0][0])
        self.assertEqual("unsupported_media_type", responses[0][1]["error"])

    def test_tv_show_search_uses_the_series_agent(self):
        plex = FakeMatchPlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_media_match(
                "GET",
                {"ratingKey": ["703"], "title": ["Correct Show"], "year": ["2011"]},
            )

        self.assertEqual(200, responses[0][0])
        self.assertEqual("show", responses[0][1]["type"])
        self.assertEqual("plex://show/correct987", responses[0][1]["results"][0]["guid"])
        _, params = next(call for call in plex.xml_calls if call[0].endswith("/matches"))
        self.assertEqual("tv.plex.agents.series", params["agent"])


class LibraryViewTests(unittest.TestCase):
    def test_library_genres_are_returned_in_title_order(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_library_genres("/api/library/7/genres")

        self.assertEqual(200, responses[0][0])
        self.assertEqual(
            [{"key": "11", "title": "Action"}, {"key": "22", "title": "Drama"}],
            responses[0][1]["genres"],
        )
        self.assertEqual("/library/sections/7/genre", plex.xml_calls[0][0])

    def test_library_view_forwards_a_valid_genre_filter(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_library(
                "/api/library/7",
                {"view": ["all"], "sort": ["titleSort"], "genre": ["11"]},
            )

        self.assertEqual(200, responses[0][0])
        self.assertEqual("11", responses[0][1]["genre"])
        path, params = plex.xml_calls[0]
        self.assertEqual("/library/sections/7/all", path)
        self.assertEqual("11", params["genre"])
        self.assertEqual("titleSort", params["sort"])
        self.assertNotIn("includeGuids", params)

    def test_browse_bundle_loads_filters_and_first_page_together(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_browse(
                "/api/browse/7",
                {"view": ["all"], "sort": ["addedAt:desc"], "start": ["0"], "limit": ["24"]},
            )

        self.assertEqual(200, responses[0][0])
        self.assertEqual("7", responses[0][1]["library"])
        self.assertEqual("Action", responses[0][1]["genres"][0]["title"])
        self.assertEqual("Pick 0", responses[0][1]["page"]["items"][0]["title"])
        self.assertEqual(
            {"/library/sections/7/genre", "/library/sections/7/all"},
            {call[0] for call in plex.xml_calls},
        )

    def test_library_view_rejects_an_invalid_genre_filter(self):
        handler, responses = handler_with_payload({})
        handler.api_library("/api/library/7", {"genre": ["../11"]})

        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_genre", responses[0][1]["error"])

    def test_continue_view_uses_on_deck_and_excludes_watched_items(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_library(
                "/api/library/7",
                {"view": ["continue"], "sort": ["addedAt:desc"], "start": ["2"], "limit": ["5"]},
            )

        self.assertEqual(200, responses[0][0])
        self.assertEqual("continue", responses[0][1]["view"])
        self.assertEqual(["41"], [item["ratingKey"] for item in responses[0][1]["items"]])
        self.assertEqual(1, responses[0][1]["totalSize"])
        path, params = plex.xml_calls[0]
        self.assertEqual("/library/sections/7/onDeck", path)
        self.assertEqual(0, params["X-Plex-Container-Start"])
        self.assertEqual(300, params["X-Plex-Container-Size"])
        self.assertNotIn("sort", params)

    def test_collections_view_uses_native_collection_directory(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_library(
                "/api/library/7",
                {
                    "view": ["collections"],
                    "sort": ["year:desc"],
                    "genre": ["11"],
                    "start": ["0"],
                    "limit": ["2"],
                },
            )

        self.assertEqual(200, responses[0][0])
        self.assertEqual("collections", responses[0][1]["view"])
        self.assertEqual(9, responses[0][1]["totalSize"])
        self.assertEqual("collection", responses[0][1]["items"][0]["type"])
        self.assertEqual(4, responses[0][1]["items"][0]["childCount"])
        path, params = plex.xml_calls[0]
        self.assertEqual("/library/sections/7/collections", path)
        self.assertEqual("titleSort", params["sort"])
        self.assertEqual(2, params["X-Plex-Container-Size"])
        self.assertNotIn("genre", params)
        self.assertIsNone(responses[0][1]["genre"])

    def test_collection_composite_image_query_is_forwarded_safely(self):
        path, params = server.plex_image_request(
            "/library/collections/101/composite/1?width=400&height=600&X-Plex-Token=ignored"
        )

        self.assertEqual("/library/collections/101/composite/1", path)
        self.assertEqual({"width": "400", "height": "600"}, params)

    def test_random_item_uses_a_single_random_library_offset(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex), mock.patch.object(server.secrets, "randbelow", return_value=2):
            handler.api_random_item({"sectionKey": ["7"]})

        self.assertEqual(200, responses[0][0])
        self.assertEqual(3, responses[0][1]["totalSize"])
        self.assertEqual(2, responses[0][1]["offset"])
        self.assertEqual("102", responses[0][1]["item"]["ratingKey"])
        self.assertEqual("/api/stream?partKey=%2Flibrary%2Fparts%2F102%2Ffile.mp4", responses[0][1]["item"]["streamUrl"])
        self.assertEqual("/library/sections", plex.xml_calls[0][0])
        self.assertEqual(0, plex.xml_calls[1][1]["X-Plex-Container-Start"])
        self.assertEqual(2, plex.xml_calls[2][1]["X-Plex-Container-Start"])

    def test_random_item_rejects_an_invalid_library_key(self):
        handler, responses = handler_with_payload({})
        handler.api_random_item({"sectionKey": ["../7"]})

        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_section", responses[0][1]["error"])

    def test_random_item_honors_genre_and_unwatched_filters(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex), mock.patch.object(server.secrets, "randbelow", return_value=1):
            handler.api_random_item({"sectionKey": ["7"], "genre": ["11"], "unwatched": ["true"]})

        self.assertEqual(200, responses[0][0])
        self.assertEqual("11", responses[0][1]["genre"])
        self.assertTrue(responses[0][1]["unwatched"])
        for _, params in plex.xml_calls[1:]:
            self.assertEqual("11", params["genre"])
            self.assertEqual("1", params["unwatched"])


class WatchStateTests(unittest.TestCase):
    def test_mark_watched_calls_scrobble(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({"ratingKey": "42", "watched": True})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_watch_state("POST")

        self.assertEqual(200, responses[0][0])
        self.assertTrue(responses[0][1]["watched"])
        self.assertEqual(1, responses[0][1]["item"]["viewCount"])
        self.assertEqual("/:/scrobble", plex.open_calls[0][0])

    def test_mark_unwatched_calls_unscrobble_and_clears_progress(self):
        plex = FakePlex()
        handler, responses = handler_with_payload({"ratingKey": "42", "watched": False})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_watch_state("POST")

        self.assertEqual(200, responses[0][0])
        self.assertFalse(responses[0][1]["watched"])
        self.assertEqual(0, responses[0][1]["item"]["viewCount"])
        self.assertEqual(0, responses[0][1]["item"]["viewOffset"])
        self.assertEqual("/:/unscrobble", plex.open_calls[0][0])

    def test_rejects_non_boolean_state(self):
        handler, responses = handler_with_payload({"ratingKey": "42", "watched": "yes"})
        handler.api_watch_state("POST")

        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_watched_state", responses[0][1]["error"])


class SubtitleSelectionTests(unittest.TestCase):
    def setUp(self):
        server.API_CACHE.clear()

    def test_selects_a_valid_subtitle_stream_for_the_item_part(self):
        plex = FakeSubtitleSelectionPlex()
        handler, responses = handler_with_payload(
            {"ratingKey": "801", "partId": "901", "streamId": "1002"}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_subtitle_selection("POST")

        self.assertEqual(200, responses[0][0])
        self.assertEqual("1002", responses[0][1]["streamId"])
        self.assertFalse(responses[0][1]["off"])
        path, params, kwargs = plex.open_calls[0]
        self.assertEqual("/library/parts/901", path)
        self.assertEqual({"subtitleStreamID": "1002", "allParts": "1"}, params)
        self.assertEqual("PUT", kwargs["method"])

    def test_persists_an_explicit_subtitles_off_choice(self):
        plex = FakeSubtitleSelectionPlex()
        handler, responses = handler_with_payload(
            {"ratingKey": "801", "partId": "901", "streamId": "0"}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_subtitle_selection("POST")

        self.assertEqual(200, responses[0][0])
        self.assertTrue(responses[0][1]["off"])
        self.assertEqual("0", plex.open_calls[0][1]["subtitleStreamID"])

    def test_rejects_a_subtitle_stream_outside_the_item_part(self):
        plex = FakeSubtitleSelectionPlex()
        handler, responses = handler_with_payload(
            {"ratingKey": "801", "partId": "901", "streamId": "9999"}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_subtitle_selection("POST")

        self.assertEqual(400, responses[0][0])
        self.assertEqual("subtitle_stream_not_found", responses[0][1]["error"])
        self.assertEqual([], plex.open_calls)


class CollectionMembershipTests(unittest.TestCase):
    def test_lists_manual_and_read_only_smart_collections(self):
        plex = FakeCollectionPlex(member=True)
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_collection_membership("GET", {"ratingKey": ["501"]})

        self.assertEqual(200, responses[0][0])
        self.assertEqual(1, responses[0][1]["memberCount"])
        automatic, manual = responses[0][1]["collections"]
        self.assertEqual("Automatic Picks", automatic["title"])
        self.assertFalse(automatic["editable"])
        self.assertEqual("Manual Picks", manual["title"])
        self.assertTrue(manual["member"])
        self.assertEqual([{"id": "tag-101", "tag": "Manual Picks"}], responses[0][1]["item"]["collections"])

    def test_adds_movie_by_native_collection_id_and_refreshes_membership(self):
        plex = FakeCollectionPlex()
        handler, responses = handler_with_payload(
            {"ratingKey": "501", "collectionRatingKey": "101", "member": True}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_collection_membership("POST", {})

        self.assertEqual(200, responses[0][0])
        self.assertEqual(1, responses[0][1]["memberCount"])
        path, params, kwargs = plex.open_calls[0]
        self.assertEqual("/library/collections/101/items", path)
        self.assertEqual("PUT", kwargs["method"])
        self.assertEqual(
            "server://machine-123/com.plexapp.plugins.library/library/metadata/501",
            params["uri"],
        )

    def test_removes_movie_by_native_collection_id(self):
        plex = FakeCollectionPlex(member=True)
        handler, responses = handler_with_payload(
            {"ratingKey": "501", "collectionRatingKey": "101", "member": False}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_collection_membership("POST", {})

        self.assertEqual(200, responses[0][0])
        self.assertEqual(0, responses[0][1]["memberCount"])
        self.assertEqual("/library/collections/101/items/501", plex.open_calls[0][0])
        self.assertEqual("DELETE", plex.open_calls[0][2]["method"])

    def test_rejects_smart_collection_changes(self):
        plex = FakeCollectionPlex()
        handler, responses = handler_with_payload(
            {"ratingKey": "501", "collectionRatingKey": "102", "member": True}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_collection_membership("POST", {})

        self.assertEqual(409, responses[0][0])
        self.assertEqual("smart_collection_read_only", responses[0][1]["error"])
        self.assertEqual([], plex.open_calls)

    def test_rejects_non_movie_and_non_boolean_state(self):
        plex = FakeCollectionPlex()
        with mock.patch.object(server, "PLEX", plex):
            handler, responses = handler_with_payload({})
            handler.api_collection_membership("GET", {"ratingKey": ["601"]})
            self.assertEqual("unsupported_media_type", responses[0][1]["error"])

            handler, responses = handler_with_payload(
                {"ratingKey": "501", "collectionRatingKey": "101", "member": "true"}
            )
            handler.api_collection_membership("POST", {})
            self.assertEqual("invalid_member_state", responses[0][1]["error"])


class CollectionManagementTests(unittest.TestCase):
    def test_creates_collection_with_movie_and_refreshes_membership(self):
        plex = FakeCollectionPlex()
        handler, responses = handler_with_payload(
            {"action": "create", "ratingKey": "501", "title": "New Collection"}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_collection_management("POST")

        self.assertEqual(200, responses[0][0])
        self.assertEqual("create", responses[0][1]["action"])
        self.assertEqual(3, len(responses[0][1]["collections"]))
        created = next(item for item in responses[0][1]["collections"] if item["ratingKey"] == "103")
        self.assertTrue(created["member"])
        path, params, kwargs = plex.open_calls[0]
        self.assertEqual("/library/collections", path)
        self.assertEqual("POST", kwargs["method"])
        self.assertEqual("New Collection", params["title"])
        self.assertEqual("7", params["sectionId"])
        self.assertEqual(1, params["type"])

    def test_renames_manual_collection_and_preserves_membership(self):
        plex = FakeCollectionPlex(member=True)
        handler, responses = handler_with_payload(
            {
                "action": "rename",
                "ratingKey": "501",
                "collectionRatingKey": "101",
                "title": "Renamed Picks",
            }
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_collection_management("POST")

        self.assertEqual(200, responses[0][0])
        renamed = next(item for item in responses[0][1]["collections"] if item["ratingKey"] == "101")
        self.assertEqual("Renamed Picks", renamed["title"])
        self.assertTrue(renamed["member"])
        path, params, kwargs = plex.open_calls[0]
        self.assertEqual("/library/sections/7/all", path)
        self.assertEqual("PUT", kwargs["method"])
        self.assertEqual(18, params["type"])

    def test_deletes_manual_collection_without_deleting_movie(self):
        plex = FakeCollectionPlex(member=True)
        handler, responses = handler_with_payload(
            {"action": "delete", "ratingKey": "501", "collectionRatingKey": "101"}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_collection_management("POST")

        self.assertEqual(200, responses[0][0])
        self.assertNotIn("101", [item["ratingKey"] for item in responses[0][1]["collections"]])
        self.assertEqual("/library/collections/101", plex.open_calls[0][0])
        self.assertEqual("DELETE", plex.open_calls[0][2]["method"])
        self.assertEqual("501", responses[0][1]["item"]["ratingKey"])

    def test_deletes_collection_directly_from_library_view(self):
        plex = FakeCollectionPlex()
        handler, responses = handler_with_payload(
            {"action": "delete", "sectionKey": "7", "collectionRatingKey": "101"}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_collection_management("POST")

        self.assertEqual(200, responses[0][0])
        self.assertEqual("101", responses[0][1]["collectionRatingKey"])
        self.assertEqual("/library/collections/101", plex.open_calls[0][0])
        self.assertNotIn("101", [item["ratingKey"] for item in plex.collections])

    def test_rejects_direct_delete_for_smart_collection(self):
        plex = FakeCollectionPlex()
        handler, responses = handler_with_payload(
            {"action": "delete", "sectionKey": "7", "collectionRatingKey": "102"}
        )
        with mock.patch.object(server, "PLEX", plex):
            handler.api_collection_management("POST")

        self.assertEqual(409, responses[0][0])
        self.assertEqual("smart_collection_read_only", responses[0][1]["error"])
        self.assertEqual([], plex.open_calls)

    def test_rejects_duplicate_and_invalid_titles(self):
        plex = FakeCollectionPlex()
        with mock.patch.object(server, "PLEX", plex):
            handler, responses = handler_with_payload(
                {"action": "create", "ratingKey": "501", "title": " manual picks "}
            )
            handler.api_collection_management("POST")
            self.assertEqual(409, responses[0][0])
            self.assertEqual("collection_title_already_exists", responses[0][1]["error"])

            handler, responses = handler_with_payload(
                {"action": "create", "ratingKey": "501", "title": "\n"}
            )
            handler.api_collection_management("POST")
            self.assertEqual(400, responses[0][0])
            self.assertEqual("invalid_collection_title", responses[0][1]["error"])
        self.assertEqual([], plex.open_calls)

    def test_rejects_smart_collection_rename_and_delete(self):
        plex = FakeCollectionPlex()
        with mock.patch.object(server, "PLEX", plex):
            for action in ("rename", "delete"):
                payload = {
                    "action": action,
                    "ratingKey": "501",
                    "collectionRatingKey": "102",
                }
                if action == "rename":
                    payload["title"] = "Not Allowed"
                handler, responses = handler_with_payload(payload)
                handler.api_collection_management("POST")
                self.assertEqual(409, responses[0][0])
                self.assertEqual("smart_collection_read_only", responses[0][1]["error"])
        self.assertEqual([], plex.open_calls)


class EpisodeNeighborTests(unittest.TestCase):
    def test_returns_adjacent_episodes_across_season_boundaries(self):
        plex = FakeEpisodePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_episode_neighbors({"ratingKey": ["12"]})

        self.assertEqual(200, responses[0][0])
        self.assertEqual("11", responses[0][1]["previous"]["ratingKey"])
        self.assertEqual("13", responses[0][1]["next"]["ratingKey"])
        self.assertEqual(1, responses[0][1]["position"])
        self.assertEqual(3, responses[0][1]["totalSize"])
        self.assertEqual("/library/metadata/10/allLeaves", plex.xml_calls[1][0])

    def test_last_episode_has_no_next_episode(self):
        plex = FakeEpisodePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_episode_neighbors({"ratingKey": ["13"]})

        self.assertEqual(200, responses[0][0])
        self.assertEqual("12", responses[0][1]["previous"]["ratingKey"])
        self.assertIsNone(responses[0][1]["next"])

    def test_rejects_invalid_rating_key_before_calling_plex(self):
        plex = FakeEpisodePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_episode_neighbors({"ratingKey": ["../12"]})

        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_rating_key", responses[0][1]["error"])
        self.assertEqual([], plex.xml_calls)


class MediaDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.movies = self.base / "movies"
        self.tv = self.base / "tv"
        self.movies.mkdir()
        self.tv.mkdir()
        self.settings_patch = mock.patch.multiple(
            server.Settings,
            media_delete_enabled=True,
            media_delete_roots=os.pathsep.join([str(self.movies), str(self.tv)]),
            media_delete_plan_ttl=300,
            qbittorrent_backup_dir="",
            saved_media_dir=str(self.base / "saved"),
        )
        self.settings_patch.start()
        self.audit_patch = mock.patch.object(
            server,
            "MEDIA_DELETE_LOG_FILE",
            self.base / "data" / "media-delete-log.jsonl",
        )
        self.list_patch = mock.patch.object(
            server,
            "MY_LIST_FILE",
            self.base / "data" / "my-list.json",
        )
        self.audit_patch.start()
        self.list_patch.start()

    def tearDown(self):
        self.list_patch.stop()
        self.audit_patch.stop()
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def element(path, item_type="movie", rating_key="501"):
        episode_attrs = (
            ' grandparentTitle="Test Show" parentIndex="1" index="3"'
            if item_type == "episode"
            else ""
        )
        escaped = str(path).replace("&", "&amp;").replace('"', "&quot;")
        return ET.fromstring(
            f'<Video ratingKey="{rating_key}" type="{item_type}" title="Test title" '
            f'librarySectionID="7"{episode_attrs}>'
            f'<Media videoCodec="h264" audioCodec="aac"><Part id="1" '
            f'key="/library/parts/1/file.mkv" file="{escaped}" /></Media></Video>'
        )

    def test_movie_deletes_its_complete_folder_and_audits_the_action(self):
        folder = self.movies / "Test Movie (2026)"
        folder.mkdir()
        video = folder / "Test.Movie.2026.mkv"
        subtitle = folder / "Test.Movie.2026.en.srt"
        note = folder / "README.txt"
        video.write_bytes(b"video")
        subtitle.write_text("subtitle")
        note.write_text("release notes")
        plex = FakePlex()

        with mock.patch.object(server, "metadata_item_element", return_value=self.element(video)), mock.patch.object(server, "PLEX", plex):
            plan = server.build_media_delete_plan("501")
            public = server.public_media_delete_plan(plan)
            token = server.verify_media_delete_token(public["confirmationToken"])
            result = server.execute_media_delete(plan)

        self.assertEqual(plan["_digest"], token["digest"])
        self.assertEqual(1, public["folderCount"])
        self.assertEqual(3, public["fileCount"])
        self.assertFalse(folder.exists())
        self.assertTrue(result["ok"])
        self.assertTrue(server.MEDIA_DELETE_LOG_FILE.is_file())
        self.assertIn(("/library/metadata/501", {}, {"method": "DELETE"}), plex.open_calls)

    def test_episode_removes_all_approved_hardlinks_but_keeps_sibling_episode(self):
        source = self.movies / "Test.Show.S01E03.mkv"
        source.write_bytes(b"episode")
        source.with_suffix(".en.srt").write_text("source subtitle")
        season = self.tv / "Test Show" / "Season 01"
        season.mkdir(parents=True)
        episode = season / "Test Show - S01E03.mkv"
        os.link(source, episode)
        episode.with_suffix(".srt").write_text("tv subtitle")
        sibling = season / "Test Show - S01E04.mkv"
        sibling.write_bytes(b"next")

        with mock.patch.object(
            server,
            "metadata_item_element",
            return_value=self.element(episode, item_type="episode"),
        ), mock.patch.object(server, "PLEX", FakePlex()):
            plan = server.build_media_delete_plan("501")
            result = server.execute_media_delete(plan)

        self.assertEqual(1, plan["hardLinkCopies"])
        self.assertEqual(0, plan["folderCount"])
        self.assertFalse(source.exists())
        self.assertFalse(episode.exists())
        self.assertFalse(source.with_suffix(".en.srt").exists())
        self.assertFalse(episode.with_suffix(".srt").exists())
        self.assertTrue(sibling.is_file())
        self.assertTrue(season.is_dir())
        self.assertEqual(4, result["deletedFileCount"])

    def test_movie_in_shared_folder_only_deletes_matching_files(self):
        folder = self.movies / "Shared"
        folder.mkdir()
        video = folder / "Wanted.mkv"
        subtitle = folder / "Wanted.en.srt"
        other = folder / "Keep.mkv"
        video.write_bytes(b"wanted")
        subtitle.write_text("subtitle")
        other.write_bytes(b"keep")

        with mock.patch.object(server, "metadata_item_element", return_value=self.element(video)), mock.patch.object(server, "PLEX", FakePlex()):
            plan = server.build_media_delete_plan("501")
            server.execute_media_delete(plan)

        self.assertEqual(0, plan["folderCount"])
        self.assertTrue(any("other video files" in warning for warning in plan["warnings"]))
        self.assertFalse(video.exists())
        self.assertFalse(subtitle.exists())
        self.assertTrue(other.is_file())

    def test_preview_blocks_a_media_folder_the_service_cannot_write(self):
        folder = self.movies / "Read Only Movie"
        folder.mkdir()
        video = folder / "Read.Only.Movie.mkv"
        video.write_bytes(b"video")

        with mock.patch.object(server, "metadata_item_element", return_value=self.element(video)), mock.patch.object(server.os, "access", return_value=False):
            plan = server.build_media_delete_plan("501")

        self.assertFalse(plan["canDelete"])
        self.assertIn("cannot write", plan["blockReason"])
        self.assertTrue(video.is_file())

    def test_refuses_media_with_an_unapproved_hardlink(self):
        video = self.movies / "Linked.mkv"
        video.write_bytes(b"linked")
        outside = self.base / "outside"
        outside.mkdir()
        os.link(video, outside / "Linked.mkv")

        with mock.patch.object(server, "metadata_item_element", return_value=self.element(video)):
            with self.assertRaises(server.MediaDeletionError) as raised:
                server.build_media_delete_plan("501")

        self.assertEqual("hardlinks_outside_approved_roots", raised.exception.code)

    def test_endpoint_requires_the_exact_confirmation_phrase(self):
        folder = self.movies / "Confirmed Movie"
        folder.mkdir()
        video = folder / "Confirmed.Movie.mkv"
        video.write_bytes(b"video")
        elem = self.element(video)
        with mock.patch.object(server, "metadata_item_element", return_value=elem):
            plan = server.public_media_delete_plan(server.build_media_delete_plan("501"))
            handler, responses = handler_with_payload(
                {
                    "ratingKey": "501",
                    "confirmationToken": plan["confirmationToken"],
                    "confirmation": "delete",
                }
            )
            handler.api_media_delete("POST", {})

        self.assertEqual(400, responses[0][0])
        self.assertEqual("confirmation_required", responses[0][1]["error"])
        self.assertTrue(video.is_file())

    def test_endpoint_rejects_a_plan_when_folder_contents_change(self):
        folder = self.movies / "Changing Movie"
        folder.mkdir()
        video = folder / "Changing.Movie.mkv"
        video.write_bytes(b"video")
        elem = self.element(video)
        with mock.patch.object(server, "metadata_item_element", return_value=elem):
            plan = server.public_media_delete_plan(server.build_media_delete_plan("501"))
            (folder / "new-file.txt").write_text("arrived after preview")
            handler, responses = handler_with_payload(
                {
                    "ratingKey": "501",
                    "confirmationToken": plan["confirmationToken"],
                    "confirmation": "DELETE",
                }
            )
            handler.api_media_delete("POST", {})

        self.assertEqual(409, responses[0][0])
        self.assertEqual("deletion_plan_changed", responses[0][1]["error"])
        self.assertTrue(video.is_file())


class MyListTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(server, "MY_LIST_FILE", Path(self.temp_dir.name) / "my-list.json")
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_add_list_and_remove_are_persisted_in_newest_first_order(self):
        plex = FakePlex()
        with mock.patch.object(server, "PLEX", plex):
            for rating_key in ("101", "102"):
                handler, responses = handler_with_payload({"ratingKey": rating_key, "saved": True})
                handler.api_my_list("POST", {})
                self.assertEqual(200, responses[0][0])

            handler, responses = handler_with_payload({})
            handler.api_my_list("GET", {"keysOnly": ["1"]})
            self.assertEqual(["102", "101"], responses[0][1]["ratingKeys"])

            handler, responses = handler_with_payload({"ratingKey": "102", "saved": False})
            handler.api_my_list("POST", {})
            self.assertEqual(["101"], responses[0][1]["ratingKeys"])

        self.assertTrue(server.MY_LIST_FILE.exists())
        self.assertEqual(["101"], server.my_list_keys())

    def test_my_list_library_view_filters_items_by_section(self):
        server.update_my_list("101", True)
        server.update_my_list("202", True)
        plex = FakePlex()
        handler, responses = handler_with_payload({})
        with mock.patch.object(server, "PLEX", plex):
            handler.api_library(
                "/api/library/7",
                {"view": ["mylist"], "start": ["0"], "limit": ["10"]},
            )

        self.assertEqual(200, responses[0][0])
        self.assertEqual(1, responses[0][1]["totalSize"])
        self.assertEqual(["101"], [item["ratingKey"] for item in responses[0][1]["items"]])
        self.assertTrue(responses[0][1]["items"][0]["inMyList"])

    def test_my_list_rejects_non_boolean_saved_state(self):
        handler, responses = handler_with_payload({"ratingKey": "101", "saved": "true"})
        handler.api_my_list("POST", {})

        self.assertEqual(400, responses[0][0])
        self.assertEqual("invalid_saved_state", responses[0][1]["error"])


if __name__ == "__main__":
    unittest.main()
