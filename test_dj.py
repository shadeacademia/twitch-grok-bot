import io
import tempfile
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

from dj import (
    DjHttpResult,
    busy_replies,
    decide_dj_command,
    error_whisper,
    format_track,
    grok_dj_port,
    help_text,
    http_call,
    run_queue,
    run_song,
)


class CopyTest(unittest.TestCase):
    def test_help_text_on_and_off(self):
        self.assertIn("Requests are on.", help_text(True))
        self.assertIn("Requests are off.", help_text(False))
        self.assertTrue(help_text(True).startswith("grok-dj is the stream music player."))

    def test_format_track(self):
        self.assertEqual(format_track({"title": "Song", "artist": "Artist"}), "Song — Artist")
        self.assertEqual(format_track({"title": "Song", "artist": ""}), "Song")
        self.assertEqual(format_track({"title": "", "artist": "X"}, fallback="query"), "query")

    def test_error_whisper_omits_empty_query(self):
        self.assertEqual(error_whisper("bob", "sr", "rage", "requests are off"), "bob !sr rage: requests are off")
        self.assertEqual(error_whisper("bob", "sr", "", "missing song"), "bob !sr: missing song")
        self.assertEqual(error_whisper("bob", "song", "", "grok-dj not running"), "bob !song: grok-dj not running")

    def test_busy_replies(self):
        channel, whisper = busy_replies("bob", "sr", "rage")
        self.assertEqual(channel, "busy, try again")
        self.assertEqual(whisper, "bob !sr rage: worker queue full")


class DecideTest(unittest.TestCase):
    def test_unknown_command_is_none(self):
        self.assertIsNone(decide_dj_command("grok", "hi", user="a", is_owner=True, may_chat=True, requests_on=True))

    def test_dj_is_public(self):
        d = decide_dj_command("dj", "", user="stranger", is_owner=False, may_chat=False, requests_on=False)
        self.assertEqual(d.action, "say")
        self.assertEqual(d.channel, help_text(False))
        self.assertEqual(d.whisper, "")

    def test_non_auth_sr_song_requests_ignored(self):
        for name, arg in (("sr", "song"), ("song", ""), ("requests", "on")):
            d = decide_dj_command(name, arg, user="x", is_owner=False, may_chat=False, requests_on=True)
            self.assertEqual(d.action, "ignore")

    def test_auth_sr_enqueues_when_on(self):
        d = decide_dj_command("sr", "never gonna", user="bob", is_owner=False, may_chat=True, requests_on=True)
        self.assertEqual(d.action, "enqueue")
        self.assertEqual(d.job, "dj_sr")
        self.assertEqual(d.query, "never gonna")
        self.assertEqual(d.channel, "")
        self.assertEqual(d.whisper, "")

    def test_auth_song_enqueues(self):
        d = decide_dj_command("song", "", user="bob", is_owner=False, may_chat=True, requests_on=False)
        self.assertEqual(d.action, "enqueue")
        self.assertEqual(d.job, "dj_song")
        self.assertEqual(d.query, "")

    def test_sr_blocked_when_off(self):
        d = decide_dj_command("sr", "rage", user="bob", is_owner=True, may_chat=True, requests_on=False)
        self.assertEqual(d.action, "error")
        self.assertEqual(d.channel, "requests are off")
        self.assertEqual(d.whisper, "bob !sr rage: requests are off")

    def test_empty_sr(self):
        d = decide_dj_command("sr", "", user="bob", is_owner=True, may_chat=True, requests_on=True)
        self.assertEqual(d.action, "error")
        self.assertEqual(d.channel, "use !sr song name")
        self.assertEqual(d.whisper, "bob !sr: missing song")

    def test_owner_requests_on_off_status(self):
        on = decide_dj_command("requests", "on", user="own", is_owner=True, may_chat=True, requests_on=False)
        self.assertEqual(on.action, "say")
        self.assertEqual(on.channel, "requests are on")
        self.assertEqual(on.requests_on, True)
        off = decide_dj_command("requests", "off", user="own", is_owner=True, may_chat=True, requests_on=True)
        self.assertEqual(off.channel, "requests are off")
        self.assertEqual(off.requests_on, False)
        st = decide_dj_command("requests", "", user="own", is_owner=True, may_chat=True, requests_on=True)
        self.assertEqual(st.channel, "requests are on")
        self.assertIsNone(st.requests_on)

    def test_auth_non_owner_requests(self):
        d = decide_dj_command("requests", "on", user="bob", is_owner=False, may_chat=True, requests_on=False)
        self.assertEqual(d.action, "error")
        self.assertEqual(d.channel, "only the owner can do that")
        self.assertEqual(d.whisper, "bob tried !requests")

    def test_requests_on_is_case_insensitive(self):
        d = decide_dj_command("requests", "ON", user="own", is_owner=True, may_chat=True, requests_on=False)
        self.assertEqual(d.requests_on, True)
        self.assertEqual(d.channel, "requests are on")


class PortTest(unittest.TestCase):
    def test_missing_file_is_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(grok_dj_port(environ={}, home=Path(tmp)), 47721)

    def test_reads_port_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / ".config" / "grok-dj"
            d.mkdir(parents=True)
            (d / "port").write_text("47730\n", encoding="utf-8")
            self.assertEqual(grok_dj_port(environ={}, home=Path(tmp)), 47730)

    def test_zero_and_junk_are_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / ".config" / "grok-dj"
            d.mkdir(parents=True)
            (d / "port").write_text("0\n", encoding="utf-8")
            self.assertEqual(grok_dj_port(environ={}, home=Path(tmp)), 47721)
            (d / "port").write_text("nope\n", encoding="utf-8")
            self.assertEqual(grok_dj_port(environ={}, home=Path(tmp)), 47721)

    def test_xdg_config_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "xdg"
            (cfg / "grok-dj").mkdir(parents=True)
            (cfg / "grok-dj" / "port").write_text("47740", encoding="utf-8")
            self.assertEqual(grok_dj_port(environ={"XDG_CONFIG_HOME": str(cfg)}, home=Path("/nope")), 47740)


