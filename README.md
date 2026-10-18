# Sleeve

Move your music into Spotify. Drop in a folder, a playlist file or an iTunes / Music library
export, and Sleeve finds every song Spotify already has and adds it to a real Spotify playlist.
Only the songs Spotify doesn't have (rare releases, your own recordings, old rips) are turned
into clean, tagged local files, ready for Spotify's Local Files.

<p align="center"><img src="packaging/icons/icon-1024.png" width="128" alt=""></p>

## How it works

**Songs Spotify has go straight into the playlist.** With Spotify connected, each song is
looked up as soon as it's added: by ISRC when the file has one, then by title and artist.
Results are scored on title, artist and length. Confident matches show **On Spotify**; close
calls show **Check match** and are confirmed by you before anything is added (a live version,
a remaster, a cover). Catalog songs play on every device with no syncing and no Premium needed
for local files.

**Everything else becomes a local file.** Spotify has no upload API, so songs it doesn't have go
through its desktop **Local Files** feature. Sleeve prepares them:

- **Formats.** MP3 and M4A (AAC) are copied as they are. WAV, AIFF, FLAC, ALAC, Ogg, Opus and
  WMA are converted with FFmpeg to MP3 (320 kbps constant bitrate) or M4A (AAC, 256 kbps).
- **Videos.** MP4, MOV, M4V, WebM, MKV and AVI files are accepted too; Sleeve keeps only the
  audio. For your own YouTube uploads, download the original from YouTube Studio (Content ›
  ⋮ › Download) and drop it in. Sleeve doesn't download from YouTube links.
- **Tags.** Spotify shows local files using their embedded tags. Sleeve reads existing tags,
  falls back to a cleaned-up file name (`01 - my_song_FINAL v2.wav` becomes `My Song`), and
  writes title, artist, album, track number, ISRC and artwork into each prepared file.
- **Files.** Output goes to `~/Music/Sleeve/<Playlist name>/` as `Artist - Title.mp3`.
  Originals are only ever read, never changed or deleted.
- **Duplicates.** Files already prepared are skipped, and so is identical audio added twice.
  Songs already in the Spotify playlist aren't added again.
- **Copy protection.** DRM-protected files (some `.m4p` or `.wma` purchases) are skipped with a
  plain explanation. Sleeve never removes DRM.

Dragging local files into the playlist has to happen in the Spotify desktop app: Spotify's Web
API can't add local files to playlists. Without a Spotify connection, Sleeve prepares every song
as a local file.

### Importing a library

**File › Import playlist…** (or drop the file on the window) reads:

- **M3U / M3U8** playlists from most players, including `#EXTINF` titles and relative paths.
- **iTunes / Music library exports** (`Library.xml`, from *File › Library › Export Library…*).
  Pick the whole library or any playlist. Apple Music streaming songs with no file on your
  computer are still matched on Spotify.

The playlist name is filled in from the imported playlist, or from the folder you drop.

## Using Sleeve

1. Drag audio files, videos, a folder or a playlist onto the window, or click **Add files…**.
   When Spotify is connected, the **Status** column fills in as songs are looked up.
2. Check the list. Double-click or press Enter to edit a title, artist or album; Tab moves to
   the next field. Select several rows and press **Cmd/Ctrl+I** to set the artist, album or
   artwork for all of them at once. Dropping an image on selected rows sets their artwork.
3. Type a playlist name, or pick a recent one.
4. Click **Add to Spotify** (or **Prepare for Spotify** when not connected). If some matches
   need checking, Sleeve asks first. Each row shows its progress, and **Cancel** stops the run.
5. Follow the short checklist that appears:
   - **First time only:** in Spotify **Settings**, turn on **Show Local Files**. Under
     **Show songs from**, click **Add a source** and choose the `Sleeve` folder in your Music
     folder. Sleeve creates this folder when it starts, and every playlist folder sits inside
     it, so you only do this once (**Help › Setup guide** has **Copy folder path** and
     **Show folder** buttons).
   - In Spotify, open **Your Library › Local Files**.
   - Select the new tracks and drag them into your playlist.

| Shortcut | Action |
|---|---|
| Cmd/Ctrl+O | Add files |
| Cmd/Ctrl+Shift+O | Add folder |
| Cmd/Ctrl+Shift+I | Import playlist |
| Cmd/Ctrl+Shift+M | Check Spotify matches |
| Enter | Edit the selected cell |
| Tab / Shift+Tab | Next / previous field while editing |
| Delete or Backspace | Remove selected rows from the list |
| Cmd/Ctrl+A | Select all |
| Cmd/Ctrl+Z | Undo tag edits and removals (redo: Cmd+Shift+Z / Ctrl+Y) |
| Cmd/Ctrl+I | Edit selected tracks |
| Cmd/Ctrl+Return | Add to Spotify / Prepare for Spotify |

