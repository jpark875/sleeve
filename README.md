# Sleeve

A small desktop utility for macOS and Windows that gets your own audio files ready to play in
Spotify. Drop in files or a folder, fix the titles, artists and artwork, pick a playlist name,
and click **Prepare for Spotify**. Sleeve converts what Spotify can't play, writes clean tags,
names the files consistently and saves everything into one folder that Spotify reads.

<p align="center"><img src="packaging/icons/icon-1024.png" width="128" alt=""></p>

## How it works, and what it can't do

Spotify has no way to upload audio through its API. The only supported route is the desktop
app's **Local Files** feature: Spotify shows audio files from folders on your computer, and you
drag them into playlists yourself. Sleeve automates everything around that step:

- **Formats.** MP3 and M4A (AAC) are copied as they are. WAV, AIFF, FLAC, ALAC, Ogg, Opus and
  WMA are converted with FFmpeg to MP3 (320 kbps constant bitrate) or M4A (AAC, 256 kbps).
- **Videos.** MP4, MOV, M4V, WebM, MKV and AVI files are accepted too; Sleeve keeps only the
  audio. For your own YouTube uploads, download the original from YouTube Studio (Content ›
  ⋮ › Download) and drop it in. Sleeve doesn't download from YouTube links.
- **Tags.** Spotify displays local files using their embedded tags. Sleeve reads existing tags,
  falls back to a cleaned-up file name (`01 - my_song_FINAL v2.wav` becomes `My Song`), and
  writes title, artist, album, track number and artwork into each prepared file.
- **Files.** Output goes to `~/Music/Sleeve/<Playlist name>/` as `Artist - Title.mp3`.
  Originals are only ever read, never changed or deleted.
- **Duplicates.** Files that are already in the output folder are skipped, and so are
  identical files added twice.
- **Copy protection.** DRM-protected files (for example some `.m4p` or `.wma` purchases) are
  skipped with a plain explanation. Sleeve never removes DRM.

The last step, dragging the tracks into the playlist, has to happen in the Spotify desktop
app. Spotify's Web API can't add local files to playlists.

## Using Sleeve

1. Drag audio files, videos or a folder onto the window, or click **Add files…**.
2. Check the list. Double-click or press Enter to edit a title, artist or album; Tab moves to
   the next field. Select several rows and press **Cmd/Ctrl+I** to set the artist, album or
   artwork for all of them at once. Dropping an image on selected rows sets their artwork.
3. Type a playlist name, or pick a recent one.
4. Click **Prepare for Spotify**. Each row shows its progress, and **Cancel** stops the run.
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
| Enter | Edit the selected cell |
| Tab / Shift+Tab | Next / previous field while editing |
| Delete or Backspace | Remove selected rows from the list |
| Cmd/Ctrl+A | Select all |
| Cmd/Ctrl+Z | Undo tag edits and removals (redo: Cmd+Shift+Z / Ctrl+Y) |
| Cmd/Ctrl+I | Edit selected tracks |
| Cmd/Ctrl+Return | Prepare for Spotify |

### Listening on your phone

Local files on a phone need **Spotify Premium**, and the phone and computer must be on the
**same Wi-Fi network**.

1. On the computer, add the tracks to a playlist and keep Spotify open.
2. On the phone, turn on **Local audio files** in Spotify's settings. On iPhone, allow
   **Local Network** access when asked.
3. Open the playlist on the phone and tap **Download**. The tracks copy over Wi-Fi.

If tracks stay grayed out, check that both devices use the same account and network (not a
guest network), then restart Spotify on both.

### Optional: connect Spotify

Connecting lets Sleeve create the playlist for you and open it in the Spotify app, so you only
have to drag the tracks in. Everything else works without it.

Since February 2026, Spotify only gives Web API access to apps in Development Mode whose
owner has Premium, with up to five allowlisted users per app. So you need your own app:

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and
   create an app. Choose **Web API**.
2. Add the redirect URI shown in Sleeve's Settings (default
   `http://127.0.0.1:43817/callback`).
3. Under **User Management**, add the Spotify account you'll connect with.
4. Copy the app's **Client ID** into Sleeve's Settings and click **Connect…**.

Sleeve uses the Authorization Code flow with PKCE, so there is no client secret. The token is
kept in the macOS Keychain or Windows Credential Manager. If Spotify refuses a request
(expired sign-in, not on the user list, rate limit, or `QUOTA_EXCEEDED`), Sleeve says so in
plain language and the prepared files are still there.

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
duplicate detection, the full prepare pipeline with real FFmpeg conversions, the Spotify
client and sign-in flow against a fake server, and the main window driven with pytest-qt.

### Layout

```
src/sleeve/
  core/       names, formats, tags, ffmpeg, dedupe, pipeline, errors (no Qt)
  spotify/    PKCE sign-in, token storage, playlist client (no Qt)
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
