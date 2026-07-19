# Plex Open Web

A small self-hosted browser UI for your own Plex Media Server. It talks to the Plex API, shows your libraries, proxies posters, and streams media parts through an authenticated web app.

This is meant to avoid Plex cloud remote-access/client limits by using your own web endpoint. It does not bypass Plex DRM, account security, or paid Plex features.

## Features

- Password-protected web UI with signed HttpOnly session cookies.
- Library list, continue/recent/all/unwatched views, persistent genre filtering, sorting, and search.
- Native Plex collection browsing with composite posters, item counts, paging, and collection-to-movie navigation.
- Movie collection membership management with searchable, immediate add/remove controls.
- Manual collection creation, rename, and confirmed deletion without removing library movies.
- Plex Fix Match for movies and TV shows, with title/year/language search, ranked poster-backed candidates, and confirmed metadata replacement.
- Permanent movie and episode deletion with an exact disk preview, typed confirmation, hardlink cleanup, and safe folder pruning.
- Server-backed My List shared with the Android app, with per-library browsing and poster badges.
- Surprise Me selection for opening a random item from the active genre and Unwatched filters.
- Persistent library, view, genre, and sort context across reloads and sign-in sessions.
- Resume-progress indicators and manual watched/unwatched controls synchronized with Plex.
- Persistent System, Light, and Dark color themes available before and after login.
- One-tap scanning for the selected Plex library with progress feedback and an automatic result reload.
- Movie playback in the browser through a Range-aware stream proxy.
- TV show navigation from show to season to episode.
- Previous/next episode navigation with optional persisted autoplay and a cancellable Up Next countdown.
- Persistent per-item subtitle selection from Plex subtitle streams and sidecar subtitle files, including an explicit remembered Off choice.
- Optional OpenSubtitles search/download. Downloads are saved beside the video as Plex-style sidecar files and are immediately available in this player.
- Original media download as a ZIP containing the untouched video file and available subtitles.
- Seekable Plex VOD playback for Safari and Apple-device browsers when video or audio conversion is required.
- Codec-aware FFmpeg fallback that converts unsupported video such as HEVC to H.264 and unsupported audio such as AC3 to AAC.
- Poster/artwork proxy so the browser only needs this app URL.
- Compressed API responses, right-sized artwork, and coalesced Plex reads for fast browsing over a tailnet.
- Cancellable library/search requests, stable loading placeholders, and incremental card rendering for responsive interaction.
- No runtime dependencies beyond Python 3 standard library.

## Release notes

Release notes cover user-facing changes and intentionally omit deployment-specific and private details.

### 0.18.0

**Added**

- Added persistent subtitle choices for every movie and episode, including a remembered `Off` selection.
- Added an authenticated subtitle-selection endpoint that validates the requested media part and subtitle stream before updating Plex.
- Added a browser-side fallback for locally added subtitle files that Plex has not indexed yet.

**Improved**

- Reopening the player, reloading the web app, or switching between live, saved, and device playback restores the last subtitle selected for that item.
- Plex-backed choices are shared with other Plex clients, while the app keeps its own stable per-item identity for sidecar and OpenSubtitles tracks.
- Subtitle preferences are bounded to the 500 most recently changed items so browser storage remains small.

**Fixed**

- Fixed manually selected subtitles reverting to Plex's default track after the player or browser restarted.
- Fixed an explicit `Off` choice being lost when the same movie or episode was opened again.

### 0.17.0

**Added**

- Added an authenticated proxy for Plex's on-demand HLS transcoder, providing complete movie and episode durations before every segment has been generated.
- Added explicit VOD playlist signaling and safe on-demand segment routing for native Safari playback.

**Improved**

- Compatible playback can start immediately while retaining the normal duration, seek bar, elapsed time, and remaining-time controls.
- Seeking can jump directly to any point in a movie; Plex generates the requested segment on demand instead of waiting for a full-file conversion.
- Repeated playlist requests reuse one transcode session, and sessions are stopped when the player closes, changes source, or leaves the page.
- Expected browser disconnects from cancelled seek segments are handled quietly without noisy server tracebacks.
- Existing bounded FFmpeg HLS generation remains available as a fallback for older clients without an item identity.

