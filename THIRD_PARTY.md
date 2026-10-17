# Third-party software

Sleeve's own source code is MIT licensed (see `LICENSE`). Installers bundle the components
below. Because Mutagen and the bundled FFmpeg build are GPL, the installers as a whole are
distributed under the GNU GPL version 3. The source for every component is linked here.

| Component | Used for | License | Source |
|---|---|---|---|
| FFmpeg 7.1 (static build from imageio-ffmpeg 0.6.0) | Converting audio to MP3/AAC | GPL-3.0-or-later (built with `--enable-gpl --enable-version3`) | https://ffmpeg.org/download.html, build scripts: https://github.com/imageio/imageio-binaries |
| LAME (inside FFmpeg) | MP3 encoding | LGPL-2.0-or-later | https://lame.sourceforge.io |
| Qt 6 / Qt for Python (PySide6) | User interface | LGPL-3.0 | https://code.qt.io, https://wiki.qt.io/Qt_for_Python |
| Mutagen | Reading and writing tags | GPL-2.0-or-later | https://github.com/quodlibet/mutagen |
| keyring | Storing the optional Spotify token in the OS keychain | MIT | https://github.com/jaraco/keyring |
| Python | Runtime | PSF License | https://www.python.org |

## Notes for redistributors

- FFmpeg runs as a separate program (`ffmpeg/ffmpeg` inside the app). It can be replaced with
  any FFmpeg build that has the `libmp3lame` and `aac` encoders; set `SLEEVE_FFMPEG` to point
  at a different binary for testing.
- Qt is linked dynamically through PySide6, so users can swap the Qt libraries in the
  installed app, as LGPL-3.0 requires.
- `packaging/vendor/ffmpeg/SOURCE.txt` (copied into the app) records the exact FFmpeg build.
- Spotify is a trademark of Spotify AB. Sleeve is not affiliated with or endorsed by Spotify.
