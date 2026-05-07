# Plex Open Web

A small self-hosted browser UI for your own Plex Media Server. It talks to the Plex API, shows your libraries, proxies posters, and streams media parts through an authenticated web app.

This is meant to avoid Plex cloud remote-access/client limits by using your own web endpoint. It does not bypass Plex DRM, account security, or paid Plex features.

## Features

- Password-protected web UI with signed HttpOnly session cookies.
- Library list, recent/all/unwatched views, sorting, and search.
- Movie playback in the browser through a Range-aware stream proxy.
- TV show navigation from show to season to episode.
- Browser subtitle selection from Plex subtitle streams and sidecar subtitle files.
- Optional OpenSubtitles search/download. Downloads are saved beside the video as Plex-style sidecar files and are immediately available in this player.
- FFmpeg fallback that copies browser-compatible video and converts unsupported audio, such as AC3, to AAC.
- Poster/artwork proxy so the browser only needs this app URL.
- No runtime dependencies beyond Python 3 standard library.

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
