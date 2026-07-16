import unittest
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
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