class HttpCallTest(unittest.TestCase):
    def test_http_unreadable_body_is_not_running(self):
        class FakeResp:
            def read(self):
                return b"nope"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with unittest.mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            self.assertTrue(http_call("GET", "/status", port=47721).not_running)

    def test_urlerror_is_not_running(self):
        with unittest.mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            self.assertTrue(http_call("GET", "/status", port=47721).not_running)

    def test_http_400_is_grok_dj_error(self):
        err = urllib.error.HTTPError(
            "http://127.0.0.1/queue",
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"ok": false, "error": "Say which song to queue"}'),
        )
        with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
            result = http_call("POST", "/queue", {"query": ""}, port=47721)
        self.assertEqual(result.error, "Say which song to queue")
        self.assertFalse(result.not_running)


class RunSongTest(unittest.TestCase):
    def test_now_playing(self):
        def fake(method, path, body=None, **kwargs):
            return DjHttpResult(
                body={
                    "ok": True,
                    "signedIn": True,
                    "paused": False,
                    "now": {"title": "Song", "artist": "Artist"},
                    "upNext": [],
                }
            )

        self.assertEqual(run_song("bob", http_call=fake), ("Now: Song — Artist", ""))

    def test_paused(self):
        def fake(method, path, body=None, **kwargs):
            return DjHttpResult(
                body={
                    "ok": True,
                    "signedIn": True,
                    "paused": True,
                    "now": {"title": "Song", "artist": ""},
                    "upNext": [],
                }
            )

        self.assertEqual(run_song("bob", http_call=fake)[0], "Paused: Song")

    def test_nothing_playing(self):
        def fake(method, path, body=None, **kwargs):
            return DjHttpResult(
                body={"ok": True, "signedIn": True, "paused": True, "now": None, "upNext": []}
            )

        self.assertEqual(run_song("bob", http_call=fake)[0], "Nothing playing.")

    def test_not_signed_in(self):
        def fake(method, path, body=None, **kwargs):
            return DjHttpResult(
                body={"ok": True, "signedIn": False, "paused": True, "now": None, "upNext": []}
            )

        self.assertEqual(run_song("bob", http_call=fake)[0], "DJ isn't signed in.")

    def test_not_running(self):
        def fake(method, path, body=None, **kwargs):
            return DjHttpResult(not_running=True)

        channel, whisper = run_song("bob", http_call=fake)
        self.assertEqual(channel, "DJ isn't running")
        self.assertEqual(whisper, "bob !song: grok-dj not running")


class RunQueueTest(unittest.TestCase):
    def test_queue_uses_new_up_next_when_now_was_set(self):
        calls = []

        def fake(method, path, body=None, **kwargs):
            calls.append((method, path, body))
            if method == "GET":
                return DjHttpResult(
                    body={"now": {"title": "Old", "artist": "A"}, "upNext": [{"title": "Older", "artist": ""}]}
                )
            return DjHttpResult(
                body={
                    "now": {"title": "Old", "artist": "A"},
                    "upNext": [{"title": "New", "artist": "B"}, {"title": "Older", "artist": ""}],
                }
            )

        channel, whisper = run_queue("bob", "new song", http_call=fake)
        self.assertEqual(channel, "@bob queued New — B")
        self.assertEqual(whisper, "")
        self.assertEqual(calls[1], ("POST", "/queue", {"query": "new song"}))

    def test_queue_started_uses_now(self):
        def fake(method, path, body=None, **kwargs):
            if method == "GET":
                return DjHttpResult(body={"now": None, "upNext": []})
            return DjHttpResult(body={"now": {"title": "New", "artist": "B"}, "upNext": []})

        self.assertEqual(run_queue("bob", "new song", http_call=fake)[0], "@bob queued New — B")

    def test_queue_falls_back_to_query_when_title_missing(self):
        def fake(method, path, body=None, **kwargs):
            if method == "GET":
                return DjHttpResult(body={"now": None, "upNext": []})
            return DjHttpResult(body={"now": {"title": "", "artist": ""}, "upNext": []})

        self.assertEqual(run_queue("bob", "never gonna", http_call=fake)[0], "@bob queued never gonna")

    def test_queue_skips_post_when_not_running(self):
        calls = []

        def fake(method, path, body=None, **kwargs):
            calls.append(method)
            return DjHttpResult(not_running=True)

        channel, whisper = run_queue("bob", "rage", http_call=fake)
        self.assertEqual(channel, "DJ isn't running")
        self.assertEqual(whisper, "bob !sr rage: grok-dj not running")
        self.assertEqual(calls, ["GET"])

    def test_queue_grok_dj_error(self):
        def fake(method, path, body=None, **kwargs):
            if method == "GET":
                return DjHttpResult(body={"now": None})
            return DjHttpResult(error='No song found for "rage"')

        channel, whisper = run_queue("bob", "rage", http_call=fake)
        self.assertEqual(channel, "couldn't play that")
        self.assertEqual(whisper, 'bob !sr rage: No song found for "rage"')


if __name__ == "__main__":
    unittest.main()

