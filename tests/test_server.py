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
