const state = {
  libraries: [],
  selectedLibrary: null,
  selectedView: "all",
  sort: "addedAt:desc",
  stack: [],
  currentItems: [],
  pageSize: 24,
  libraryStart: 0,
  libraryTotal: 0,
  libraryLoadingMore: false,
  scanInProgress: false,
  watchStateRatingKey: null,
  playerItem: null,
  subtitleTrackElements: [],
  savePollTimer: null,
  progressTimer: null,
  lastProgressReportAt: 0,
  lastReportedTimeMs: 0,
  usingSavedPlayback: false,
  usingDevicePlayback: false,
  deviceSaveInProgress: false,
  deviceObjectUrls: [],
  subtitleItem: null,
  subtitleResults: [],
};

const DEVICE_CACHE_MAX_BYTES = 12 * 1024 * 1024 * 1024;
const DEVICE_CACHE_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
const LOCAL_PROGRESS_KEY = "plex-open-web-progress-v1";
const THEME_KEY = "plex-open-web-theme-v1";
const THEME_VALUES = new Set(["system", "light", "dark"]);
const PROGRESS_REPORT_INTERVAL_MS = 15000;

const el = {
  login: document.querySelector("#login"),
  app: document.querySelector("#app"),
  loginForm: document.querySelector("#login-form"),
  loginError: document.querySelector("#login-error"),
  loginVersion: document.querySelector("#login-version"),
  loginTheme: document.querySelector("#login-theme"),
  password: document.querySelector("#password"),
  libraries: document.querySelector("#libraries"),
  logout: document.querySelector("#logout"),
  appVersion: document.querySelector("#app-version"),
  appTheme: document.querySelector("#app-theme"),
  serverName: document.querySelector("#server-name"),
  breadcrumbs: document.querySelector("#breadcrumbs"),
  viewTitle: document.querySelector("#view-title"),
  searchForm: document.querySelector("#search-form"),
  searchInput: document.querySelector("#search-input"),
  status: document.querySelector("#status"),
  grid: document.querySelector("#grid"),
  loadMore: document.querySelector("#load-more"),
  sort: document.querySelector("#sort"),
  scanLibrary: document.querySelector("#scan-library"),
  viewButtons: [...document.querySelectorAll(".view-button")],
  detailsDialog: document.querySelector("#details-dialog"),
  detailsPoster: document.querySelector("#details-poster"),
  detailsKicker: document.querySelector("#details-kicker"),
  detailsTitle: document.querySelector("#details-title"),
  detailsSummary: document.querySelector("#details-summary"),
  detailsMeta: document.querySelector("#details-meta"),
  detailsPlay: document.querySelector("#details-play"),
  detailsOpen: document.querySelector("#details-open"),
  detailsSubtitles: document.querySelector("#details-subtitles"),
  detailsWatchState: document.querySelector("#details-watch-state"),
  detailsClose: document.querySelector("#details-close"),
  playerDialog: document.querySelector("#player-dialog"),
  playerTitle: document.querySelector("#player-title"),
  playbackMode: document.querySelector("#playback-mode"),
  playerClose: document.querySelector("#player-close"),
  subtitleLabel: document.querySelector("#subtitle-select-label"),
  subtitleSelect: document.querySelector("#subtitle-select"),
  playerSave: document.querySelector("#player-save"),
  playerDeleteSave: document.querySelector("#player-delete-save"),
  playerDeviceSave: document.querySelector("#player-device-save"),
  playerDeviceDelete: document.querySelector("#player-device-delete"),
  playerDownloadOriginal: document.querySelector("#player-download-original"),
  playerSubtitleSearch: document.querySelector("#player-subtitle-search"),
  player: document.querySelector("#player"),
  subtitleDialog: document.querySelector("#subtitle-dialog"),
  subtitleClose: document.querySelector("#subtitle-close"),
  subtitleForm: document.querySelector("#subtitle-form"),
  subtitleLanguage: document.querySelector("#subtitle-language"),
  subtitleQuery: document.querySelector("#subtitle-query"),
  subtitleStatus: document.querySelector("#subtitle-status"),
  subtitleResults: document.querySelector("#subtitle-results"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let data = null;
  const text = await response.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { message: text };
    }
  }
  if (!response.ok) {
    const message = data?.message || data?.error || `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return data;
}

function showLogin() {
  el.app.hidden = true;
  el.login.hidden = false;
  el.password.focus();
}

function showApp() {
  el.login.hidden = true;
  el.app.hidden = false;
}

function showVersion(version) {
  if (!version) return;
  const label = `Version ${version}`;
  el.loginVersion.textContent = label;
  el.appVersion.textContent = label;
}

function applyTheme(value, { persist = true } = {}) {
  const theme = THEME_VALUES.has(value) ? value : "system";
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.resolvedTheme = theme === "system" ? (systemDark ? "dark" : "light") : theme;
  el.loginTheme.value = theme;
  el.appTheme.value = theme;
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // The selected theme still applies for this page when storage is unavailable.
    }
  }
}

applyTheme(document.documentElement.dataset.theme, { persist: false });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (document.documentElement.dataset.theme === "system") {
    applyTheme("system", { persist: false });
  }
});

function setStatus(message = "", kind = "") {
  el.status.textContent = message;
  el.status.dataset.kind = kind;
}

function labelFor(item) {
  if (item.type === "episode") {
    const season = item.parentIndex ? `S${String(item.parentIndex).padStart(2, "0")}` : "";
    const episode = item.index ? `E${String(item.index).padStart(2, "0")}` : "";
    return [item.grandparentTitle, `${season}${episode}`].filter(Boolean).join(" ");
  }
  if (item.type === "season") {
    return `${item.childCount || item.leafCount || 0} episodes`;
  }
  if (item.type === "show") {
    return `${item.viewedLeafCount || 0}/${item.leafCount || 0} watched`;
  }
  return [item.year, item.durationText].filter(Boolean).join(" • ");
}

function progressPercent(item) {
  if (!item || item.viewCount) return 0;
  const durationMs = Number(item.duration || item.media?.duration || 0);
  const localOffset = Number(localProgressFor(item)?.timeMs || 0);
  const offsetMs = Math.max(Number(item.viewOffset || 0), localOffset);
  if (durationMs <= 0 || offsetMs < 10000) return 0;
  return Math.min(99, Math.max(1, Math.round((offsetMs / durationMs) * 100)));
}

function progressMarkup(item) {
  const percent = progressPercent(item);
  if (!percent) return "";
  return `<span class="resume-progress" aria-label="${percent}% watched"><span style="width:${percent}%"></span></span>`;
}

function displayTitle(item) {
  return item.type === "episode" && item.grandparentTitle
    ? `${item.grandparentTitle}: ${item.title}`
    : item.title;
}

function subtitleSearchTitle(item) {
  if (item.media?.file) {
    return item.media.file;
  }
  const title = item.type === "episode" && item.grandparentTitle ? item.grandparentTitle : item.title;
  return [title, item.year || item.originallyAvailableAt?.slice(0, 4)].filter(Boolean).join(" ");
}

function posterMarkup(item) {
  if (item.posterUrl) {
    return `<img loading="lazy" src="${escapeAttr(item.posterUrl)}" alt="">`;
  }
  const letter = (item.title || "?").trim().slice(0, 1).toUpperCase();
  return `<div class="poster-fallback">${escapeHtml(letter)}</div>`;
}

function itemCanOpen(item) {
  return ["show", "season"].includes(item.type);
}

function renderLibraries() {
  el.libraries.innerHTML = "";
  for (const library of state.libraries) {
    const button = document.createElement("button");
    button.className = "library-button";
    button.dataset.active = state.selectedLibrary?.key === library.key ? "true" : "false";
    button.innerHTML = `<span>${escapeHtml(library.title)}</span><small>${escapeHtml(library.type)}</small>`;
    button.addEventListener("click", () => selectLibrary(library.key));
    el.libraries.append(button);
  }
  updateScanButton();
}

function updateScanButton() {
  if (!el.scanLibrary) return;
  el.scanLibrary.disabled = !state.selectedLibrary || state.scanInProgress;
  el.scanLibrary.textContent = state.scanInProgress ? "Scanning..." : "Scan library";
}

function renderBreadcrumbs() {
  const parts = [];
  if (state.selectedLibrary) {
    parts.push({ title: state.selectedLibrary.title, index: -1 });
  }
  state.stack.forEach((entry, index) => parts.push({ title: entry.title, index }));
  el.breadcrumbs.innerHTML = "";
  parts.forEach((part, i) => {
    const button = document.createElement("button");
    button.textContent = part.title;
    button.addEventListener("click", () => {
      if (part.index === -1) {
        state.stack = [];
        loadLibrary();
      } else {
        state.stack = state.stack.slice(0, part.index + 1);
        renderItems(state.stack.at(-1).items);
      }
    });
    el.breadcrumbs.append(button);
    if (i < parts.length - 1) {
      const sep = document.createElement("span");
      sep.textContent = "/";
      el.breadcrumbs.append(sep);
    }
  });
}

function renderItems(items) {
  state.currentItems = items;
  renderBreadcrumbs();
  const current = state.stack.at(-1);
  el.viewTitle.textContent = current?.title || state.selectedLibrary?.title || "Library";
  el.grid.innerHTML = "";
  if (!items.length) {
    const emptyContinueView = state.stack.length === 0 && state.selectedView === "continue";
    setStatus(emptyContinueView ? "Nothing to continue." : "No items found.", "muted");
    updateLoadMore();
    return;
  }
  setStatus(statusTextForItems(items.length), "muted");
  const fragment = document.createDocumentFragment();
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "media-card";
    card.innerHTML = `
      <button class="poster-button" data-action="open">
        ${posterMarkup(item)}
        ${item.viewCount ? '<span class="watched">Watched</span>' : ""}
        ${supportedSubtitles(item).length ? '<span class="subtitle-badge">CC</span>' : ""}
        ${progressMarkup(item)}
      </button>
      <div class="card-body">
        <h3>${escapeHtml(item.title)}</h3>
        <p>${escapeHtml(labelFor(item))}</p>
        <div class="card-actions">
          ${item.streamUrl ? '<button data-action="play" class="mini-primary">Play</button>' : ""}
          <button data-action="details" class="mini-secondary">Details</button>
        </div>
      </div>
    `;
    card.addEventListener("click", (event) => {
      const action = event.target.closest("button")?.dataset.action;
      if (action === "play") {
        event.stopPropagation();
        playItem(item);
      } else if (action === "details") {
        event.stopPropagation();
        openDetails(item);
      } else if (action === "open") {
        event.stopPropagation();
        if (itemCanOpen(item)) {
          openChildren(item);
        } else {
          openDetails(item);
        }
      }
    });
    fragment.append(card);
  }
  el.grid.append(fragment);
  updateLoadMore();
}

function statusTextForItems(count) {
  if (state.libraryTotal && state.libraryTotal > count) {
    return `${count} of ${state.libraryTotal} items`;
  }
  return `${count} items`;
}

function updateLoadMore() {
  if (!el.loadMore) return;
  const browsingLibrary = Boolean(state.selectedLibrary) && state.stack.length === 0;
  const canLoadMore = browsingLibrary && state.libraryTotal > state.currentItems.length;
  el.loadMore.hidden = !canLoadMore;
  el.loadMore.disabled = state.libraryLoadingMore;
  if (state.libraryLoadingMore) {
    el.loadMore.textContent = "Loading...";
  } else if (canLoadMore) {
    const remaining = state.libraryTotal - state.currentItems.length;
    el.loadMore.textContent = `Load more (${remaining} left)`;
  }
}

async function loadServerInfo() {
  try {
    const data = await api("/api/server");
    el.serverName.textContent = data.friendlyName || "Plex server";
  } catch {
    el.serverName.textContent = "Plex server";
  }
}

async function loadLibraries() {
  setStatus("Loading libraries...");
  const data = await api("/api/libraries");
  state.libraries = data.libraries || [];
  state.selectedLibrary = state.libraries[0] || null;
  resetLibraryPaging();
  renderLibraries();
  if (state.selectedLibrary) {
    await loadLibrary();
  } else {
    renderItems([]);
  }
}

async function selectLibrary(key) {
  state.selectedLibrary = state.libraries.find((library) => library.key === key);
  state.stack = [];
  resetLibraryPaging();
  renderLibraries();
  await loadLibrary();
}

function resetLibraryPaging() {
  state.libraryStart = 0;
  state.libraryTotal = 0;
  state.libraryLoadingMore = false;
  updateLoadMore();
}

async function loadLibrary({ append = false } = {}) {
  if (!state.selectedLibrary) return false;
  if (!append) {
    resetLibraryPaging();
    setStatus("Loading media...");
  } else {
    state.libraryLoadingMore = true;
    updateLoadMore();
    setStatus(`${statusTextForItems(state.currentItems.length)} - loading more...`, "muted");
  }
  const start = append ? state.currentItems.length : 0;
  const params = new URLSearchParams({
    view: state.selectedView,
    sort: state.sort,
    start: String(start),
    limit: String(state.pageSize),
  });
  try {
    const data = await api(`/api/library/${encodeURIComponent(state.selectedLibrary.key)}?${params}`);
    state.libraryStart = start;
    state.libraryTotal = data.totalSize || data.size || 0;
    const incoming = data.items || [];
    renderItems(append ? [...state.currentItems, ...incoming] : incoming);
    return true;
  } catch (error) {
    if (!append) {
      state.currentItems = [];
      state.libraryTotal = 0;
      el.grid.innerHTML = "";
    }
    setStatus(`Could not load media: ${error.message}`, "error");
    return false;
  } finally {
    state.libraryLoadingMore = false;
    updateLoadMore();
  }
}

async function scanSelectedLibrary() {
  if (!state.selectedLibrary || state.scanInProgress) return;
  const library = state.selectedLibrary;
  state.scanInProgress = true;
  updateScanButton();
  setStatus(`Starting scan for ${library.title}...`);
  try {
    await api("/api/library-scan", {
      method: "POST",
      body: JSON.stringify({ sectionKey: library.key }),
    });
    setStatus(`Plex is scanning ${library.title}. Results will refresh shortly.`, "success");
    await new Promise((resolve) => window.setTimeout(resolve, 3000));
    if (state.selectedLibrary?.key === library.key) {
      state.stack = [];
      const refreshed = await loadLibrary();
      if (refreshed) {
        setStatus(`Scan started for ${library.title}. Results refreshed.`, "success");
      }
    }
  } catch (error) {
    setStatus(`Could not start scan: ${error.message}`, "error");
  } finally {
    state.scanInProgress = false;
    updateScanButton();
  }
}

async function openChildren(item) {
  setStatus("Loading...");
  state.libraryTotal = 0;
  updateLoadMore();
  const data = await api(`/api/children/${encodeURIComponent(item.ratingKey)}`);
  state.stack.push({ title: item.title, item, items: data.items || [] });
  renderItems(data.items || []);
}

function openDetails(item) {
  el.detailsPoster.src = item.posterUrl || "";
  el.detailsPoster.hidden = !item.posterUrl;
  el.detailsKicker.textContent = [item.type, item.year || item.originallyAvailableAt].filter(Boolean).join(" / ");
  el.detailsTitle.textContent = displayTitle(item);
  el.detailsSummary.textContent = item.summary || "";
  const percent = progressPercent(item);
  const meta = [
    item.contentRating,
    item.durationText,
    item.rating ? `${item.rating}/10` : "",
    item.media?.videoResolution,
    item.viewCount ? "Watched" : percent ? `${percent}% watched` : "",
  ]
    .filter(Boolean)
    .map((value) => `<span>${escapeHtml(String(value))}</span>`)
    .join("");
  el.detailsMeta.innerHTML = meta;
  el.detailsPlay.hidden = !item.streamUrl;
  el.detailsPlay.onclick = () => playItem(item);
  el.detailsOpen.hidden = !itemCanOpen(item);
  el.detailsOpen.onclick = async () => {
    el.detailsDialog.close();
    await openChildren(item);
  };
  el.detailsSubtitles.hidden = !item.streamUrl;
  el.detailsSubtitles.onclick = () => openSubtitleDialog(item);
  const canChangeWatchState = Boolean(item.ratingKey && ["movie", "episode"].includes(item.type));
  el.detailsWatchState.hidden = !canChangeWatchState;
  el.detailsWatchState.disabled = state.watchStateRatingKey === item.ratingKey;
  el.detailsWatchState.textContent = state.watchStateRatingKey === item.ratingKey
    ? "Updating..."
    : item.viewCount
      ? "Mark unwatched"
      : "Mark watched";
  el.detailsWatchState.onclick = () => setWatchState(item, !Boolean(item.viewCount));
  if (!el.detailsDialog.open) {
    el.detailsDialog.showModal();
  }
}

async function setWatchState(item, watched) {
  if (!item?.ratingKey || state.watchStateRatingKey) return;
  state.watchStateRatingKey = item.ratingKey;
  openDetails(item);
  try {
    const data = await api("/api/watch-state", {
      method: "POST",
      body: JSON.stringify({ ratingKey: item.ratingKey, watched }),
    });
    Object.assign(item, data.item || {}, {
      viewCount: watched ? Math.max(1, data.item?.viewCount || item.viewCount || 0) : 0,
      viewOffset: watched ? 0 : Number(data.item?.viewOffset || 0),
    });
    clearLocalProgress(item);
    if (state.playerItem?.ratingKey === item.ratingKey) {
      Object.assign(state.playerItem, item);
    }
    const reloadFilteredView = state.stack.length === 0 && ["continue", "unwatched"].includes(state.selectedView);
    if (reloadFilteredView) {
      el.detailsDialog.close();
      await loadLibrary();
    } else {
      renderItems([...state.currentItems]);
    }
    setStatus(`${displayTitle(item)} marked ${watched ? "watched" : "unwatched"}.`, "success");
  } catch (error) {
    setStatus(`Could not update watched state: ${error.message}`, "error");
  } finally {
    state.watchStateRatingKey = null;
    if (el.detailsDialog.open) {
      openDetails(item);
    }
  }
}

function supportedSubtitles(item) {
  return (item.subtitles || []).filter((subtitle) => subtitle.supported && subtitle.subtitleUrl);
}

async function hydrateItem(item) {
  if (!item.ratingKey || item._hydrated) {
    return item;
  }
  try {
    const data = await api(`/api/metadata/${encodeURIComponent(item.ratingKey)}`);
    if (data.item) {
      Object.assign(item, data.item, { _hydrated: true });
    }
  } catch {
    item._hydrated = true;
  }
  return item;
}

async function refreshSavedPlayback(item) {
  if (!item?.ratingKey) {
    return null;
  }
  const data = await api(`/api/saved-playback?${new URLSearchParams({ ratingKey: item.ratingKey })}`);
  item.savedPlayback = data.savedPlayback;
  return item.savedPlayback;
}

function savedPlaybackUrlFor(item) {
  return item.savedPlayback?.ready && item.savedPlayback?.streamUrl
    ? item.savedPlayback.streamUrl
    : null;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isSavedPlaybackUrl(item, streamUrl) {
  const savedUrl = savedPlaybackUrlFor(item);
  return Boolean(savedUrl && streamUrl === savedUrl);
}

function progressStore() {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_PROGRESS_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveProgressStore(store) {
  try {
    localStorage.setItem(LOCAL_PROGRESS_KEY, JSON.stringify(store));
  } catch {
    // Resume still works through Plex even if browser storage is full or blocked.
  }
}

function localProgressFor(item) {
  if (!item?.ratingKey) return null;
  const entry = progressStore()[item.ratingKey];
  if (!entry || Date.now() - Number(entry.updatedAt || 0) > 30 * 24 * 60 * 60 * 1000) {
    return null;
  }
  return entry;
}

function rememberLocalProgress(item, timeMs, durationMs) {
  if (!item?.ratingKey || !durationMs || durationMs <= 0) return;
  const remainingMs = durationMs - timeMs;
  const store = progressStore();
  if (timeMs < 10000 || remainingMs < 30000) {
    delete store[item.ratingKey];
  } else {
    store[item.ratingKey] = { timeMs, durationMs, updatedAt: Date.now() };
  }
  saveProgressStore(store);
}

function clearLocalProgress(item) {
  if (!item?.ratingKey) return;
  const store = progressStore();
  delete store[item.ratingKey];
  saveProgressStore(store);
}

function playbackDurationMs(item = state.playerItem) {
  if (Number.isFinite(el.player.duration) && el.player.duration > 0) {
    return Math.floor(el.player.duration * 1000);
  }
  return item?.duration || item?.media?.duration || 0;
}

function playbackTimeMs() {
  if (!Number.isFinite(el.player.currentTime) || el.player.currentTime < 0) {
    return 0;
  }
  return Math.floor(el.player.currentTime * 1000);
}

function resumeTimeFor(item) {
  if (item?.viewCount) return 0;
  const durationMs = item?.duration || item?.media?.duration || 0;
  const plexOffset = Number(item?.viewOffset || 0);
  const local = localProgressFor(item);
  const localOffset = Number(local?.timeMs || 0);
  const offset = Math.max(plexOffset, localOffset);
  if (offset < 10000) return 0;
  if (durationMs && durationMs - offset < 30000) return 0;
  return offset / 1000;
}

async function reportPlaybackProgress(stateName = "stopped", { force = false, keepalive = false } = {}) {
  const item = state.playerItem;
  if (!item?.ratingKey) return;
  const timeMs = stateName === "ended" ? playbackDurationMs(item) : playbackTimeMs();
  const durationMs = playbackDurationMs(item);
  if (!force && Math.abs(timeMs - state.lastReportedTimeMs) < 5000) {
    return;
  }
  rememberLocalProgress(item, timeMs, durationMs);
  state.lastProgressReportAt = Date.now();
  state.lastReportedTimeMs = timeMs;
  const payload = {
    ratingKey: item.ratingKey,
    timeMs,
    durationMs,
    state: stateName,
  };
  if (stateName === "ended" || (durationMs && durationMs - timeMs < 30000)) {
    clearLocalProgress(item);
  }
  const body = JSON.stringify(payload);
  try {
    const response = await fetch("/api/playback-progress", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive,
    });
    if (response.ok) {
      const data = await response.json();
      if (data.watched) {
        item.viewCount = Math.max(1, item.viewCount || 0);
        item.viewOffset = 0;
        clearLocalProgress(item);
      } else if (data.progressSaved || timeMs >= 10000) {
        item.viewOffset = timeMs;
      }
    }
  } catch {
    // Local progress has already been stored for same-browser resume.
  }
}

function startProgressReporting() {
  stopProgressReporting();
  state.progressTimer = setInterval(() => {
    if (!el.player.paused && !el.player.ended) {
      reportPlaybackProgress("playing").catch(() => {});
    }
  }, PROGRESS_REPORT_INTERVAL_MS);
}

function stopProgressReporting() {
  if (state.progressTimer) {
    clearInterval(state.progressTimer);
    state.progressTimer = null;
  }
}

function deviceStorageSupported() {
  return Boolean(window.isSecureContext && navigator.storage?.getDirectory);
}

async function deviceStorageRoot() {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle("plex-open-web-device-cache-v1", { create: true });
}

function deviceCacheIdFor(item) {
  return item?.savedPlayback?.id || null;
}

function deviceMetaFileName(id) {
  return `${id}.json`;
}

function deviceVideoFileName(id) {
  return `${id}.mp4`;
}

function deviceSubtitleFileName(id, index) {
  return `${id}-sub-${index}.vtt`;
}

async function fileExists(root, name) {
  try {
    await root.getFileHandle(name);
    return true;
  } catch (error) {
    if (error?.name === "NotFoundError") {
      return false;
    }
    throw error;
  }
}

async function readDeviceMetadata(item) {
  const id = deviceCacheIdFor(item);
  if (!id || !deviceStorageSupported()) {
    return null;
  }
  try {
    const root = await deviceStorageRoot();
    const file = await (await root.getFileHandle(deviceMetaFileName(id))).getFile();
    return JSON.parse(await file.text());
  } catch (error) {
    if (error?.name === "NotFoundError") {
      return null;
    }
    throw error;
  }
}

async function writeDeviceMetadata(root, id, metadata) {
  const handle = await root.getFileHandle(deviceMetaFileName(id), { create: true });
  const writable = await handle.createWritable();
  await writable.write(JSON.stringify(metadata, null, 2));
  await writable.close();
}

async function removeDeviceEntry(root, name) {
  try {
    await root.removeEntry(name);
  } catch (error) {
    if (error?.name !== "NotFoundError") {
      throw error;
    }
  }
}

async function listDeviceCacheEntries(root = null) {
  if (!deviceStorageSupported()) return [];
  const cacheRoot = root || await deviceStorageRoot();
  const entries = [];
  for await (const [name, handle] of cacheRoot.entries()) {
    if (!name.endsWith(".json")) continue;
    try {
      const file = await handle.getFile();
      const metadata = JSON.parse(await file.text());
      entries.push({
        ...metadata,
        metaFile: name,
        savedAt: Number(metadata.savedAt) || 0,
        bytes: Number(metadata.bytes) || 0,
      });
    } catch {
      entries.push({ metaFile: name, savedAt: 0, bytes: 0, invalid: true });
    }
  }
  return entries;
}

async function deleteDeviceMetadataEntry(root, metadata) {
  if (metadata?.videoFile) {
    await removeDeviceEntry(root, metadata.videoFile);
  }
  for (const subtitle of metadata?.subtitles || []) {
    if (subtitle.file) {
      await removeDeviceEntry(root, subtitle.file);
    }
  }
  if (metadata?.metaFile) {
    await removeDeviceEntry(root, metadata.metaFile);
  } else if (metadata?.id) {
    await removeDeviceEntry(root, deviceMetaFileName(metadata.id));
  }
}

async function pruneDeviceCache() {
  if (!deviceStorageSupported()) return;
  const root = await deviceStorageRoot();
  const now = Date.now();
  let entries = await listDeviceCacheEntries(root);
  for (const entry of entries) {
    if (entry.invalid || (entry.savedAt && now - entry.savedAt > DEVICE_CACHE_MAX_AGE_MS)) {
      await deleteDeviceMetadataEntry(root, entry);
    }
  }
  entries = (await listDeviceCacheEntries(root)).sort((a, b) => (a.savedAt || 0) - (b.savedAt || 0));
  let total = entries.reduce((sum, entry) => sum + (entry.bytes || 0), 0);
  for (const entry of entries) {
    if (total <= DEVICE_CACHE_MAX_BYTES) break;
    await deleteDeviceMetadataEntry(root, entry);
    total -= entry.bytes || 0;
  }
}

async function devicePlaybackStatus(item) {
  if (!deviceStorageSupported()) {
    return { state: "unsupported", ready: false, supported: false };
  }
  const id = deviceCacheIdFor(item);
  if (!id) {
    return { state: "missing", ready: false, supported: true };
  }
  const metadata = await readDeviceMetadata(item);
  if (!metadata?.videoFile) {
    return { id, state: "missing", ready: false, supported: true };
  }
  const root = await deviceStorageRoot();
  const ready = await fileExists(root, metadata.videoFile);
  return {
    id,
    state: ready ? "ready" : "missing",
    ready,
    supported: true,
    bytes: metadata.bytes,
    savedAt: metadata.savedAt,
    subtitles: metadata.subtitles || [],
  };
}

async function refreshDevicePlayback(item) {
  item.devicePlayback = await devicePlaybackStatus(item);
  return item.devicePlayback;
}

async function writeResponseToDeviceFile(response, root, fileName, onProgress) {
  if (!response.ok || !response.body) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  const total = Number(response.headers.get("Content-Length")) || 0;
  const handle = await root.getFileHandle(fileName, { create: true });
  const writable = await handle.createWritable();
  const reader = response.body.getReader();
  let written = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      await writable.write(value);
      written += value.byteLength;
      if (total && onProgress) {
        onProgress(written, total);
      }
    }
    await writable.close();
  } catch (error) {
    try {
      await writable.abort();
    } catch {
      // Ignore abort errors; the original write error is more useful.
    }
    throw error;
  }
  return written;
}

async function ensureSavedPlaybackReady(item) {
  if (item.savedPlayback?.ready) {
    return item.savedPlayback;
  }
  const data = await api("/api/saved-playback", {
    method: "POST",
    body: JSON.stringify({ ratingKey: item.ratingKey }),
  });
  item.savedPlayback = data.savedPlayback;
  updateSaveControls(item);
  while (item.savedPlayback?.state === "saving") {
    el.playerDeviceSave.textContent = "Waiting";
    await sleep(2500);
    await refreshSavedPlayback(item);
    updateSaveControls(item);
  }
  if (!item.savedPlayback?.ready) {
    throw new Error(item.savedPlayback?.message || "Saved server copy is not ready.");
  }
  return item.savedPlayback;
}

async function saveDevicePlayback(item = state.playerItem) {
  if (!item?.ratingKey || !deviceStorageSupported()) return;
  state.deviceSaveInProgress = true;
  updateDeviceControls(item);
  await navigator.storage?.persist?.();
  await pruneDeviceCache();
  await ensureSavedPlaybackReady(item);
  const id = deviceCacheIdFor(item);
  const root = await deviceStorageRoot();
  const videoFile = deviceVideoFileName(id);
  await removeDeviceEntry(root, `${videoFile}.tmp`);
  const response = await fetch(item.savedPlayback.streamUrl, { credentials: "same-origin" });
  const bytes = await writeResponseToDeviceFile(response, root, videoFile, (written, total) => {
    const percent = Math.max(1, Math.min(99, Math.floor((written / total) * 100)));
    el.playerDeviceSave.textContent = `${percent}%`;
  });
  const subtitleMetadata = [];
  const subtitles = supportedSubtitles(item);
  for (let index = 0; index < subtitles.length; index += 1) {
    const subtitle = subtitles[index];
    const subtitleFile = deviceSubtitleFileName(id, index);
    const subtitleResponse = await fetch(subtitle.subtitleUrl, { credentials: "same-origin" });
    if (!subtitleResponse.ok) {
      continue;
    }
    const handle = await root.getFileHandle(subtitleFile, { create: true });
    const writable = await handle.createWritable();
    await writable.write(await subtitleResponse.blob());
    await writable.close();
    subtitleMetadata.push({
      id: subtitle.id,
      index,
      file: subtitleFile,
      label: subtitle.label || subtitle.displayTitle || subtitle.language || `Subtitle ${index + 1}`,
      srclang: subtitle.srclang || "und",
      selected: Boolean(subtitle.selected),
      default: Boolean(subtitle.default),
      forced: Boolean(subtitle.forced),
    });
  }
  await writeDeviceMetadata(root, id, {
    id,
    ratingKey: item.ratingKey,
    title: displayTitle(item),
    videoFile,
    bytes,
    savedAt: Date.now(),
    subtitles: subtitleMetadata,
  });
  state.deviceSaveInProgress = false;
  await refreshDevicePlayback(item);
  await pruneDeviceCache();
  await refreshDevicePlayback(item);
  updateDeviceControls(item);
  await switchToDevicePlayback(item);
}

function revokeDeviceObjectUrls() {
  for (const url of state.deviceObjectUrls) {
    URL.revokeObjectURL(url);
  }
  state.deviceObjectUrls = [];
}

async function prepareDevicePlayback(item) {
  const metadata = await readDeviceMetadata(item);
  if (!metadata?.videoFile) {
    throw new Error("Device copy is missing.");
  }
  const root = await deviceStorageRoot();
  const videoFile = await (await root.getFileHandle(metadata.videoFile)).getFile();
  const videoUrl = URL.createObjectURL(videoFile);
  const objectUrls = [videoUrl];
  const subtitleUrls = new Map();
  for (const subtitle of metadata.subtitles || []) {
    try {
      const file = await (await root.getFileHandle(subtitle.file)).getFile();
      const url = URL.createObjectURL(file);
      objectUrls.push(url);
      subtitleUrls.set(subtitle.id || subtitle.index, url);
    } catch {
      // A missing subtitle file should not block local video playback.
    }
  }
  const localSubtitles = (item.subtitles || []).map((subtitle, index) => {
    const meta = (metadata.subtitles || []).find(
      (candidate) => candidate.id === subtitle.id || candidate.index === index
    );
    const url = meta ? subtitleUrls.get(meta.id || meta.index) : null;
    return {
      ...subtitle,
      supported: Boolean(url),
      subtitleUrl: url || subtitle.subtitleUrl,
      source: url ? "device" : subtitle.source,
    };
  });
  return {
    item: { ...item, subtitles: localSubtitles },
    streamUrl: videoUrl,
    objectUrls,
  };
}

async function deleteDevicePlayback(item = state.playerItem) {
  if (!item?.ratingKey || !deviceStorageSupported()) return;
  const wasUsingDevice = state.usingDevicePlayback;
  const resumeTime = el.player.currentTime || 0;
  const autoplay = !el.player.paused;
  if (wasUsingDevice) {
    const fallbackUrl = savedPlaybackUrlFor(item) || liveStreamUrlFor(item);
    state.usingDevicePlayback = false;
    state.usingSavedPlayback = isSavedPlaybackUrl(item, fallbackUrl);
    setPlaybackMode(item, state.usingSavedPlayback ? "saved" : "live");
    revokeDeviceObjectUrls();
    loadPlayerSource(item, fallbackUrl, { resumeTime, autoplay });
  }
  const metadata = await readDeviceMetadata(item);
  const id = deviceCacheIdFor(item);
  const root = await deviceStorageRoot();
  if (metadata?.videoFile) {
    await removeDeviceEntry(root, metadata.videoFile);
    for (const subtitle of metadata.subtitles || []) {
      if (subtitle.file) {
        await removeDeviceEntry(root, subtitle.file);
      }
    }
  }
  if (id) {
    await removeDeviceEntry(root, deviceMetaFileName(id));
  }
  await refreshDevicePlayback(item);
  updateDeviceControls(item);
}

function clearSubtitleTracks() {
  disableAllTextTracks();
  for (const track of [...el.player.querySelectorAll("track")]) {
    track.remove();
  }
  state.subtitleTrackElements = [];
  el.subtitleSelect.innerHTML = "";
  el.subtitleSelect.hidden = true;
  el.subtitleLabel.hidden = true;
}

function disableAllTextTracks() {
  for (const track of [...el.player.textTracks]) {
    track.mode = "disabled";
  }
}

function currentSubtitleIndex() {
  const value = Number(el.subtitleSelect.value);
  return Number.isFinite(value) ? value : -1;
}

function setActiveSubtitle(index = currentSubtitleIndex()) {
  disableAllTextTracks();
  const trackElement = state.subtitleTrackElements[index];
  if (trackElement?.track) {
    trackElement.track.mode = "showing";
  }
}

function reapplyActiveSubtitle() {
  requestAnimationFrame(() => setActiveSubtitle());
}

function configureSubtitles(item) {
  clearSubtitleTracks();
  const subtitles = supportedSubtitles(item);
  const off = document.createElement("option");
  off.value = "-1";
  off.textContent = "Off";
  el.subtitleSelect.append(off);
  if (!subtitles.length) {
    return;
  }
  el.subtitleSelect.hidden = false;
  el.subtitleLabel.hidden = false;
  let selectedIndex = subtitles.findIndex((subtitle) => subtitle.selected);
  if (selectedIndex < 0) {
    selectedIndex = subtitles.findIndex((subtitle) => subtitle.default || subtitle.forced);
  }
  subtitles.forEach((subtitle, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = subtitle.label || subtitle.displayTitle || subtitle.language || `Subtitle ${index + 1}`;
    el.subtitleSelect.append(option);

    const track = document.createElement("track");
    track.kind = subtitle.forced ? "subtitles" : "subtitles";
    track.src = subtitle.subtitleUrl;
    track.srclang = subtitle.srclang || "und";
    track.label = option.textContent;
    track.addEventListener("load", reapplyActiveSubtitle);
    state.subtitleTrackElements[index] = track;
    el.player.append(track);
  });
  el.subtitleSelect.value = selectedIndex >= 0 ? String(selectedIndex) : "-1";
  reapplyActiveSubtitle();
}

function liveStreamUrlFor(item) {
  const url = item.playback?.audioTranscodeRequired && item.playback.compatibleStreamUrl
    ? item.playback.compatibleStreamUrl
    : item.streamUrl;
  if (!url) {
    return url;
  }
  const streamUrl = new URL(url, window.location.origin);
  if (window.location.protocol === "http:" && /^100\./.test(window.location.hostname)) {
    streamUrl.searchParams.set("quality", "remote");
  }
  return `${streamUrl.pathname}${streamUrl.search}`;
}

function streamUrlFor(item) {
  return item?.devicePlayback?.ready ? null : savedPlaybackUrlFor(item) || liveStreamUrlFor(item);
}

function setPlaybackMode(item, mode) {
  if (mode === "device" || mode === true) {
    el.playbackMode.textContent = mode === "device" ? "On device" : "Saved copy";
    el.playbackMode.title = mode === "device"
      ? "Playing from this device's browser storage."
      : "Playing the saved browser-friendly file for smoother seeking.";
    el.playbackMode.hidden = false;
    return;
  }
  if (mode === "saved") {
    el.playbackMode.textContent = "Saved copy";
    el.playbackMode.title = "Playing the saved browser-friendly file for smoother seeking.";
    el.playbackMode.hidden = false;
    return;
  }
  if (item.playback?.audioTranscodeRequired) {
    el.playbackMode.textContent = "AAC audio";
    el.playbackMode.title = item.playback.audioTranscodeReason || "Audio is being converted for browser playback.";
    el.playbackMode.hidden = false;
  } else {
    el.playbackMode.hidden = true;
  }
}

function updateDeviceControls(item = state.playerItem) {
  const supported = deviceStorageSupported();
  const canUse = supported && Boolean(item?.ratingKey);
  const status = item?.devicePlayback;
  el.playerDeviceSave.hidden = !canUse;
  el.playerDeviceDelete.hidden = !canUse || !status?.ready;
  if (!canUse) {
    return;
  }
  el.playerDeviceSave.title = "Keeps local copies for 14 days and prunes oldest items above 12 GB.";
  el.playerDeviceDelete.title = "";
  el.playerDeviceSave.disabled = state.deviceSaveInProgress;
  el.playerDeviceDelete.disabled = false;
  if (state.deviceSaveInProgress) {
    if (!/%$|Waiting/.test(el.playerDeviceSave.textContent)) {
      el.playerDeviceSave.textContent = "Saving";
    }
  } else if (status?.ready) {
    el.playerDeviceSave.textContent = state.usingDevicePlayback ? "On device" : "Play device";
    el.playerDeviceSave.disabled = state.usingDevicePlayback;
  } else {
    el.playerDeviceSave.textContent = "Save device";
  }
}

function updateDownloadControls(item = state.playerItem) {
  const canDownload = Boolean(item?.downloadOriginalUrl);
  el.playerDownloadOriginal.hidden = !canDownload;
  el.playerDownloadOriginal.disabled = !canDownload;
  el.playerDownloadOriginal.title = canDownload
    ? "Download the original media file in a zip with available subtitle files."
    : "";
  el.playerDownloadOriginal.textContent = "Download";
}

function downloadOriginalFiles(item = state.playerItem) {
  if (!item?.downloadOriginalUrl) return;
  const link = document.createElement("a");
  link.href = item.downloadOriginalUrl;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.append(link);
  link.click();
  link.remove();
}

function loadPlayerSource(item, streamUrl, { resumeTime = 0, autoplay = true } = {}) {
  clearSubtitleTracks();
  const applyResume = () => {
    reapplyActiveSubtitle();
    if (resumeTime > 0 && Number.isFinite(resumeTime)) {
      try {
        el.player.currentTime = Math.max(0, resumeTime);
      } catch {
        // Some streams reject seeking until more metadata arrives.
      }
    }
    if (autoplay) {
      el.player.play().catch(() => {});
    }
  };
  el.player.addEventListener("loadedmetadata", applyResume, { once: true });
  el.player.addEventListener("loadeddata", reapplyActiveSubtitle, { once: true });
  el.player.src = streamUrl;
  configureSubtitles(item);
  el.player.load();
  if (autoplay) {
    el.player.play().catch(() => {});
  }
}

function stopSavePolling() {
  if (state.savePollTimer) {
    clearTimeout(state.savePollTimer);
    state.savePollTimer = null;
  }
}

function updateSaveControls(item = state.playerItem) {
  const status = item?.savedPlayback;
  const canSave = Boolean(item?.ratingKey && item?.partKey);
  el.playerSave.hidden = !canSave;
  el.playerDeleteSave.hidden = !canSave || !status?.ready;
  if (!canSave) {
    return;
  }
  el.playerSave.title = "";
  el.playerDeleteSave.title = "";
  el.playerSave.disabled = false;
  el.playerDeleteSave.disabled = false;
  if (status?.state === "saving") {
    el.playerSave.textContent = "Saving";
    el.playerSave.disabled = true;
    el.playerDeleteSave.hidden = true;
  } else if (status?.ready) {
    el.playerSave.textContent = state.usingSavedPlayback ? "Saved" : "Play saved";
    el.playerSave.disabled = state.usingSavedPlayback;
  } else if (status?.state === "error") {
    el.playerSave.textContent = "Retry save";
  } else {
    el.playerSave.textContent = "Save";
  }
}

async function switchToSavedPlayback(item = state.playerItem) {
  const streamUrl = savedPlaybackUrlFor(item);
  if (!item || !streamUrl) return;
  const resumeTime = el.player.currentTime || 0;
  const autoplay = !el.player.paused;
  revokeDeviceObjectUrls();
  state.usingDevicePlayback = false;
  state.usingSavedPlayback = true;
  setPlaybackMode(item, "saved");
  loadPlayerSource(item, streamUrl, { resumeTime, autoplay });
  updateSaveControls(item);
  updateDeviceControls(item);
}

async function switchToDevicePlayback(item = state.playerItem) {
  if (!item?.ratingKey || !item.devicePlayback?.ready) return;
  const resumeTime = el.player.currentTime || 0;
  const autoplay = !el.player.paused;
  const prepared = await prepareDevicePlayback(item);
  revokeDeviceObjectUrls();
  state.deviceObjectUrls = prepared.objectUrls;
  state.usingDevicePlayback = true;
  state.usingSavedPlayback = false;
  setPlaybackMode(item, "device");
  loadPlayerSource(prepared.item, prepared.streamUrl, { resumeTime, autoplay });
  updateSaveControls(item);
  updateDeviceControls(item);
}

function pollSavedPlayback(item, switchWhenReady = false) {
  stopSavePolling();
  state.savePollTimer = setTimeout(async () => {
    if (!state.playerItem || state.playerItem.ratingKey !== item.ratingKey) {
      return;
    }
    try {
      await refreshSavedPlayback(state.playerItem);
      updateSaveControls(state.playerItem);
    if (state.playerItem.savedPlayback?.ready) {
      if (switchWhenReady && !state.usingSavedPlayback) {
        await switchToSavedPlayback(state.playerItem);
      }
      updateDeviceControls(state.playerItem);
      return;
    }
      if (state.playerItem.savedPlayback?.state === "saving") {
        pollSavedPlayback(state.playerItem, switchWhenReady);
      }
    } catch {
      pollSavedPlayback(item, switchWhenReady);
    }
  }, 2500);
}

async function savePlayback(item = state.playerItem) {
  if (!item?.ratingKey) return;
  el.playerSave.disabled = true;
  el.playerSave.textContent = "Saving";
  const data = await api("/api/saved-playback", {
    method: "POST",
    body: JSON.stringify({ ratingKey: item.ratingKey }),
  });
  item.savedPlayback = data.savedPlayback;
  updateSaveControls(item);
  if (item.savedPlayback?.ready) {
    await switchToSavedPlayback(item);
  } else if (item.savedPlayback?.state === "saving") {
    pollSavedPlayback(item, true);
  }
}

async function deleteSavedPlayback(item = state.playerItem) {
  if (!item?.ratingKey) return;
  const resumeTime = el.player.currentTime || 0;
  const wasUsingSaved = state.usingSavedPlayback;
  const autoplay = !el.player.paused;
  el.playerDeleteSave.disabled = true;
  const data = await api("/api/saved-playback", {
    method: "POST",
    body: JSON.stringify({ ratingKey: item.ratingKey, action: "delete" }),
  });
  item.savedPlayback = data.savedPlayback;
  if (wasUsingSaved) {
    const liveUrl = liveStreamUrlFor(item);
    state.usingSavedPlayback = false;
    setPlaybackMode(item, "live");
    loadPlayerSource(item, liveUrl, { resumeTime, autoplay });
  }
  updateSaveControls(item);
  updateDeviceControls(item);
}

async function playItem(item) {
  item = await hydrateItem(item);
  try {
    await refreshSavedPlayback(item);
  } catch {
    item.savedPlayback = item.savedPlayback || { state: "unavailable", ready: false };
  }
  try {
    await pruneDeviceCache();
    await refreshDevicePlayback(item);
  } catch {
    item.devicePlayback = { state: "unsupported", ready: false, supported: false };
  }
  state.playerItem = item;
  state.lastProgressReportAt = 0;
  state.lastReportedTimeMs = 0;
  el.playerTitle.textContent = displayTitle(item);
  const resumeTime = resumeTimeFor(item);
  if (item.devicePlayback?.ready) {
    state.usingDevicePlayback = true;
    state.usingSavedPlayback = false;
    setPlaybackMode(item, "device");
    const prepared = await prepareDevicePlayback(item);
    revokeDeviceObjectUrls();
    state.deviceObjectUrls = prepared.objectUrls;
    loadPlayerSource(prepared.item, prepared.streamUrl, { resumeTime });
  } else {
    const streamUrl = streamUrlFor(item);
    if (!streamUrl) return;
    state.usingDevicePlayback = false;
    state.usingSavedPlayback = isSavedPlaybackUrl(item, streamUrl);
    setPlaybackMode(item, state.usingSavedPlayback ? "saved" : "live");
    loadPlayerSource(item, streamUrl, { resumeTime });
  }
  el.playerSubtitleSearch.hidden = !item.ratingKey;
  el.playerSubtitleSearch.onclick = () => openSubtitleDialog(item);
  updateSaveControls(item);
  updateDeviceControls(item);
  updateDownloadControls(item);
  if (item.savedPlayback?.state === "saving") {
    pollSavedPlayback(item, false);
  }
  el.playerDialog.showModal();
}

function setSubtitleStatus(message = "", kind = "") {
  el.subtitleStatus.textContent = message;
  el.subtitleStatus.dataset.kind = kind;
}

function subtitleResultMeta(result) {
  return [
    result.languageName,
    result.downloads ? `${result.downloads} downloads` : "",
    result.rating ? `${result.rating}/10` : "",
    result.fps ? `${result.fps} fps` : "",
    result.hearingImpaired ? "SDH" : "",
    result.trusted ? "Trusted" : "",
    result.aiTranslated ? "AI" : "",
  ].filter(Boolean).join(" • ");
}

function renderSubtitleResults(results) {
  el.subtitleResults.innerHTML = "";
  if (!results.length) {
    setSubtitleStatus("No subtitles found.", "muted");
    return;
  }
  setSubtitleStatus(`${results.length} subtitles found`, "muted");
  const fragment = document.createDocumentFragment();
  for (const result of results) {
    const row = document.createElement("article");
    row.className = "subtitle-result";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(result.release || result.fileName || "Subtitle")}</strong>
        <p>${escapeHtml(subtitleResultMeta(result))}</p>
      </div>
      <button type="button" class="mini-primary">Download</button>
    `;
    row.querySelector("button").addEventListener("click", () => downloadSubtitle(result, row.querySelector("button")));
    fragment.append(row);
  }
  el.subtitleResults.append(fragment);
}

