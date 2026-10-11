import unittest

from dj import (
    busy_replies,
    decide_dj_command,
    error_whisper,
    format_track,
    help_text,
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


if __name__ == "__main__":
    unittest.main()