**Fixed**

- Fixed some AC3, EAC3, DTS, and other compatibility streams appearing as `Live Broadcast` in Safari.
- Fixed Safari temporarily hiding the movie timeline and normal playback controls while a growing event playlist was being prepared.
- Plex authentication tokens remain server-side while manifests and segments pass through the app's existing signed-session boundary.

### 0.16.0

**Added**

- Added Fix Match to movie and TV show details, using the active Plex library's own metadata agent and language.
- Added automatic candidate search by title and year, plus manual searches by title, IMDb ID, TMDB ID, or TVDB ID.
- Added Plex-ranked results with posters, summaries, release years, Best match and Current labels, and an explicit confirmation before applying a match.
- Added Refresh match for downloading fresh metadata again when the existing Plex GUID is already correct but its poster or description is stale.

**Improved**

- Match changes refresh the visible title, poster, description, breadcrumbs, and media grid without requiring a page reload.
- In-flight searches are cancelled when a newer search starts or the dialog closes, and controls remain stable while Plex applies a match.
- Match result images are restricted to local Plex artwork or Plex's official image service.

**Fixed**

- Fresh post-match metadata bypasses the normal short-lived details cache, preventing the old title or poster from immediately reappearing.
- Fix Match is limited to movie and show records, matching Plex's show-level behavior and preventing episode-level or cross-type matches.

### 0.15.4

**Added**

- Added automatic H.264 conversion for HEVC and other video codecs that browsers cannot reliably render through the authenticated stream.
- Added distinct player status labels for video conversion, audio conversion, and combined H.264 plus AAC playback.

**Improved**

- Original-resolution H.264 conversion uses browser-safe 8-bit 4:2:0 output and regular keyframes while remaining comfortably faster than real time on the media server.
- HLS cache identities now include the video conversion mode so copied-video and H.264 streams can never reuse incompatible segments.

**Fixed**

- Fixed HEVC Main 10 television episodes playing sound over a black picture in Safari.
- Unsupported video now selects the compatibility stream even when its audio track is already browser-compatible.

### 0.15.3

**Improved**

- Native HLS playlists now declare the beginning as their preferred start point even while FFmpeg is still generating later segments.

**Fixed**

- New Safari playback now begins at the start of an episode instead of jumping to the newest segment generated ahead of real time.
- Existing episode resume positions are still applied after HLS metadata becomes available.

### 0.15.2

**Added**

- Added native HLS playback for Safari when unsupported source audio must be converted to AAC, with four-second independently playable segments for quick startup.
- Added a bounded HLS cache that reuses completed streams, expires inactive sessions automatically, and prunes older completed streams toward a configurable disk limit.

**Improved**

- Safari and Apple-device browsers now use their supported streaming transport, while Chrome and Firefox retain the lightweight live MP4 path.
- HLS playlists are updated atomically while conversion runs, and finished segments support byte ranges and private immutable caching.

**Fixed**

- Fixed the remaining Safari `FormatError` on chunked MP4 television episodes by replacing that Apple-specific path with native HLS delivery.
- Multiple requests for the same episode now share one HLS conversion instead of starting duplicate FFmpeg work.

### 0.15.1

**Improved**

- Live audio conversion now sends a populated delayed MP4 initialization header, preserving immediate playback while remaining compatible with Safari's media stack.
- Playback failures now show a clear in-player message with the existing saved-copy recovery path instead of leaving an unexplained black frame.

### 0.15.0

**Added**

- Added Delete from disk to movie and episode details, backed by a server-generated preview of every file and complete folder that will be removed.
- Added a dedicated irreversible-action dialog that requires typing `DELETE` exactly before the permanent command becomes available.
- Added approved-root hardlink discovery so deleting an episode also removes linked source copies that would otherwise keep the disk data alive.
- Added local deletion audit entries and qBittorrent download detection with active-download blocking and completed-torrent warnings.

**Improved**

