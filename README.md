# ineffa Instagram Bot

An Instagram DM/group adapter for KnightBot, running as **ineffa** with **@_hooman_hater** as owner/super-admin. The unchanged WhatsApp source remains in `../whatsapp-bot`; the adapter is in `../instagram-bot`, both under `~/Desktop/knightbot-instagram`.

## Runtime

- Chromium persistent-profile login with saved-identity **Continue** recovery
- Instagrapi realtime wakeups plus conservative polling fallback
- Twenty-five priority workers; owner/group-admin jobs run before normal queued jobs
- Queue capacity 500 with queue-number notices under pressure
- SQLite deduplication, moderation state, warnings, bans, and group settings
- 2 GB Python memory guard and 2 GB systemd cgroup limit
- Layered supervisor crash restart: 15s, 30s, 60s, then 5 minutes
- User systemd boot service with login linger enabled

## Commands

Run `.menu` or `.help` in Instagram for the current menu. Major features include:

- Voice music: `.song` / `.play`
- Country photos: `.pies india` or aliases such as `.india`, `.japan`, and `.korea`
- Local anime-elf sticker images: `.sticker` or `.sticker <happy|angry|smug|sleepy|love|shocked|sad|chaos>`
- Seventy individually tested tools covering math, statistics, random choices, encoding, JSON, Morse, hashes, text operations, dates, UUIDs, and generators
- Per-chat Ineffa mode: owner/group-admin `.aiautoreply on|off` for automatic replies in DMs and groups
- Group moderation: `.groupinfo`, `.staff`, `.tagall`, `.add`, `.setname`, `.warn`, `.ban`, `.mute`, `.antilink`, and `.antibadword`
- Group configuration: `.settings` and admin-only `.setting <name> <value>` for `antilink`, `antibadword`, `mute`, `adminonly`, and `maxwarnings`
- Owner controls: `.admin`, `.botstatus`, `.health`, `.stats`, `.cleartmp`, and `.restart`

The full 101-file WhatsApp command review is in `COMMAND_AUDIT.md`. WhatsApp-only and privacy-bypass behavior returns an explicit compatibility status instead of fake success.

## Control

```bash
./botctl.sh status
./botctl.sh restart
./botctl.sh stop
./botctl.sh start
./botctl.sh logs
```

The service is installed as `~/.config/systemd/user/jinshi-mds.service`, enabled for `default.target`, and user linger is enabled. To verify:

```bash
systemctl --user status jinshi-mds.service
loginctl show-user "$USER" -p Linger
```

Manual Chromium profile refresh:

```bash
./run.sh --browser-login
```

Offline validation:

```bash
./run.sh --check
./.venv/bin/python -m unittest -v test_bot.py
```

## Storage and safety

Runtime files remain local under `.venv/`, `.browsers/`, `session/`, `data/`, `temp/`, and `logs/`. `.env` and session files are excluded from Git and must not be shared.

This uses Instagram private interfaces and browser automation, not an official bot API. Instagram can require interactive verification, invalidate sessions, restrict automation, or suspend accounts. The adapter does not implement stealth, platform-limit evasion, unsolicited bulk DMs, deleted-message recovery, or view-once bypass.

## Attribution

Adapted from [KnightBot-MD](https://github.com/mruniquehacker/Knightbot-MD). Preserve original notices and comply with its license and Instagram's terms.
