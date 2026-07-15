import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import server


class FakeResponse:
    def getcode(self):
        return 200

    def close(self):
        return None


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
            rating_key = path.rsplit("/", 1)[-1]
            return ET.fromstring(
                f'<MediaContainer><Video ratingKey="{rating_key}" type="movie" title="Test" duration="600000" /></MediaContainer>'
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


def handler_with_payload(payload):
    handler = object.__new__(server.AppHandler)
    responses = []
    handler.read_json = lambda: payload
    handler.send_json = lambda body, status=200, headers=None: responses.append((status, body))
    return handler, responses


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


if __name__ == "__main__":
    unittest.main()