async function searchSubtitles() {
  const item = state.subtitleItem;
  if (!item?.ratingKey) return;
  setSubtitleStatus("Searching...");
  el.subtitleResults.innerHTML = "";
  const params = new URLSearchParams({
    ratingKey: item.ratingKey,
    language: el.subtitleLanguage.value,
    query: el.subtitleQuery.value.trim(),
  });
  try {
    const data = await api(`/api/subtitle-search?${params}`);
    if (data.configured === false) {
      state.subtitleResults = [];
      setSubtitleStatus(data.message || "OpenSubtitles is not configured.", "error");
      return;
    }
    state.subtitleResults = data.results || [];
    renderSubtitleResults(state.subtitleResults);
  } catch (error) {
    setSubtitleStatus(error.message, "error");
  }
}

async function openSubtitleDialog(item) {
  item = await hydrateItem(item);
  state.subtitleItem = item;
  state.subtitleResults = [];
  el.subtitleQuery.value = subtitleSearchTitle(item);
  el.subtitleResults.innerHTML = "";
  setSubtitleStatus("");
  if (!el.subtitleDialog.open) {
    el.subtitleDialog.showModal();
  }
  await searchSubtitles();
}

async function downloadSubtitle(result, button) {
  const item = state.subtitleItem;
  if (!item?.ratingKey || !result.fileId) return;
  button.disabled = true;
  button.textContent = "Downloading";
  try {
    const data = await api("/api/subtitle-download", {
      method: "POST",
      body: JSON.stringify({
        ratingKey: item.ratingKey,
        fileId: result.fileId,
        language: result.language,
        fileName: result.fileName,
      }),
    });
    if (data.subtitle) {
      item.subtitles = (item.subtitles || []).filter((subtitle) => subtitle.id !== data.subtitle.id);
      item.subtitles.push(data.subtitle);
      if (state.playerItem?.ratingKey === item.ratingKey) {
        state.playerItem.subtitles = item.subtitles;
        configureSubtitles(state.playerItem);
      }
    }
    setSubtitleStatus(`Saved ${data.savedName || "subtitle"}.`, "success");
    button.textContent = "Saved";
  } catch (error) {
    setSubtitleStatus(error.message, "error");
    button.disabled = false;
    button.textContent = "Download";
  }
}

