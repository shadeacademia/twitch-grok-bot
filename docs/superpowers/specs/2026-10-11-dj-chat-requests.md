# DJ chat requests

Auth Twitch chatters can request songs through the existing grok-dj player. The Twitch bot is the only thing in chat. grok-dj does not change.

## Goal

While grok-dj is open, the owner can turn requests on, and people already allowed to talk to the bot can queue a song with `!sr`. Chat can also ask what the DJ is (`!dj`) and, if authorized, what's playing (`!song`). Skip, pause, and resume stay on the grok-dj window.

## Out of scope

- Changing grok-dj
- Starting grok-dj from the bot
- Chat commands for skip, pause, or resume
- Persisting the requests on/off flag across bot restarts
- Visualizer banner for who requested a song

## Architecture

Two processes on this machine.

- **twitch-grok-bot** remains the only Twitch IRC client.
- **grok-dj** remains the YouTube Music player. It already serves `127.0.0.1` and writes its port to `$XDG_CONFIG_HOME/grok-dj/port` (default `~/.config/grok-dj/port`), falling back to `47721`.

The bot GETs `/status` and POSTs `/queue`. It does not shell out to the grok-dj CLI.

Requests start **off** each time `bot.py` starts. The flag lives in memory on `Bot`.

## Components

All work is in `twitch-grok-bot`.

| Piece | Responsibility |
|---|---|
| `dj.py` | Port file, HTTP client, track line, help/error copy, and `decide_dj_command` (auth, flag, enqueue vs reply). Connection failures are “not running”. |
| `bot.py` | On/off flag. Calls `decide_dj_command`. Worker jobs for `!sr` / `!song`. `say` and `/w`. Cooldown timestamps. |
| `chat.py` | `!sr`, `!song`, `!dj`, `!requests` stay commands, so they never become Grok questions. |
| `test_dj.py` | HTTP client, copy, `decide_dj_command`. |
| `test_logic.py` | Existing tests plus `chat_question` ignores DJ commands. |

stdlib only (`urllib.request`). No new dependency.

## Commands

`parse_command` already lowercases the name.

| Who | Command | When | Effect |
|---|---|---|---|
| Anyone | `!dj` | always | Channel blurb: what grok-dj is, the commands, and whether requests are on or off. Does not call grok-dj. |
| Auth (owner + whitelist) | `!song` | always | Worker GETs `/status`. Channel: now playing, paused, nothing, not signed in, or not running. |
| Auth | `!sr <query>` | requests on | Worker GETs `/status` then POSTs `/queue` with `{ "query": "<query>" }`. Channel: queued title/artist. |
| Owner | `!requests on` | always | Set flag true. Channel: `requests are on`. |
| Owner | `!requests off` | always | Set flag false. Channel: `requests are off`. |
| Owner | `!requests` | always | Channel: current on/off. |
| Auth, not owner | `!requests` | — | Short channel error + owner whisper. |
| Everyone else | `!sr` / `!song` / `!requests` | — | Ignored. No reply, no whisper. |

Owner is always auth (`may_chat`).

`!sr` and `!song` use the existing per-user `CHAT_COOLDOWN` (default 15s). `!dj` and `!requests` do not.

## Data flow

1. PRIVMSG arrives. `parse_command` splits `!name` and the rest.
2. DJ commands are handled **before** the Grok chat path, same area as `!allow`.
3. `!dj` and `!requests` reply on the IRC thread. No grok-dj call.
4. `!sr` and `!song` enqueue a job on the existing worker (`kind` `dj_sr` or `dj_song`). Same one-at-a-time queue as Grok, so a song request waits behind a Grok reply.
5. The worker talks to `127.0.0.1:{port}` with a 20s timeout. `!song` is GET `/status`. `!sr` is GET `/status` then POST `/queue`.
6. Channel reply for success or short error. Errors also whisper the owner.

Job tuple stays `(kind, user, text, msg_id)`. For DJ jobs, `text` is the song query (`!sr`) or empty (`!song`).

## grok-dj HTTP

Port: read `$XDG_CONFIG_HOME/grok-dj/port` or `~/.config/grok-dj/port`. If missing, unreadable, or not a positive int, use `47721`.

| Command | Request | Success body |
|---|---|---|
| `!sr` | GET `/status`, then POST `/queue` JSON `{ "query": "<song>" }` | snapshot `{ ok, now, upNext, paused, signedIn }` |
| `!song` | GET `/status` | same snapshot shape |

Track line, same as the grok-dj CLI: `title — artist` if artist is set, otherwise `title`. Em dash.

`!sr` success track, using the snapshot from **before** the POST:

- If `now` was empty, grok-dj starts the request. Use `now` from the POST response.
- If `now` was set, grok-dj `unshift`s the request. Use `upNext[0]` from the POST response.

If that track has no title, use the original query. If GET already says not running, skip the POST.

Connection refused, timeout, or unreadable response → not running.

HTTP 400 / `{ ok: false, error }` → grok-dj error. Use `error` text in the owner whisper.

## Channel copy

Exact strings. `say()` still collapses whitespace and caps at 480 characters. Success `!sr` is prefixed with `@{user}` like Grok replies.