- Movie releases stored in dedicated folders now remove the complete release folder, including subtitles, artwork, and release metadata.
- Shared movie folders preserve unrelated videos and remove only the selected movie plus matching sidecars.
- Episode deletion preserves sibling episodes and prunes only directories that become empty.
- Hardlinked copies are located with a targeted inode lookup, keeping real multi-library previews responsive even across large media roots.
- Successful deletion clears My List and resume state immediately, requests Plex metadata removal, starts a library refresh, and updates the visible grid without a full reload.

**Fixed**

- Media can now be deleted when Plex's own service account lacks write permission but the Plex Open Web service account owns the configured media folders.
- A confirmation cannot be replayed for another item, reused after expiry, or executed after any planned file or folder changes.
- Media outside explicitly approved roots, symlinked originals, library roots, and hardlinks outside approved roots are rejected before disk changes begin.
- Read-only or permission-blocked media folders are reported in the preview and cannot reach the permanent confirmation action.

### 0.14.0

**Added**

- Added one authenticated startup response containing session state, server identity, libraries, My List keys, genres, and the first media page.
- Added a combined browse endpoint that loads a library's genres and first media page concurrently.
- Added a visible Delete action to manual collection cards, with confirmation that the movies remain in Plex.
- Added hover and keyboard-focus metadata prefetch plus in-flight request sharing, making Details and Play react faster without duplicate metadata calls.
- Added direct server-side collection deletion by library and collection ID, while keeping smart collections read-only.

**Improved**

- Cold startup median fell from 110.9 ms to 61.2 ms in the release benchmark, while the slowest measured cold run fell from 531.4 ms to 87.9 ms.
- Cold library switching fell from a 91.7 ms median to 54.8 ms, and warm startup fell from 5.12 ms to 3.02 ms.
- Browse responses now use short private client caching with stale-on-error support, while server-side library and search entries remain mutation-aware.
- Repeated metadata reads are coalesced and cached for ten seconds, with all API caches invalidated by mutations.
- Plex browse requests no longer ask the server to compute GUID data that card rendering does not use.
- Collection deletion updates the current grid immediately instead of forcing an extra library round trip.

**Fixed**

- Startup no longer performs separate session, bootstrap, genre, and media requests in sequence.
- Signed-out startup checks do not contact Plex or expose library data.
- A collection can now be deleted directly from the Collections view instead of requiring an unrelated movie as an entry point.
- Smart collection deletion remains blocked in both the interface and server API.

### 0.13.0

**Added**

- Added a single authenticated bootstrap request that returns server identity, libraries, My List keys, and app version together.
- Added layout-stable library placeholders and an immediate Preparing state when playback is selected.
- Added bounded, mutation-aware request coalescing for repeated library, genre, search, children, server, and library-list reads.
- Added regression coverage for concurrent request coalescing, compressed JSON, right-sized Plex artwork, lightweight browse records, and bootstrap output.

**Improved**

- Large API responses now use gzip when supported. In the release benchmark, a 60-item library page fell from 199,134 bytes to 29,528 bytes over the wire.
- Posters now use Plex's photo transcoder at 480 by 720 pixels with long-lived immutable client caching. The measured first poster fell from 1,838,960 bytes to 131,204 bytes.
- Warm 60-item library responses fell from a 79.6 ms median to 3.1 ms, while an eight-client burst fell from 1,112 ms to 260 ms.
- Browse records skip saved-file filesystem checks and external-ID parsing until full metadata is requested.
- Versioned JavaScript and CSS use immutable caching, while server-side static bytes are retained in memory.
- Library paging appends only new cards, uses one delegated grid listener, defers off-screen rendering, and prioritizes first-viewport poster decoding.
- Phone layouts use compact horizontal library and view strips plus a denser filter grid, bringing the first movie posters into the initial viewport.
- Playback metadata and browser-device availability are prepared concurrently, and subtitle search opens without an unnecessary metadata round trip.
- The HTTP server uses a larger connection queue and daemon request workers for cleaner recovery under bursts and shutdowns.

**Fixed**