async function search(query) {
  if (query.trim().length < 2) {
    await loadLibrary();
    return;
  }
  state.stack = [{ title: `Search: ${query}`, items: [] }];
  state.libraryTotal = 0;
  updateLoadMore();
  setStatus("Searching...");
  const data = await api(`/api/search?${new URLSearchParams({ query })}`);
  state.stack = [{ title: `Search: ${query}`, items: data.items || [] }];
  renderItems(data.items || []);
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value = "") {
  return escapeHtml(value);
}

async function boot() {
  const me = await api("/api/me");
  showVersion(me.version);
  if (!me.authenticated && me.authRequired) {
    showLogin();
    return;
  }
  showApp();
  await loadServerInfo();
  await loadLibraries();
}

el.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  el.loginError.textContent = "";
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ password: el.password.value.trim() }),
    });
    showApp();
    await loadServerInfo();
    await loadLibraries();
  } catch (error) {
    el.loginError.textContent = error.message === "invalid_password" ? "Invalid password." : error.message;
  }
});

el.logout.addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  window.location.reload();
});

el.loginTheme.addEventListener("change", () => applyTheme(el.loginTheme.value));
el.appTheme.addEventListener("change", () => applyTheme(el.appTheme.value));

el.searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await search(el.searchInput.value);
});