**`!dj`**

```
grok-dj is the stream music player. !sr song requests a track (auth, when requests are on). !song is what's playing (auth). Owner: !requests on/off. Skip, pause, and resume stay on the player. Requests are on.
```

Last sentence is `Requests are off.` when the flag is false.

**Success**

| Event | Channel |
|---|---|
| `!requests on` | `requests are on` |
| `!requests off` | `requests are off` |
| `!requests` (status) | `requests are on` or `requests are off` |
| `!sr` queued/started | `@{user} queued {line}` |
| `!song` playing | `Now: {line}` |
| `!song` paused | `Paused: {line}` |
| `!song` signed in, nothing | `Nothing playing.` |
| `!song` not signed in | `DJ isn't signed in.` |

**Short errors (channel)**

| Situation | Channel |
|---|---|
| `!sr` while requests off | `requests are off` |
| `!sr` with no query | `use !sr song name` |
| grok-dj not running (`!sr` / `!song`) | `DJ isn't running` |
| grok-dj error (`!sr`) | `couldn't play that` |
| grok-dj error (`!song`) | `couldn't read the DJ` |
| Worker queue full (`!sr` / `!song`) | `busy, try again` |
| Auth non-owner `!requests` | `only the owner can do that` |

Empty `!sr` and requests-off are answered on the IRC thread (no worker). Queue-full is answered in `_enqueue` when `put_nowait` fails.

## Owner whispers

Whisper target: `TWITCH_CHANNEL` (the channel login).

Send the same way as `/timeout`: `PRIVMSG #{channel} :/w {owner} {details}` with details capped at 400 characters.

Every short channel error also whispers. Success does not whisper. Ignored non-auth commands do not whisper.

| Situation | Whisper |
|---|---|
| Requests off | `{user} !sr {query}: requests are off` |
| Empty `!sr` | `{user} !sr: missing song` |
| Not running | `{user} !sr {query}: grok-dj not running` or `{user} !song: grok-dj not running` |
| grok-dj error | `{user} !sr {query}: {error}` or `{user} !song: {error}` |
| Queue full | `{user} !sr {query}: worker queue full` or `{user} !song: worker queue full` |
| Auth non-owner `!requests` | `{user} tried !requests` |

If `{query}` is empty, omit it (`{user} !sr: …`).

## Dispatch in `bot.py`

After `parse_command`, before Grok:

1. If command name is `dj` → `say` the blurb. Return.
2. If command name is `requests`:
   - owner → set/read flag, `say` on/off. Return.
   - `may_chat` → short error + whisper. Return.
   - else ignore. Return.
3. If command name is `sr` or `song`:
   - not `may_chat` → ignore. Return.
   - `sr` and no query → short error + whisper. Return.
   - `sr` and requests off → short error + whisper. Return.
   - else apply cooldown; `_enqueue("dj_sr"|"dj_song", login, query, "")`. Return.
4. Existing `allow` / `unallow` / `whitelist` path unchanged.
5. Existing Grok path unchanged.

`bot.py` calls `decide_dj_command(name, arg, user=..., is_owner=..., may_chat=..., requests_on=...)` and then `say` / whisper / `_enqueue`. Cooldown stays in `bot.py`.

`decide_dj_command` returns `None` when this is not a DJ command (caller continues to `!allow` / Grok). Otherwise a small result: `action` is `say`, `error`, or `enqueue`; `channel` and `whisper` strings; `job` is `dj_sr` or `dj_song` when enqueueing; `query` is the song text. `action=say` has no whisper. `action=error` always has both strings. `action=enqueue` has empty strings (the worker writes the replies after HTTP).

Worker: on `dj_sr` / `dj_song`, call the HTTP helpers, then `say` + maybe whisper. Failures from `dj.py` are data (not running / grok-dj error), not exceptions for the happy path. Unexpected exceptions print to stdout like other worker errors, then send the matching short channel error (`couldn't play that` / `couldn't read the DJ`) plus whisper `{user} !sr {query}: {exc}`.

## Testing

unittest. grok-dj is not started. HTTP is mocked (`urllib` patched or a fake opener).

`test_logic.py`:

- `parse_command` for `!sr`, `!song`, `!dj`, `!requests on`, `!requests off`, `!requests`
- `chat_question("!sr never gonna give you up", ...)` is `None`
- Existing whitelist, automod, and Grok parse tests still pass

`test_dj.py`:

- `decide_dj_command`: `!dj` has no auth gate; non-auth `!sr` / `!song` / `!requests` ignore; owner and whitelist enqueue `!sr` / `!song`; `!sr` blocked when requests off; empty `!sr`; owner-only `!requests`; auth non-owner `!requests` returns short error + whisper
- HTTP: request started (`now` empty before POST → use new `now`); request queued (`now` set before POST → use new `upNext[0]`); nothing playing; not signed in; connection refused; `{ ok: false, error }`
- Channel and whisper strings match the tables

## Risks

Twitch may drop IRC `/w` whispers if the bot account cannot whisper. Channel short errors still land. If whispers are silent in live testing, fix the send path then; do not block the rest of the feature on it.
