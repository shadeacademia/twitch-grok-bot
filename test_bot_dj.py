import os
import queue
import unittest
import unittest.mock

from bot import Bot


class BotDjTest(unittest.TestCase):
    def setUp(self):
        os.environ["TWITCH_NICK"] = "bot"
        os.environ["TWITCH_OAUTH"] = "oauth:x"
        os.environ["TWITCH_CHANNEL"] = "owner"
        self.bot = Bot()

    def _priv(self, user, text, badges=""):
        self.bot._on_message(user, {"badges": badges, "id": "1"}, text)

    def _said(self):
        lines = []
        while True:
            try:
                lines.append(self.bot.outbound.get_nowait())
            except queue.Empty:
                return lines

    def test_flag_starts_off(self):
        self.assertFalse(self.bot.requests_enabled)

    def test_dj_public(self):
        self._priv("stranger", "!dj")
        self.assertTrue(any("grok-dj is the stream music player." in line for line in self._said()))

    def test_non_privileged_dj_still_moderates(self):
        self._priv("stranger", "!dj buy followers")
        lines = self._said()
        self.assertTrue(any("/timeout stranger" in line and "banned phrase" in line for line in lines))

    def test_owner_requests_on_sets_flag(self):
        self._priv("owner", "!requests on")
        self.assertTrue(self.bot.requests_enabled)
        self.assertTrue(any(line.endswith(" :requests are on") for line in self._said()))

    def test_sr_ignored_when_not_auth(self):
        self.bot.requests_enabled = True
        self._priv("stranger", "!sr rage")
        self.assertEqual(self._said(), [])
        self.assertTrue(self.bot.jobs.empty())

    def test_sr_enqueues_for_owner_when_on(self):
        self.bot.requests_enabled = True
        self._priv("owner", "!sr never gonna")
        kind, user, text, _ = self.bot.jobs.get_nowait()
        self.assertEqual((kind, user, text), ("dj_sr", "owner", "never gonna"))

    def test_sr_off_whispers_owner(self):
        self._priv("owner", "!sr rage")
        lines = self._said()
        self.assertTrue(any(line.endswith(" :requests are off") for line in lines))
        self.assertTrue(any("PRIVMSG #owner :/w owner " in line and "requests are off" in line for line in lines))

    def test_sr_cooldown_is_silent(self):
        self.bot.requests_enabled = True
        self.bot.cooldown = 60
        self._priv("owner", "!sr one")
        self.bot.jobs.get_nowait()
        self._said()
        self._priv("owner", "!sr two")
        self.assertTrue(self.bot.jobs.empty())
        self.assertEqual(self._said(), [])

    def test_enqueue_full_busy(self):
        self.bot.requests_enabled = True
        for _ in range(8):
            self.bot.jobs.put_nowait(("chat", "x", "q", ""))
        self._priv("owner", "!sr rage")
        lines = self._said()
        self.assertTrue(any(line.endswith(" :busy, try again") for line in lines))
        self.assertTrue(any("/w owner" in line and "worker queue full" in line for line in lines))

    def test_whisper_is_capped(self):
        self.bot.whisper("x" * 500)
        line = self._said()[0]
        details = line.split(" :/w owner ", 1)[1]
        self.assertEqual(len(details), 400)

    def test_worker_sr_success(self):
        with unittest.mock.patch("bot.run_queue", return_value=("@owner queued Song — Artist", "")):
            self.bot._handle_job("dj_sr", "owner", "rage", "")
        lines = self._said()
        self.assertTrue(any(line.endswith(" :@owner queued Song — Artist") for line in lines))
        self.assertFalse(any("/w " in line for line in lines))

    def test_worker_sr_exception(self):
        with unittest.mock.patch("bot.run_queue", side_effect=RuntimeError("boom")):
            self.bot._handle_job("dj_sr", "owner", "rage", "")
        lines = self._said()
        self.assertTrue(any(line.endswith(" :couldn't play that") for line in lines))
        self.assertTrue(any("/w owner" in line and "boom" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
