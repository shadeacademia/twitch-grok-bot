# Twitch Grok bot

AutoMod for the channel, plus a chat bot that only answers people you add.
Replies use the `grok` login already on this machine (SuperGrok). No API key.

## Run

```bash
cp .env.example .env
# fill in the bot's Twitch login, oauth token, and your channel
python3 bot.py
```

Get the token while logged into the **bot** account: <https://twitchapps.com/tmi/>.
Mod that account in your channel or `/timeout` does nothing.

The process has to stay running here, because this is the machine signed into Grok.

## Who it talks to

Nobody, until you add them. From your channel account:

- `!allow someuser`
- `!unallow someuser`
- `!whitelist`

You can also edit `whitelist.txt`. The owner can always talk to it.
Whitelisted people get a reply from `!grok ...`, `!ask ...`, or `@botname ...`.
Everyone else is ignored, aside from moderation.

Each reply is one Grok turn, one at a time, so a busy chat does not fire them all at once.
A reply usually takes a few seconds. The default model is `grok-4.7-build-fast` at low effort, which is the quicker setting this login accepts.

## DJ requests

grok-dj must already be open on this machine. The bot talks to it on localhost and does not start it.

- `!dj` — anyone; what grok-dj is, the commands, and whether requests are on
- `!song` — auth; what's playing
- `!sr song` — auth; queue a track when requests are on
- `!requests on` / `!requests off` — owner

Skip, pause, and resume stay on the grok-dj window. Requests start off each time the bot starts. If grok-dj isn't running, the bot says so.