- Slower obsolete library and search responses can no longer overwrite a newer library, filter, sort, view, or query selection.
- Refresh failures preserve the last usable grid instead of replacing it with a blank screen.
- Closing the player invalidates unfinished playback preparation, preventing a late response from reopening or changing closed playback state.
- Missing streams now leave a clear No playable source state instead of an apparently unresponsive player.
- Plex thumbnail resize parameters are now sent through the actual photo-transcode endpoint instead of being ignored by direct thumbnail URLs.
- Repeated saved-playback status reads now reuse one filesystem stat result.

### 0.12.0

**Added**

- Added New collection creation directly from a movie's collection manager; the movie is included immediately.
- Added Rename and Delete actions for every manual Plex collection.
- Added a focused rename dialog and an explicit deletion confirmation explaining that movies remain in the library.

**Improved**

- Collection creation, rename, and deletion use Plex's native library operations and refresh authoritative names, counts, and memberships afterward.
- Manual collection rows now combine membership, movie count, and lifecycle actions while remaining responsive on phone-sized screens.
- Duplicate-name and validation errors are translated into clear collection-specific feedback.
- Renaming the currently open collection updates its breadcrumb immediately.

**Fixed**

- Deleting the currently open collection now closes stale detail dialogs and returns to the refreshed collection library.
- Existing collection names are compared case-insensitively before create or rename operations.
- Empty, control-character, oversized, cross-library, missing, and smart-collection mutation requests are rejected before Plex is changed.
- Deleting a collection removes only the collection container; its movies remain untouched in the Plex library.

### 0.11.0

**Added**

- Added a Collections action to movie details for adding or removing the movie from existing Plex collections.
- Added a searchable collection checklist with membership counts and immediate updates.

**Improved**

- Collection changes use Plex's native collection IDs and refresh membership from Plex after every operation.
- Collection counts in movie details and open collection contents update without a page reload.
- Smart collections remain visible with a clear read-only explanation because Plex controls their membership automatically.

**Fixed**

- Collection membership is validated against the movie's library before Plex is changed.
- Invalid item IDs, invalid collection IDs, unsupported media types, and non-boolean membership requests are rejected.
- Repeated add or remove requests are idempotent and do not issue duplicate Plex mutations.

### 0.10.0

**Added**

- Added Previous and Next episode actions to episode details.
- Added an in-player Next episode control and a persisted Auto next checkbox.
- Added a five-second Up Next countdown with a one-time Cancel action.

**Improved**

- Episode order comes from Plex's native show feed and continues correctly across season boundaries.
- The player changes episodes in place while refreshing playback, subtitle, save, download, and device-copy controls.
- Adjacent episode metadata is cached per item so reopening details or playback does not repeat unnecessary lookups.

**Fixed**

- The details dialog now closes before playback starts, avoiding stacked media dialogs.
- Auto next stops cleanly at the final available episode and does not block playback if neighbor metadata is unavailable.
- Browser device object URLs are released when continuation switches back to a live stream.
- Cancelled stream or subtitle requests no longer trigger a second server error after the client disconnects.

### 0.9.0

**Added**

- Added a persistent My List shared by the web and Android clients.
- Added Add to My List and Remove from My List actions for movies, shows, and episodes.
- Added a paged My List library view and poster badges for saved items.

**Improved**

- My List refreshes from the server when opened so changes from another client appear immediately.
- Saved metadata is loaded in efficient Plex batches while preserving newest-first order.
- Six library views use balanced controls on desktop and in a three-by-two mobile layout.

**Fixed**

- My List state is stored atomically outside the deployed source tree so deployments and service restarts preserve it.
- Invalid item identifiers and unsupported media types are rejected before saved state changes.
- Empty My List libraries now show a specific empty-state message and disable unrelated sort, genre, and random controls.

### 0.8.0

**Added**

- Added a server-backed genre selector populated from the active Plex library.
- Added authenticated genre discovery and filtered-library API support shared with Android.
- Added independent saved genre choices for each library.

**Improved**

