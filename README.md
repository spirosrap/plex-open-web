# Plex Open Web

A small self-hosted browser UI for your own Plex Media Server. It talks to the Plex API, shows your libraries, proxies posters, and streams media parts through an authenticated web app.

This is meant to avoid Plex cloud remote-access/client limits by using your own web endpoint. It does not bypass Plex DRM, account security, or paid Plex features.

## Features

- Password-protected web UI with signed HttpOnly session cookies.
- Library list, continue/recent/all/unwatched views, sorting, and search.
- Resume-progress indicators and manual watched/unwatched controls synchronized with Plex.
- Persistent System, Light, and Dark color themes available before and after login.
- One-tap scanning for the selected Plex library with progress feedback and an automatic result reload.
- Movie playback in the browser through a Range-aware stream proxy.
- TV show navigation from show to season to episode.
- Browser subtitle selection from Plex subtitle streams and sidecar subtitle files.
- Optional OpenSubtitles search/download. Downloads are saved beside the video as Plex-style sidecar files and are immediately available in this player.
- Original media download as a ZIP containing the untouched video file and available subtitles.
- FFmpeg fallback that copies browser-compatible video and converts unsupported audio, such as AC3, to AAC.
- Poster/artwork proxy so the browser only needs this app URL.
- No runtime dependencies beyond Python 3 standard library.

## Release notes

Release notes cover user-facing changes and intentionally omit deployment-specific and private details.

### 0.5.0

**Added**

- Added System, Light, and Dark color-theme selectors to the login and signed-in app.
- Added per-browser theme persistence across reloads and sign-out.

**Improved**

- System mode follows the browser or operating-system color preference automatically.
- Theme colors now cover forms, navigation, cards, dialogs, subtitle tools, playback controls, status messages, and focus states.
- The saved theme is applied before the first paint to prevent a light-to-dark or dark-to-light flash.

**Fixed**

- Fixed low-contrast fields, metadata labels, danger actions, and selected controls when using the Light theme.
- Theme changes remain usable for the current page even when browser storage is unavailable.

### 0.4.0

**Added**

- Added a Continue view for in-progress media and the next available TV episodes in each library.
- Added Mark watched and Mark unwatched actions to movie and episode details.
- Added resume-progress bars to media posters and progress status to details.

**Improved**

- Continue preserves Plex's On Deck ordering and disables the unrelated library sort control.
- Continue and Unwatched automatically reload after playback or a manual watched-state change.
- Added regression tests for Continue paging, watched-state validation, and Plex watched/unwatched actions.

**Fixed**

- Manual watched-state changes now clear stale browser resume positions.
- Media cards now refresh their progress and watched state after the player closes.
- Player cleanup and progress reporting now also run when the dialog is dismissed with browser-native controls.
- Continue excludes fully watched entries even when Plex retains an old On Deck offset.
- Unsupported media types and invalid watched-state requests are rejected before changing Plex data.

### 0.3.0

**Added**

- Added authenticated one-tap scanning for the selected Plex library.
- Added scan progress, success, and error feedback.

**Improved**

- The Scan library control is disabled while a scan is running.
- The selected library automatically reloads after Plex accepts the scan.

**Fixed**

- Invalid or missing library section identifiers are rejected before Plex is asked to scan.
- Scan failures remain visible instead of looking like a successful refresh.

### 0.2.2

**Maintenance**

- Updated release metadata only; no user-facing behavior changed.

### 0.2.1

**Improved**

- Moved the version label into the signed-in app identity area while keeping it visible on the login screen.
- Added server-injected versioning to HTML and asset URLs so releases automatically load fresh CSS and JavaScript.

**Fixed**

- Fixed generic or stale version labels when frontend startup or browser caching was out of date.

### 0.2.0

**Added**

- Added original-media ZIP downloads containing the untouched video file and available subtitles.
- Added playback progress reporting, local resume positions, and Plex watched-state updates.
- Added browser-device save, play, and delete actions for cached MP4 and VTT files.
- Added the app version to health and session responses and displayed it in the interface.

**Improved**

- Expanded support for Plex, sidecar, embedded, and OpenSubtitles subtitle sources.
- Added server-saved browser-compatible playback and clear playback-mode indicators.
- Increased the browser device cache to approximately 12 GB with automatic 14-day pruning.

**Fixed**

- Playback progress is preserved locally when server reporting temporarily fails.
- Completed playback clears stale resume positions and marks media as watched.

## Run locally

```bash
cp .env.example .env
python3 server.py
```

Required settings:

- `APP_PASSWORD`: password for the web app login.
- `APP_SECRET`: long random string used to sign sessions.
- `PLEX_BASE_URL`: usually `http://127.0.0.1:32400` when running on the Plex host.
- `PLEX_TOKEN`: a token for your Plex server.

Optional subtitle search/download settings:

- `OPENSUBTITLES_API_KEY`: your OpenSubtitles API key.
- `OPENSUBTITLES_USERNAME`: your OpenSubtitles username.
- `OPENSUBTITLES_PASSWORD`: your OpenSubtitles password.
- `OPENSUBTITLES_USER_AGENT`: app user agent registered for your API key.

## Deploy on your Plex host

Copy the folder to the server, create `.env`, then install the systemd service:

```bash
cd ~/plex-open-web
chmod +x scripts/install-systemd.sh
./scripts/install-systemd.sh
```

The app listens on `APP_PORT`, default `5055`.

## Tailscale remote access

If the app runs on a machine in your tailnet, Tailscale Serve can expose it as an HTTPS tailnet-only URL:

```text
https://your-host.your-tailnet.ts.net/
```

The direct Tailscale app URL can also work:

```text
http://100.x.y.z:5055/
```

The helper below configures Tailscale Serve and installs a narrow route exclusion for ports `443` and `5055`, which can help when a VPN routes normal tailnet TCP replies away from `tailscale0`:

```bash
./scripts/install-tailscale-remote.sh
```

For public internet access, use a separate HTTPS reverse proxy or Tailscale Funnel intentionally. This setup is tailnet-only.

## Notes

- Direct browser playback depends on the source file codec/container being supported by the browser. MP4/H.264/AAC is the safest. Unsupported audio can be converted to AAC with FFmpeg when `ffmpeg` is installed.
- OpenSubtitles downloads require the app user to have write access to the media folder so it can save the sidecar subtitle file.

## Security

- Do not commit `.env`, Plex tokens, OpenSubtitles credentials, API keys, or app passwords.
- Keep this app behind Tailscale or another private network unless you intentionally harden it for public internet exposure.
- Use `APP_SECURE_COOKIE=1` when serving through HTTPS.
