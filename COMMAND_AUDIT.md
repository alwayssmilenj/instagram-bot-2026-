# KnightBot WhatsApp Command Audit → Instagram Adapter

Every file in `whatsapp-bot/commands` was reviewed for its platform dependency. Status below describes the Instagram adaptation.

## Native or adapted and routed

- Core/info: `alive.js`, `ping.js`, `owner.js`, `help.js`, `fact.js`, `joke.js`, `quote.js`, `weather.js`, `news.js`, `github.js`, `lyrics.js`, `translate.js`, `meme.js`.
- Fun/social: `eightball.js`, `compliment.js`, `flirt.js`, `insult.js`, `truth.js`, `dare.js`, `goodnight.js`, `shayari.js`, `roseday.js`, `character.js`, `ship.js`, `simp.js`, `stupid.js`, `wasted.js`, `anime.js`, `pies.js`, `misc.js`.
- Music/link: `song.js` and `play.js` download bounded AAC/M4A and send an Instagram voice message; `spotify.js` returns an Instagram-playable search link.
- Group moderation: `antilink.js`, `antibadword.js`, `ban.js`, `unban.js`, `warn.js`, `warnings.js`, `mute.js`, `unmute.js`, `groupinfo.js`, `staff.js`, `tagall.js`, and the rename portion of `groupmanage.js`. State is persisted in SQLite and admin actions obey the configured owner or Instagram `admin_user_ids`.

## Adapted to a safe Instagram response/equivalent

- Group/tag/event family: `antitag.js`, `tag.js`, `tagnotadmin.js`, `hidetag.js`, `mention.js`, `topmembers.js`, `welcome.js`, `goodbye.js`, `promote.js`, `demote.js`, `kick.js`, `resetlink.js`, `delete.js`, `clear.js`. Instagram does not expose safe equivalents for every WhatsApp group operation; commands report the limitation and do not fake success.
- AI/generation family: `ai.js`, `chatbot.js`, `imagine.js`, `sora.js`, `video.js`. Routed with a clear API-key requirement rather than silently failing.
- Image/sticker family: `attp.js`, `emojimix.js`, `img-blur.js`, `remini.js`, `removebg.js`, `setpp.js`, `simage.js`, `sticker-alt.js`, `sticker.js`, `stickercrop.js`, `stickertelegram.js`, `take.js`, `textmaker.js`. Instagram lacks WhatsApp sticker metadata; commands explain the required supported attachment path.
- Downloader/media family: `facebook.js`, `instagram.js`, `igs.js`, `tiktok.js`, `gif.js`, `ss.js`, `tts.js`, `url.js`. Unsafe/unreliable bulk download behavior is not enabled; original links are preserved.
- Games: `hangman.js`, `tictactoe.js`, `trivia.js`. Routed without pretending that a persistent game started; lightweight Instagram game state remains a future enhancement.
- WhatsApp account/runtime family: `anticall.js`, `autoread.js`, `autostatus.js`, `autotyping.js`, `clearsession.js`, `cleartmp.js`, `pair.js`, `pmblocker.js`, `settings.js`, `sudo.js`, `update.js`. These rely on Baileys/WhatsApp events or host maintenance and return an explicit platform/owner-only response.

## Intentionally not ported

- `antidelete.js` and `viewonce.js`: recovering deleted/private ephemeral content or bypassing view-once expectations is privacy-invasive, so these are explicitly disabled.

The original JavaScript files remain unchanged under `whatsapp-bot/`; the Instagram implementation is an adapter rather than a replacement.