- Genre filters work with paging, sorting, Continue, Recent, All, and Unwatched views.
- Surprise Me now respects the selected genre and limits picks to unwatched media from the Unwatched view.
- Desktop and mobile toolbars accommodate both genre and sort controls without horizontal overflow.

**Fixed**

- Switching libraries no longer carries an unrelated genre identifier into the new library.
- Removed or invalid saved genres fall back to All genres instead of producing an empty library.
- Collections temporarily disables the unrelated genre filter while preserving it for other views.

### 0.7.0

**Added**

- Added a Surprise Me action that chooses a random item from the selected library and opens its details.
- Added per-browser persistence for the last library, view, and sort selection.
- Added a validated random-item API shared by the web and Android clients.

**Improved**

- Random selection reads only the library count and one chosen item, remaining fast for large libraries.
- Mobile toolbar actions now wrap into a stable sort row and two equal command buttons.
- Restored Continue and Collections views automatically keep sorting disabled as expected.

**Fixed**

- Reloading no longer always resets browsing to the first library, All view, and Recently added sort.
- Missing libraries and invalid or obsolete saved view values fall back safely to supported defaults.

### 0.6.0

**Added**

- Added a Collections view for each Plex library, including native collection artwork and item counts.
- Collection cards open directly into their movies while preserving breadcrumb and back navigation.

**Improved**

- Collections are listed alphabetically and use the same paging behavior as the rest of the library.
- The five library views use a balanced two-row layout on narrow screens, keeping every option visible without horizontal page overflow.
- Collection cards have a clear badge and dedicated Open action.

**Fixed**

- Fixed Plex composite collection posters whose source URLs contain width and height parameters.
- Cancelled lazy-image requests no longer produce avoidable server errors.
- Collection directories are now treated as browsable containers instead of unplayable media details.

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

Optional app settings:

- `APP_DATA_DIR`: persistent directory for server-owned state such as My List; defaults outside the source directory.

Optional compatible-playback settings:

- `FFMPEG_PATH`: FFmpeg executable used for audio conversion, saved copies, embedded subtitles, and HLS playback.
- `SAVED_MEDIA_DIR`: storage for prepared MP4 files and temporary HLS sessions; defaults outside the source directory.
- `HLS_CACHE_TTL`: inactive Plex VOD session and fallback HLS cache lifetime; defaults to four hours.
- `HLS_CACHE_MAX_BYTES`: target maximum for completed inactive fallback HLS sessions; defaults to 6 GiB, while an active playback session is always preserved.
- `HLS_STARTUP_TIMEOUT`: maximum wait for Plex VOD setup or the first fallback HLS segment; defaults to 15 seconds.
- `HLS_TRANSCODE_TIMEOUT`: maximum fallback HLS generation time; defaults to four hours.

Optional permanent media deletion settings:

- `MEDIA_DELETE_ENABLED`: set to `1` to expose permanent movie and episode deletion in authenticated clients. It is disabled by default.
- `MEDIA_DELETE_ROOTS`: colon-separated absolute media roots. Every original and hardlink must stay inside these roots.
- `MEDIA_DELETE_PLAN_TTL`: lifetime in seconds for a signed deletion preview; defaults to five minutes.
- `QBITTORRENT_BACKUP_DIR`: optional qBittorrent `BT_backup` directory used to detect active or still-managed downloads.

The service account must be able to write the approved roots. Do not approve a root broader than the actual media libraries. A changed plan always requires a new preview and confirmation.

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

- Direct browser playback depends on the source file codec/container being supported by the browser. MP4/H.264/AAC is the safest. Safari-compatible conversion uses Plex's on-demand VOD transcoder, with FFmpeg retained for saved copies and fallback playback.
- OpenSubtitles downloads require the app user to have write access to the media folder so it can save the sidecar subtitle file.

## Security

- Do not commit `.env`, Plex tokens, OpenSubtitles credentials, API keys, or app passwords.
- Keep this app behind Tailscale or another private network unless you intentionally harden it for public internet exposure.
- Use `APP_SECURE_COOKIE=1` when serving through HTTPS.