el.loadMore.addEventListener("click", async () => {
  if (state.libraryLoadingMore) return;
  await loadLibrary({ append: true });
});

el.scanLibrary.addEventListener("click", scanSelectedLibrary);

el.sort.addEventListener("change", async () => {
  state.sort = el.sort.value;
  state.stack = [];
  resetLibraryPaging();
  await loadLibrary();
});

for (const button of el.viewButtons) {
  button.addEventListener("click", async () => {
    state.selectedView = button.dataset.view;
    el.viewButtons.forEach((candidate) => candidate.classList.toggle("active", candidate === button));
    el.sort.disabled = state.selectedView === "continue";
    state.stack = [];
    resetLibraryPaging();
    await loadLibrary();
  });
}

el.detailsClose.addEventListener("click", () => el.detailsDialog.close());
el.playerClose.addEventListener("click", () => el.playerDialog.close());
el.playerDialog.addEventListener("close", async () => {
  const hadPlayer = Boolean(state.playerItem);
  const reloadFilteredView = state.stack.length === 0 && ["continue", "unwatched"].includes(state.selectedView);
  const progressReport = reportPlaybackProgress("stopped", { force: true, keepalive: true });
  state.playerItem = null;
  stopProgressReporting();
  stopSavePolling();
  el.player.pause();
  el.player.removeAttribute("src");
  el.playbackMode.hidden = true;
  el.playerSubtitleSearch.hidden = true;
  el.playerSave.hidden = true;
  el.playerDeleteSave.hidden = true;
  el.playerDeviceSave.hidden = true;
  el.playerDeviceDelete.hidden = true;
  el.playerDownloadOriginal.hidden = true;
  state.usingSavedPlayback = false;
  state.usingDevicePlayback = false;
  revokeDeviceObjectUrls();
  clearSubtitleTracks();
  el.player.load();
  await progressReport;
  if (!hadPlayer) return;
  if (reloadFilteredView) {
    await loadLibrary();
  } else {
    renderItems([...state.currentItems]);
  }
});
el.subtitleSelect.addEventListener("change", () => {
  setActiveSubtitle(Number(el.subtitleSelect.value));
});
el.player.addEventListener("play", () => {
  startProgressReporting();
});
el.player.addEventListener("pause", () => {
  if (!el.player.ended) {
    reportPlaybackProgress("paused", { force: true }).catch(() => {});
  }
});
el.player.addEventListener("timeupdate", () => {
  const item = state.playerItem;
  if (!item?.ratingKey) return;
  rememberLocalProgress(item, playbackTimeMs(), playbackDurationMs(item));
});
el.player.addEventListener("ended", () => {
  stopProgressReporting();
  reportPlaybackProgress("ended", { force: true }).catch(() => {});
});
window.addEventListener("pagehide", () => {
  reportPlaybackProgress("stopped", { force: true, keepalive: true }).catch(() => {});
});
el.playerSave.addEventListener("click", async () => {
  try {
    if (state.playerItem?.savedPlayback?.ready && !state.usingSavedPlayback) {
      await switchToSavedPlayback();
    } else {
      await savePlayback();
    }
  } catch (error) {
    el.playerSave.disabled = false;
    el.playerSave.textContent = "Retry save";
    el.playerSave.title = error.message;
  }
});
el.playerDeleteSave.addEventListener("click", async () => {
  try {
    await deleteSavedPlayback();
  } catch (error) {
    el.playerDeleteSave.disabled = false;
    el.playerDeleteSave.title = error.message;
  }
});
el.playerDeviceSave.addEventListener("click", async () => {
  try {
    if (state.playerItem?.devicePlayback?.ready && !state.usingDevicePlayback) {
      await switchToDevicePlayback();
    } else {
      await saveDevicePlayback();
    }
  } catch (error) {
    state.deviceSaveInProgress = false;
    el.playerDeviceSave.disabled = false;
    el.playerDeviceSave.textContent = "Retry device";
    el.playerDeviceSave.title = error.message;
  }
});
el.playerDeviceDelete.addEventListener("click", async () => {
  try {
    await deleteDevicePlayback();
  } catch (error) {
    el.playerDeviceDelete.disabled = false;
    el.playerDeviceDelete.title = error.message;
  }
});
el.playerDownloadOriginal.addEventListener("click", () => {
  downloadOriginalFiles();
});
el.subtitleClose.addEventListener("click", () => el.subtitleDialog.close());
el.subtitleForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await searchSubtitles();
});

boot().catch((error) => {
  showLogin();
  el.loginError.textContent = error.message;
});