### Listening on your phone

Local files on a phone need **Spotify Premium**, and the phone and computer must be on the
**same Wi-Fi network**.

1. On the computer, add the tracks to a playlist and keep Spotify open.
2. On the phone, turn on **Local audio files** in Spotify's settings. On iPhone, allow
   **Local Network** access when asked.
3. Open the playlist on the phone and tap **Download**. The tracks copy over Wi-Fi.

If tracks stay grayed out, check that both devices use the same account and network (not a
guest network), then restart Spotify on both.

### Connecting Spotify

Click **Connect Spotify…** in the bottom bar; a short guide walks through it. Since February
2026 Spotify only gives personal apps this access, so you create your own app once:

1. In the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create an app
   (any name) and tick **Web API**. The account that owns it needs Premium.
2. Add the redirect URI Sleeve shows (default `http://127.0.0.1:43817/callback`).
3. Under **User Management**, add the Spotify account you'll connect with (up to five people).
4. Paste the app's **Client ID** into Sleeve and click **Connect**.

Sign-in uses the Authorization Code flow with PKCE, so there is no client secret, and the token
is kept in the macOS Keychain or Windows Credential Manager. Search results are cached for 30
days so repeat runs are fast. If Spotify slows requests down, Sleeve waits and carries on; if it
refuses them (expired sign-in, not on the user list, `QUOTA_EXCEEDED`), Sleeve says so in plain
language and still prepares your local files.

### Where things are

| What | macOS | Windows |
|---|---|---|
| Prepared tracks | `~/Music/Sleeve/` | `%USERPROFILE%\Music\Sleeve\` |
| Settings | `~/Library/Application Support/Sleeve/` | `%APPDATA%\Sleeve\` |
| Log (Help › Show log) | `~/Library/Logs/Sleeve/` | `%LOCALAPPDATA%\Sleeve\Logs\` |

Sleeve sends no telemetry. It makes no network requests except to Spotify, and only after you
connect an account.

## Development

Requires Python 3.12.

```sh
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m sleeve
```

The `dev` extra installs `imageio-ffmpeg`, which supplies an FFmpeg binary during development.
Set `SLEEVE_FFMPEG=/path/to/ffmpeg` to use a different one, and `SLEEVE_HOME=/tmp/x` to keep
settings, logs and output away from your real folders.

```sh
ruff check . && ruff format --check .
mypy
QT_QPA_PLATFORM=offscreen pytest
```

The tests cover file-name cleanup, format detection (including DRM), tag reading and writing,
duplicate detection, match scoring, M3U and iTunes library import, the full prepare pipeline
with real FFmpeg conversions, the Spotify client and sign-in flow, and the main window driven
end to end with pytest-qt against a fake Spotify Web API.

### Layout

```
src/sleeve/
  core/       names, formats, tags, ffmpeg, dedupe, pipeline, matching, library (no Qt)
  spotify/    PKCE sign-in, token storage, catalog search, playlists, matcher (no Qt)
  ui/         main window, track table and model, dialogs, background workers
  app.py      entry point
packaging/    PyInstaller spec, FFmpeg fetcher, icons, macOS and Windows build scripts
tests/
```

## Packaging

Both builds bundle a pinned FFmpeg from the `imageio-ffmpeg` 0.6.0 wheels, verified by
SHA-256 (`packaging/fetch_ffmpeg.py`). CI builds both installers on every push to `main`, and
you can download them from the workflow run's artifacts.

### macOS (.app and .dmg)

```sh
packaging/macos/build.sh
```

This produces `dist/Sleeve.app` and `dist/Sleeve-<version>.dmg`. To sign and notarize, set
these before running the script:

```sh
export SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
xcrun notarytool store-credentials sleeve-notary --apple-id you@example.com --team-id TEAMID
export NOTARY_PROFILE=sleeve-notary
```

The script signs the bundled `ffmpeg` and the app with the hardened runtime
(`packaging/macos/entitlements.plist`), builds the dmg, then notarizes and staples it. Build on
an Apple silicon Mac for an arm64 app; for Intel, build on an Intel Mac (or under Rosetta with
an x86_64 Python).

### Windows (installer)

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php), then run in PowerShell:

```powershell
packaging\windows\build.ps1
```

This produces `dist\Sleeve\` and `dist\Sleeve-<version>-setup.exe`. To sign, set
`$env:SIGN_CERT_THUMBPRINT` to a code-signing certificate in your store; the script signs the
app, FFmpeg and the installer with `signtool`.

### Licenses

Sleeve's source is MIT. The installers include FFmpeg (GPL build) and Mutagen (GPL-2.0+), so
they are distributed under GPL-3.0 as a whole. See [THIRD_PARTY.md](THIRD_PARTY.md) for every
component, its license and its source. Spotify is a trademark of Spotify AB; Sleeve is not
affiliated with Spotify.
