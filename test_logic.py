import tempfile
import unittest
from pathlib import Path

from chat import chat_question, clean_chat_reply, parse_command, parse_mod_decision
from grok_bridge import reply_text
from moderate import LocalMod
from whitelist import Whitelist


class WhitelistTest(unittest.TestCase):
    def test_add_persists_and_ignores_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whitelist.txt"
            book = Whitelist(path)
            self.assertIsNone(book.add("nope name"))
            self.assertEqual(book.add("@CoolUser"), "cooluser")
            again = Whitelist(path)
            self.assertTrue(again.allows("CoolUser"))
            self.assertFalse(again.allows("someoneelse"))
            again.remove("cooluser")
            self.assertFalse(again.allows("cooluser"))


class ChatGateTest(unittest.TestCase):
    def test_only_addressed_messages_become_questions(self):
        self.assertIsNone(chat_question("hello chat", "grokbot", False))
        self.assertEqual(chat_question("!grok what time is it", "grokbot", False), "what time is it")
        self.assertEqual(chat_question("@GrokBot hey", "grokbot", False), "hey")
        self.assertIsNone(chat_question("!allow steve", "grokbot", False))
        self.assertEqual(parse_command("!sr never gonna"), ("sr", "never gonna"))
        self.assertEqual(parse_command("!song"), ("song", ""))
        self.assertEqual(parse_command("!dj"), ("dj", ""))
        self.assertEqual(parse_command("!requests on"), ("requests", "on"))
        self.assertEqual(parse_command("!requests off"), ("requests", "off"))
        self.assertEqual(parse_command("!requests"), ("requests", ""))
        self.assertIsNone(chat_question("!sr never gonna give you up", "grokbot", False))
        self.assertIsNone(chat_question("!song", "grokbot", False))
        self.assertIsNone(chat_question("!dj", "grokbot", False))
        self.assertIsNone(chat_question("!requests on", "grokbot", False))

    def test_planning_sentence_is_stripped(self):
        raw = (
            "I'll look at the bot repo and how secrets are handled so I can answer shadeacademia accurately."
            "Yes. Leave the tokens out."
        )
        self.assertEqual(clean_chat_reply(raw), "Yes. Leave the tokens out.")

    def test_leading_mention_is_stripped(self):
        self.assertEqual(
            clean_chat_reply("@shadeacademia yeah i'm good", "shadeacademia"),
            "yeah i'm good",
        )

    def test_mod_decision_parser(self):
        self.assertEqual(
            parse_mod_decision('{"action":"timeout","seconds":90,"reason":"scam"}'),
            ("timeout", 90, "scam"),
        )
        self.assertIsNone(parse_mod_decision("nope"))
        self.assertEqual(parse_command("!unallow @Bob"), ("unallow", "@Bob"))


class ModTest(unittest.TestCase):
    def test_banned_phrase_and_clean_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "banned.txt"
            path.write_text("buy followers\n", encoding="utf-8")
            mod = LocalMod(path)
            self.assertEqual(mod.check("a", "please buy followers now").reason, "banned phrase")
            self.assertIsNone(mod.check("a", "that clutch was nasty"))
            self.assertEqual(mod.check("a", "go to http://evil.example").reason, "link")


class GrokParseTest(unittest.TestCase):
    def test_reply_text_ignores_a_banner(self):
        raw = 'noise\n{"text":"pong from chat","stopReason":"end_turn"}\n'
        self.assertEqual(reply_text(raw), "pong from chat")


if __name__ == "__main__":
    unittest.main()
