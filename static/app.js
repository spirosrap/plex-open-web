const state = {
  libraries: [],
  selectedLibrary: null,
  preferredLibraryKey: "",
  genres: [],
  genreKeysByLibrary: {},
  genresLoading: false,
  myListKeys: new Set(),
  myListUpdatingKey: null,
  selectedView: "all",
  sort: "addedAt:desc",
  stack: [],
  currentItems: [],
  pageSize: 24,
  libraryStart: 0,
  libraryTotal: 0,
  libraryLoadingMore: false,
  libraryRequestId: 0,
  libraryAbortController: null,
  searchRequestId: 0,
  searchAbortController: null,
  metadataRequests: new Map(),
  scanInProgress: false,
  surpriseInProgress: false,
  watchStateRatingKey: null,
  detailsItem: null,
  matchItem: null,
  matchResults: [],
  matchRequestId: 0,
  matchAbortController: null,
  matchSearching: false,
  matchApplyingGuid: null,
  mediaDeletionEnabled: false,
  mediaDeleteItem: null,
  mediaDeletePlan: null,
  mediaDeleteBusy: false,
  collectionItem: null,
  collectionMembership: null,
  collectionUpdating: new Set(),
  collectionCreating: false,
  collectionRenameTarget: null,
  playerItem: null,
  playerPreparationId: 0,
  playerNeighbors: null,
  autoplayNext: true,
  autoplayTimer: null,
  autoplaySeconds: 0,
  subtitleTrackElements: [],
  savePollTimer: null,
  progressTimer: null,
  lastProgressReportAt: 0,
  lastReportedTimeMs: 0,
  usingSavedPlayback: false,
  usingDevicePlayback: false,
  activeHlsSessionId: null,
  subtitleSelectionPromise: Promise.resolve(),
  deviceSaveInProgress: false,
  deviceObjectUrls: [],
  subtitleItem: null,
  subtitleResults: [],
};

const DEVICE_CACHE_MAX_BYTES = 12 * 1024 * 1024 * 1024;
const DEVICE_CACHE_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
const LOCAL_PROGRESS_KEY = "plex-open-web-progress-v1";
const SUBTITLE_PREFERENCES_KEY = "plex-open-web-subtitle-preferences-v1";
const SUBTITLE_PREFERENCES_MAX_ITEMS = 500;
const THEME_KEY = "plex-open-web-theme-v1";
const BROWSE_KEY = "plex-open-web-browse-v1";
const AUTOPLAY_NEXT_KEY = "plex-open-web-autoplay-next-v1";
const THEME_VALUES = new Set(["system", "light", "dark"]);
const VIEW_VALUES = new Set(["continue", "recent", "all", "unwatched", "collections", "mylist"]);
const SORT_VALUES = new Set(["addedAt:desc", "titleSort", "year:desc", "lastViewedAt:desc"]);
const PROGRESS_REPORT_INTERVAL_MS = 15000;

const savedBrowse = readBrowsePreferences();
state.preferredLibraryKey = typeof savedBrowse.libraryKey === "string" ? savedBrowse.libraryKey : "";
state.genreKeysByLibrary = normalizeGenrePreferences(savedBrowse.genreKeys);
state.selectedView = VIEW_VALUES.has(savedBrowse.view) ? savedBrowse.view : "all";
state.sort = SORT_VALUES.has(savedBrowse.sort) ? savedBrowse.sort : "addedAt:desc";
try {
  state.autoplayNext = localStorage.getItem(AUTOPLAY_NEXT_KEY) !== "false";
} catch {
  state.autoplayNext = true;
}

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
  genreFilter: document.querySelector("#genre-filter"),
  sort: document.querySelector("#sort"),
  surpriseMe: document.querySelector("#surprise-me"),
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
  detailsPreviousEpisode: document.querySelector("#details-previous-episode"),
  detailsNextEpisode: document.querySelector("#details-next-episode"),
  detailsSubtitles: document.querySelector("#details-subtitles"),
  detailsWatchState: document.querySelector("#details-watch-state"),
  detailsMyList: document.querySelector("#details-my-list"),
  detailsCollections: document.querySelector("#details-collections"),
  detailsFixMatch: document.querySelector("#details-fix-match"),
  detailsDeleteMedia: document.querySelector("#details-delete-media"),
  detailsClose: document.querySelector("#details-close"),
  matchDialog: document.querySelector("#match-dialog"),
  matchTitle: document.querySelector("#match-title"),
  matchForm: document.querySelector("#match-form"),
  matchQuery: document.querySelector("#match-query"),
  matchYear: document.querySelector("#match-year"),
  matchLanguage: document.querySelector("#match-language"),
  matchSearch: document.querySelector("#match-search"),
  matchStatus: document.querySelector("#match-status"),
  matchResults: document.querySelector("#match-results"),
  matchClose: document.querySelector("#match-close"),
  mediaDeleteDialog: document.querySelector("#media-delete-dialog"),
  mediaDeleteForm: document.querySelector("#media-delete-form"),
  mediaDeleteTitle: document.querySelector("#media-delete-title"),
  mediaDeleteStatus: document.querySelector("#media-delete-status"),
  mediaDeletePlan: document.querySelector("#media-delete-plan"),
  mediaDeleteSummary: document.querySelector("#media-delete-summary"),
  mediaDeletePaths: document.querySelector("#media-delete-paths"),
  mediaDeleteWarnings: document.querySelector("#media-delete-warnings"),
  mediaDeleteConfirmation: document.querySelector("#media-delete-confirmation"),
  mediaDeleteSubmit: document.querySelector("#media-delete-submit"),
  mediaDeleteCancel: document.querySelector("#media-delete-cancel"),
  mediaDeleteClose: document.querySelector("#media-delete-close"),
  collectionDialog: document.querySelector("#collection-dialog"),
  collectionTitle: document.querySelector("#collection-title"),
  collectionFilter: document.querySelector("#collection-filter"),
  collectionCreateForm: document.querySelector("#collection-create-form"),
  collectionCreateTitle: document.querySelector("#collection-create-title"),
  collectionCreate: document.querySelector("#collection-create"),
  collectionStatus: document.querySelector("#collection-status"),
  collectionList: document.querySelector("#collection-list"),
  collectionClose: document.querySelector("#collection-close"),
  collectionNameDialog: document.querySelector("#collection-name-dialog"),
  collectionNameForm: document.querySelector("#collection-name-form"),
  collectionNameInput: document.querySelector("#collection-name-input"),
  collectionNameStatus: document.querySelector("#collection-name-status"),
  collectionNameCancel: document.querySelector("#collection-name-cancel"),
  collectionNameSave: document.querySelector("#collection-name-save"),
  playerDialog: document.querySelector("#player-dialog"),
  playerTitle: document.querySelector("#player-title"),
  playbackMode: document.querySelector("#playback-mode"),
  playerError: document.querySelector("#player-error"),
  playerClose: document.querySelector("#player-close"),
  subtitleLabel: document.querySelector("#subtitle-select-label"),
  subtitleSelect: document.querySelector("#subtitle-select"),
  playerSave: document.querySelector("#player-save"),
  playerDeleteSave: document.querySelector("#player-delete-save"),
  playerDeviceSave: document.querySelector("#player-device-save"),
  playerDeviceDelete: document.querySelector("#player-device-delete"),
  playerDownloadOriginal: document.querySelector("#player-download-original"),
  playerSubtitleSearch: document.querySelector("#player-subtitle-search"),
  autoplayNextLabel: document.querySelector("#autoplay-next-label"),
  autoplayNext: document.querySelector("#autoplay-next"),
  playerNextEpisode: document.querySelector("#player-next-episode"),
  playerUpNext: document.querySelector("#player-up-next"),
  playerUpNextText: document.querySelector("#player-up-next-text"),
  playerUpNextCancel: document.querySelector("#player-up-next-cancel"),
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

function readBrowsePreferences() {
  try {
    const value = JSON.parse(localStorage.getItem(BROWSE_KEY) || "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

function persistBrowsePreferences() {
  try {
    localStorage.setItem(BROWSE_KEY, JSON.stringify({
      libraryKey: state.selectedLibrary?.key || state.preferredLibraryKey || "",
      genreKeys: state.genreKeysByLibrary,
      view: state.selectedView,
      sort: state.sort,
    }));
  } catch {
    // Browsing remains fully usable when storage is unavailable.
  }
}

function normalizeGenrePreferences(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([libraryKey, genreKey]) => (
    /^\d+$/.test(libraryKey) && typeof genreKey === "string" && /^\d+$/.test(genreKey)
  )));
}

function selectedGenreKey() {
  const libraryKey = state.selectedLibrary?.key;
  return libraryKey ? state.genreKeysByLibrary[libraryKey] || "" : "";
}

function activeGenreKey() {
  const selected = selectedGenreKey();
  return ["collections", "mylist"].includes(state.selectedView) || !state.genres.some((genre) => genre.key === selected)
    ? ""
    : selected;
}

function renderGenreFilter() {
  el.genreFilter.replaceChildren();
  el.genreFilter.add(new Option(state.genresLoading ? "Loading genres..." : "All genres", ""));
  for (const genre of state.genres) {
    el.genreFilter.add(new Option(genre.title, genre.key));
  }
  el.genreFilter.value = selectedGenreKey();
  el.genreFilter.disabled = !state.selectedLibrary
    || state.genresLoading
    || !state.genres.length
    || ["collections", "mylist"].includes(state.selectedView);
}

function syncBrowseControls() {
  el.viewButtons.forEach((button) => button.classList.toggle("active", button.dataset.view === state.selectedView));
  el.sort.value = state.sort;
  el.sort.disabled = ["continue", "collections", "mylist"].includes(state.selectedView);
  renderGenreFilter();
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
  if (item.type === "collection") {
    const count = item.childCount || item.leafCount || 0;
    return `${count} ${count === 1 ? "item" : "items"}`;
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

function episodeCode(item) {
  if (item?.type !== "episode") return "";
  const hasSeason = item.parentIndex !== null && item.parentIndex !== undefined && Number.isFinite(Number(item.parentIndex));
  const hasEpisode = item.index !== null && item.index !== undefined && Number.isFinite(Number(item.index));
  const season = hasSeason ? `S${String(item.parentIndex).padStart(2, "0")}` : "";
  const episode = hasEpisode ? `E${String(item.index).padStart(2, "0")}` : "";
  return `${season}${episode}`;
}

async function fetchEpisodeNeighbors(item) {
  if (item?.type !== "episode" || !item.ratingKey) return null;
  if (item._episodeNeighbors) return item._episodeNeighbors;
  const data = await api(`/api/episode-neighbors?${new URLSearchParams({ ratingKey: item.ratingKey })}`);
  item._episodeNeighbors = data;
  return data;
}

function subtitleSearchTitle(item) {
  if (item.media?.file) {
    return item.media.file;
  }
  const title = item.type === "episode" && item.grandparentTitle ? item.grandparentTitle : item.title;
  return [title, item.year || item.originallyAvailableAt?.slice(0, 4)].filter(Boolean).join(" ");
}

function posterMarkup(item, index = Number.MAX_SAFE_INTEGER) {
  if (item.posterUrl) {
    const firstViewport = index < 6;
    return `<img loading="${firstViewport ? "eager" : "lazy"}" decoding="async" fetchpriority="${firstViewport ? "high" : "low"}" src="${escapeAttr(item.posterUrl)}" alt="">`;
  }
  const letter = (item.title || "?").trim().slice(0, 1).toUpperCase();
  return `<div class="poster-fallback">${escapeHtml(letter)}</div>`;
}

function itemCanOpen(item) {
  return ["show", "season", "collection"].includes(item.type);
}

function itemIsPlayable(item) {
  return ["movie", "episode"].includes(item.type);
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
  updateSurpriseButton();
}

function updateScanButton() {
  if (!el.scanLibrary) return;
  el.scanLibrary.disabled = !state.selectedLibrary || state.scanInProgress;
  el.scanLibrary.textContent = state.scanInProgress ? "Scanning..." : "Scan library";
}

function updateSurpriseButton() {
  if (!el.surpriseMe) return;
  el.surpriseMe.disabled = !state.selectedLibrary || state.surpriseInProgress || state.selectedView === "mylist";
  el.surpriseMe.textContent = state.surpriseInProgress ? "Choosing..." : "Surprise me";
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

function createMediaCard(item, index) {
  const inMyList = Boolean(item.ratingKey && state.myListKeys.has(String(item.ratingKey)));
  const card = document.createElement("article");
  card.className = "media-card";
  card.dataset.itemIndex = String(index);
  card.innerHTML = `
    <button class="poster-button" data-action="open">
      ${posterMarkup(item, index)}
      ${item.type === "collection" ? '<span class="collection-badge">Collection</span>' : ""}
      ${item.viewCount ? '<span class="watched">Watched</span>' : ""}
      ${inMyList ? '<span class="my-list-badge">My List</span>' : ""}
      ${supportedSubtitles(item).length ? `<span class="subtitle-badge${inMyList ? " stacked" : ""}">CC</span>` : ""}
      ${progressMarkup(item)}
    </button>
    <div class="card-body">
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(labelFor(item))}</p>
      <div class="card-actions">
        ${itemIsPlayable(item) ? '<button data-action="play" class="mini-primary">Play</button>' : ""}
        ${item.type === "collection"
          ? `<button data-action="open" class="mini-secondary">Open</button>${item.smart ? "" : '<button data-action="delete-collection" class="mini-danger">Delete</button>'}`
          : '<button data-action="details" class="mini-secondary">Details</button>'}
      </div>
    </div>
  `;
  return card;
}

function renderLoadingGrid() {
  el.grid.classList.remove("is-refreshing");
  el.grid.setAttribute("aria-busy", "true");
  el.grid.innerHTML = Array.from({ length: 12 }, () => `
    <article class="media-card skeleton-card" aria-hidden="true">
      <div class="poster-button skeleton-block"></div>
      <div class="card-body">
        <div class="skeleton-line skeleton-title"></div>
        <div class="skeleton-line skeleton-meta"></div>
      </div>
    </article>
  `).join("");
}

function renderItems(items, { append = false } = {}) {
  const previousLength = append ? state.currentItems.length : 0;
  state.currentItems = items;
  renderBreadcrumbs();
  const current = state.stack.at(-1);
  el.viewTitle.textContent = current?.title || state.selectedLibrary?.title || "Library";
  el.grid.classList.remove("is-refreshing");
  el.grid.setAttribute("aria-busy", "false");
  if (!append) {
    el.grid.innerHTML = "";
  }
  if (!items.length) {
    const emptyContinueView = state.stack.length === 0 && state.selectedView === "continue";
    const emptyCollectionsView = state.stack.length === 0 && state.selectedView === "collections";
    const emptyMyListView = state.stack.length === 0 && state.selectedView === "mylist";
    setStatus(
      emptyContinueView
        ? "Nothing to continue."
        : emptyCollectionsView
          ? "No collections found."
          : emptyMyListView
            ? "My List is empty."
            : "No items found.",
      "muted",
    );
    updateLoadMore();
    return;
  }
  setStatus(statusTextForItems(items.length), "muted");
  const fragment = document.createDocumentFragment();
  for (let index = previousLength; index < items.length; index += 1) {
    fragment.append(createMediaCard(items[index], index));
  }
  el.grid.append(fragment);
  updateLoadMore();
}

function statusTextForItems(count) {
  const collectionsView = state.stack.length === 0 && state.selectedView === "collections";
  const myListView = state.stack.length === 0 && state.selectedView === "mylist";
  const nounCount = state.libraryTotal && state.libraryTotal > count ? state.libraryTotal : count;
  const noun = collectionsView
    ? nounCount === 1 ? "collection" : "collections"
    : myListView
      ? nounCount === 1 ? "saved item" : "saved items"
      : nounCount === 1 ? "item" : "items";
  if (state.libraryTotal && state.libraryTotal > count) {
    return `${count} of ${state.libraryTotal} ${noun}`;
  }
  return `${count} ${noun}`;
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

function browseRequestParams(libraryKey, { start = 0, includeBrowse = false } = {}) {
  const savedGenre = state.genreKeysByLibrary[libraryKey] || "";
  const genre = ["collections", "mylist"].includes(state.selectedView) ? "" : savedGenre;
  const params = new URLSearchParams({
    view: state.selectedView,
    sort: state.sort,
    genre,
    start: String(start),
    limit: String(state.pageSize),
  });
  if (includeBrowse) {
    params.set("includeBrowse", "1");
    if (libraryKey) params.set("libraryKey", libraryKey);
  }
  return params;
}

function applyGenres(libraryKey, genres) {
  state.genres = genres || [];
  state.genresLoading = false;
  const savedGenre = state.genreKeysByLibrary[libraryKey];
  const invalidSavedGenre = Boolean(savedGenre && !state.genres.some((genre) => genre.key === savedGenre));
  if (invalidSavedGenre) delete state.genreKeysByLibrary[libraryKey];
  renderGenreFilter();
  persistBrowsePreferences();
  return invalidSavedGenre;
}

function applyLibraryPage(data, { append = false } = {}) {
  if (state.selectedView === "mylist" && data.ratingKeys) {
    state.myListKeys = new Set(data.ratingKeys.map(String));
  }
  const incoming = data.items || [];
  state.libraryStart = Number(data.start || 0);
  state.libraryTotal = data.totalSize || data.size || 0;
  renderItems(append ? [...state.currentItems, ...incoming] : incoming, { append });
}

async function loadLibraries() {
  const params = browseRequestParams(state.preferredLibraryKey, { includeBrowse: true });
  const data = await api(`/api/bootstrap?${params}`);
  showVersion(data.version);
  state.mediaDeletionEnabled = Boolean(data.mediaDeletionEnabled);
  if (!data.authenticated && data.authRequired) {
    showLogin();
    return false;
  }
  showApp();
  el.serverName.textContent = data.server?.friendlyName || "Plex server";
  state.myListKeys = new Set((data.ratingKeys || []).map(String));
  state.libraries = data.libraries || [];
  const selectedKey = String(data.selectedLibraryKey || state.preferredLibraryKey || "");
  state.selectedLibrary = state.libraries.find((library) => String(library.key) === selectedKey)
    || state.libraries[0]
    || null;
  state.preferredLibraryKey = state.selectedLibrary?.key || "";
  state.stack = [];
  resetLibraryPaging();
  syncBrowseControls();
  persistBrowsePreferences();
  renderLibraries();
  if (!state.selectedLibrary) {
    renderItems([]);
    return true;
  }
  const bundle = data.browse;
  if (bundle && String(bundle.library) === String(state.selectedLibrary.key)) {
    const invalidGenre = applyGenres(state.selectedLibrary.key, bundle.genres);
    if (!invalidGenre) {
      applyLibraryPage(bundle.page || {});
      return true;
    }
  }
  await loadBrowse();
  return true;
}

async function selectLibrary(key) {
  state.selectedLibrary = state.libraries.find((library) => library.key === key);
  if (!state.selectedLibrary) return;
  state.preferredLibraryKey = state.selectedLibrary.key;
  state.stack = [];
  resetLibraryPaging();
  persistBrowsePreferences();
  renderLibraries();
  await loadBrowse();
}

async function loadBrowse() {
  if (!state.selectedLibrary) return false;
  state.libraryAbortController?.abort();
  const controller = new AbortController();
  state.libraryAbortController = controller;
  const requestId = ++state.libraryRequestId;
  const libraryKey = state.selectedLibrary.key;
  resetLibraryPaging();
  state.genres = [];
  state.genresLoading = true;
  renderGenreFilter();
  setStatus("Loading library...");
  if (state.currentItems.length) {
    el.grid.classList.add("is-refreshing");
    el.grid.setAttribute("aria-busy", "true");
  } else {
    renderLoadingGrid();
  }
  try {
    const params = browseRequestParams(libraryKey);
    const bundle = await api(`/api/browse/${encodeURIComponent(libraryKey)}?${params}`, {
      signal: controller.signal,
    });
    if (requestId !== state.libraryRequestId || state.selectedLibrary?.key !== libraryKey) return false;
    const invalidGenre = applyGenres(libraryKey, bundle.genres);
    if (invalidGenre) {
      params.set("genre", "");
      const page = await api(`/api/library/${encodeURIComponent(libraryKey)}?${params}`, {
        signal: controller.signal,
      });
      if (requestId !== state.libraryRequestId) return false;
      applyLibraryPage(page);
    } else {
      applyLibraryPage(bundle.page || {});
    }
    return true;
  } catch (error) {
    if (error.name === "AbortError" || requestId !== state.libraryRequestId) return false;
    if (!state.currentItems.length) el.grid.innerHTML = "";
    setStatus(`Could not load library: ${error.message}`, "error");
    return false;
  } finally {
    if (requestId === state.libraryRequestId) {
      state.genresLoading = false;
      state.libraryAbortController = null;
      el.grid.classList.remove("is-refreshing");
      el.grid.setAttribute("aria-busy", "false");
      renderGenreFilter();
      updateLoadMore();
    }
  }
}

function resetLibraryPaging() {
  state.libraryStart = 0;
  state.libraryTotal = 0;
  state.libraryLoadingMore = false;
  updateLoadMore();
}

async function loadLibrary({ append = false } = {}) {
  if (!state.selectedLibrary) return false;
  if (append && state.libraryLoadingMore) return false;
  state.libraryAbortController?.abort();
  const controller = new AbortController();
  state.libraryAbortController = controller;
  const requestId = ++state.libraryRequestId;
  if (!append) {
    resetLibraryPaging();
    setStatus("Loading media...");
    if (state.currentItems.length) {
      el.grid.classList.add("is-refreshing");
      el.grid.setAttribute("aria-busy", "true");
    } else {
      renderLoadingGrid();
    }
  } else {
    state.libraryLoadingMore = true;
    updateLoadMore();
    setStatus(`${statusTextForItems(state.currentItems.length)} - loading more...`, "muted");
  }
  const start = append ? state.currentItems.length : 0;
  const params = new URLSearchParams({
    view: state.selectedView,
    sort: state.sort,
    genre: activeGenreKey(),
    start: String(start),
    limit: String(state.pageSize),
  });
  try {
    const data = await api(`/api/library/${encodeURIComponent(state.selectedLibrary.key)}?${params}`, {
      signal: controller.signal,
    });
    if (requestId !== state.libraryRequestId) return false;
    applyLibraryPage(data, { append });
    return true;
  } catch (error) {
    if (error.name === "AbortError" || requestId !== state.libraryRequestId) return false;
    el.grid.classList.remove("is-refreshing");
    el.grid.setAttribute("aria-busy", "false");
    if (!state.currentItems.length) el.grid.innerHTML = "";
    setStatus(`Could not load media: ${error.message}`, "error");
    return false;
  } finally {
    if (requestId === state.libraryRequestId) {
      state.libraryLoadingMore = false;
      state.libraryAbortController = null;
      el.grid.classList.remove("is-refreshing");
      el.grid.setAttribute("aria-busy", "false");
      updateLoadMore();
    }
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

async function surpriseMe() {
  if (!state.selectedLibrary || state.surpriseInProgress) return;
  state.surpriseInProgress = true;
  updateSurpriseButton();
  setStatus(`Choosing from ${state.selectedLibrary.title}...`);
  try {
    const params = new URLSearchParams({ sectionKey: state.selectedLibrary.key });
    const genre = activeGenreKey();
    if (genre) params.set("genre", genre);
    if (state.selectedView === "unwatched") params.set("unwatched", "1");
    const data = await api(`/api/random-item?${params}`);
    if (!data.item) {
      setStatus("This library has no items to choose from.", "muted");
      return;
    }
    openDetails(data.item);
    setStatus(`Surprise pick: ${displayTitle(data.item)}.`, "success");
  } catch (error) {
    setStatus(`Could not choose an item: ${error.message}`, "error");
  } finally {
    state.surpriseInProgress = false;
    updateSurpriseButton();
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

function renderDetailsEpisodeActions(item, neighbors = null) {
  const previous = neighbors?.previous;
  const next = neighbors?.next;
  el.detailsPreviousEpisode.hidden = !previous;
  el.detailsNextEpisode.hidden = !next;
  if (previous) {
    el.detailsPreviousEpisode.textContent = `Previous ${episodeCode(previous)}`.trim();
    el.detailsPreviousEpisode.title = displayTitle(previous);
    el.detailsPreviousEpisode.onclick = () => {
      el.detailsDialog.close();
      playItem(previous);
    };
  }
  if (next) {
    el.detailsNextEpisode.textContent = `Next ${episodeCode(next)}`.trim();
    el.detailsNextEpisode.title = displayTitle(next);
    el.detailsNextEpisode.onclick = () => {
      el.detailsDialog.close();
      playItem(next);
    };
  }
  if (state.detailsItem !== item) {
    el.detailsPreviousEpisode.hidden = true;
    el.detailsNextEpisode.hidden = true;
  }
}

async function loadDetailsEpisodeActions(item) {
  renderDetailsEpisodeActions(item);
  if (item?.type !== "episode") return;
  try {
    const neighbors = await fetchEpisodeNeighbors(item);
    if (state.detailsItem === item) {
      renderDetailsEpisodeActions(item, neighbors);
    }
  } catch {
    // Episode details remain usable when adjacent metadata is temporarily unavailable.
  }
}

function openDetails(item) {
  state.detailsItem = item;
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
  el.detailsPlay.hidden = !itemIsPlayable(item);
  el.detailsPlay.onclick = () => {
    el.detailsDialog.close();
    playItem(item);
  };
  el.detailsOpen.hidden = !itemCanOpen(item);
  el.detailsOpen.onclick = async () => {
    el.detailsDialog.close();
    await openChildren(item);
  };
  el.detailsSubtitles.hidden = !itemIsPlayable(item);
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
  const canUseMyList = Boolean(item.ratingKey && ["movie", "show", "episode"].includes(item.type));
  const inMyList = canUseMyList && state.myListKeys.has(String(item.ratingKey));
  el.detailsMyList.hidden = !canUseMyList;
  el.detailsMyList.disabled = state.myListUpdatingKey === item.ratingKey;
  el.detailsMyList.textContent = state.myListUpdatingKey === item.ratingKey
    ? "Updating..."
    : inMyList
      ? "Remove from My List"
      : "Add to My List";
  el.detailsMyList.onclick = () => setMyList(item, !inMyList);
  const canManageCollections = Boolean(item.ratingKey && item.type === "movie");
  const collectionCount = Array.isArray(item.collections) ? item.collections.length : 0;
  el.detailsCollections.hidden = !canManageCollections;
  el.detailsCollections.textContent = collectionCount ? `Collections (${collectionCount})` : "Collections";
  el.detailsCollections.onclick = () => openCollectionMembership(item);
  const canFixMatch = Boolean(item.ratingKey && ["movie", "show"].includes(item.type));
  el.detailsFixMatch.hidden = !canFixMatch;
  el.detailsFixMatch.textContent = item.guid ? "Fix match" : "Match";
  el.detailsFixMatch.onclick = () => openMatchDialog(item);
  const canDeleteMedia = Boolean(
    state.mediaDeletionEnabled && item.ratingKey && ["movie", "episode"].includes(item.type),
  );
  el.detailsDeleteMedia.hidden = !canDeleteMedia;
  el.detailsDeleteMedia.onclick = () => openMediaDelete(item);
  if (!el.detailsDialog.open) {
    el.detailsDialog.showModal();
  }
  loadDetailsEpisodeActions(item);
  if (item.ratingKey && !item._hydrated) {
    hydrateItem(item).then(() => {
      if (item._hydrated && state.detailsItem === item && el.detailsDialog.open) {
        openDetails(item);
      }
    });
  }
}

function setMatchStatus(message, kind = "") {
  el.matchStatus.textContent = message;
  el.matchStatus.dataset.kind = kind;
}

function ensureMatchLanguageOption(language) {
  if (!language || [...el.matchLanguage.options].some((option) => option.value === language)) return;
  el.matchLanguage.add(new Option(language, language));
}

function renderMatchResults() {
  const busy = state.matchSearching || Boolean(state.matchApplyingGuid);
  el.matchQuery.disabled = busy;
  el.matchYear.disabled = busy;
  el.matchLanguage.disabled = busy;
  el.matchSearch.disabled = busy;
  el.matchClose.disabled = Boolean(state.matchApplyingGuid);
  el.matchSearch.textContent = state.matchSearching ? "Searching..." : "Search";
  el.matchResults.replaceChildren();
  if (state.matchSearching) {
    setMatchStatus("Searching Plex metadata...");
    return;
  }
  if (!state.matchResults.length) {
    setMatchStatus("No matching titles found.", "muted");
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const candidate of state.matchResults) {
    const row = document.createElement("article");
    row.className = "match-result";

    const poster = document.createElement("div");
    poster.className = "match-result-poster";
    if (candidate.posterUrl) {
      const image = document.createElement("img");
      image.src = candidate.posterUrl;
      image.alt = "";
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      poster.append(image);
    } else {
      const placeholder = document.createElement("span");
      placeholder.textContent = candidate.type === "show" ? "TV" : "Film";
      poster.append(placeholder);
    }

    const copy = document.createElement("div");
    copy.className = "match-result-copy";
    const heading = document.createElement("div");
    heading.className = "match-result-heading";
    const name = document.createElement("strong");
    name.textContent = candidate.name;
    heading.append(name);
    if (candidate.best) {
      const best = document.createElement("span");
      best.className = "match-badge";
      best.textContent = "Best match";
      heading.append(best);
    }
    if (candidate.current) {
      const current = document.createElement("span");
      current.className = "match-badge current";
      current.textContent = "Current";
      heading.append(current);
    }
    const metadata = document.createElement("small");
    metadata.textContent = [candidate.year, candidate.type === "show" ? "TV show" : "Movie"]
      .filter(Boolean)
      .join(" / ");
    copy.append(heading, metadata);
    if (candidate.summary) {
      const summary = document.createElement("p");
      summary.textContent = candidate.summary;
      copy.append(summary);
    }

    const use = document.createElement("button");
    use.type = "button";
    use.className = "primary-button match-use";
    use.textContent = state.matchApplyingGuid === candidate.guid
      ? "Matching..."
      : candidate.current
        ? "Refresh match"
        : "Use match";
    use.disabled = Boolean(state.matchApplyingGuid);
    use.addEventListener("click", () => applyMediaMatch(candidate));
    row.append(poster, copy, use);
    fragment.append(row);
  }
  el.matchResults.append(fragment);
  setMatchStatus(`${state.matchResults.length} ${state.matchResults.length === 1 ? "match" : "matches"}`);
}

async function searchMediaMatches() {
  const item = state.matchItem;
  const title = el.matchQuery.value.trim();
  if (!item?.ratingKey || !title || state.matchApplyingGuid) return;
  state.matchAbortController?.abort();
  const controller = new AbortController();
  state.matchAbortController = controller;
  const requestId = ++state.matchRequestId;
  state.matchSearching = true;
  state.matchResults = [];
  renderMatchResults();
  const params = new URLSearchParams({
    ratingKey: item.ratingKey,
    title,
    language: el.matchLanguage.value,
  });
  if (el.matchYear.value.trim()) params.set("year", el.matchYear.value.trim());
  let failure = "";
  try {
    const data = await api(`/api/media-match?${params}`, { signal: controller.signal });
    if (requestId !== state.matchRequestId || state.matchItem !== item) return;
    state.matchResults = data.results || [];
    ensureMatchLanguageOption(data.language);
    if (data.language) el.matchLanguage.value = data.language;
  } catch (error) {
    if (error.name !== "AbortError" && requestId === state.matchRequestId) {
      failure = error.message;
    }
  } finally {
    if (requestId === state.matchRequestId) {
      state.matchSearching = false;
      state.matchAbortController = null;
      renderMatchResults();
      if (failure) setMatchStatus(failure, "error");
    }
  }
}

async function openMatchDialog(item) {
  if (!item?.ratingKey || !["movie", "show"].includes(item.type)) return;
  state.matchAbortController?.abort();
  state.matchItem = item;
  state.matchResults = [];
  state.matchSearching = false;
  state.matchApplyingGuid = null;
  el.matchTitle.textContent = `${item.guid ? "Fix match" : "Match"} for ${displayTitle(item)}`;
  el.matchQuery.value = item.title || "";
  el.matchYear.value = item.year || "";
  const library = state.libraries.find(
    (candidate) => String(candidate.key) === String(item.librarySectionID),
  );
  const language = library?.language || "el-GR";
  ensureMatchLanguageOption(language);
  el.matchLanguage.value = language;
  setMatchStatus("");
  if (!el.matchDialog.open) el.matchDialog.showModal();
  await searchMediaMatches();
}

async function applyMediaMatch(candidate) {
  const item = state.matchItem;
  if (!item?.ratingKey || state.matchApplyingGuid) return;
  const year = candidate.year ? ` (${candidate.year})` : "";
  const action = candidate.current ? "Refresh metadata from" : "Use";
  const confirmed = window.confirm(
    `${action} ${candidate.name}${year} for ${displayTitle(item)}? Plex will update its title, poster, description, and related metadata.`,
  );
  if (!confirmed) return;

  state.matchApplyingGuid = candidate.guid;
  renderMatchResults();
  setMatchStatus("Applying Plex match...");
  let failure = "";
  try {
    const result = await api("/api/media-match", {
      method: "POST",
      body: JSON.stringify({
        ratingKey: item.ratingKey,
        guid: candidate.guid,
        name: candidate.name,
        year: candidate.year,
      }),
    });
    let refreshed = result.item;
    await sleep(700);
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const data = await api(
        `/api/metadata/${encodeURIComponent(item.ratingKey)}?refresh=1&match=${Date.now()}`,
      );
      if (data.item) refreshed = data.item;
      if (refreshed?.guid === candidate.guid) break;
      await sleep(500 + attempt * 250);
    }
    if (refreshed) {
      Object.assign(item, refreshed, { _hydrated: true, _metadataFailedAt: 0 });
    }
    state.metadataRequests.delete(String(item.ratingKey));
    for (const entry of state.stack) {
      if (entry.item?.ratingKey === item.ratingKey) entry.title = item.title;
    }
    renderItems([...state.currentItems]);
    if (el.detailsDialog.open) openDetails(item);
    el.matchDialog.close();
    setStatus(
      candidate.current
        ? `Plex metadata refreshed for ${displayTitle(item)}.`
        : `Matched as ${displayTitle(item)}. Plex is refreshing its metadata.`,
      "success",
    );
  } catch (error) {
    failure = error.message;
  } finally {
    state.matchApplyingGuid = null;
    if (el.matchDialog.open) renderMatchResults();
    if (failure) setMatchStatus(failure, "error");
  }
}

function setMediaDeleteStatus(message, kind = "") {
  el.mediaDeleteStatus.textContent = message;
  el.mediaDeleteStatus.dataset.kind = kind;
}

function updateMediaDeleteSubmit() {
  const confirmed = el.mediaDeleteConfirmation.value.trim() === "DELETE";
  el.mediaDeleteSubmit.disabled = state.mediaDeleteBusy
    || !state.mediaDeletePlan?.canDelete
    || !confirmed;
  el.mediaDeleteSubmit.textContent = state.mediaDeleteBusy ? "Deleting..." : "Delete permanently";
  el.mediaDeleteCancel.disabled = state.mediaDeleteBusy;
  el.mediaDeleteClose.disabled = state.mediaDeleteBusy;
  el.mediaDeleteConfirmation.disabled = state.mediaDeleteBusy || !state.mediaDeletePlan?.canDelete;
}

function renderMediaDeletePlan(plan) {
  state.mediaDeletePlan = plan;
  el.mediaDeletePlan.hidden = false;
  const fileLabel = `${plan.fileCount} ${plan.fileCount === 1 ? "file" : "files"}`;
  const folderLabel = plan.folderCount
    ? ` and ${plan.folderCount} complete ${plan.folderCount === 1 ? "folder" : "folders"}`
    : "";
  el.mediaDeleteSummary.textContent = `${fileLabel}${folderLabel} (${plan.totalSizeText}) will be permanently removed.`;
  const pathRows = [
    ...(plan.folders || []).map((path) => ({ path, label: "Folder" })),
    ...(plan.files || []).map((path) => ({ path, label: "File" })),
  ];
  el.mediaDeletePaths.innerHTML = pathRows
    .map(({ path, label }) => `<div><strong>${label}:</strong> ${escapeHtml(path)}</div>`)
    .join("");
  el.mediaDeletePaths.hidden = pathRows.length === 0;
  el.mediaDeleteWarnings.innerHTML = (plan.warnings || [])
    .map((warning) => `<li>${escapeHtml(warning)}</li>`)
    .join("");
  el.mediaDeleteWarnings.hidden = !(plan.warnings || []).length;
  setMediaDeleteStatus(
    plan.canDelete ? "Review the exact disk changes below." : plan.blockReason || "Disk deletion is currently blocked.",
    plan.canDelete ? "" : "error",
  );
  updateMediaDeleteSubmit();
}

async function openMediaDelete(item) {
  if (!item?.ratingKey || !state.mediaDeletionEnabled) return;
  state.mediaDeleteItem = item;
  state.mediaDeletePlan = null;
  state.mediaDeleteBusy = false;
  el.mediaDeleteTitle.textContent = `Delete ${displayTitle(item)}?`;
  el.mediaDeletePlan.hidden = true;
  el.mediaDeleteConfirmation.value = "";
  setMediaDeleteStatus("Inspecting files...");
  updateMediaDeleteSubmit();
  if (el.detailsDialog.open) el.detailsDialog.close();
  if (!el.mediaDeleteDialog.open) el.mediaDeleteDialog.showModal();
  try {
    const plan = await api(`/api/media-delete?ratingKey=${encodeURIComponent(item.ratingKey)}`);
    if (state.mediaDeleteItem !== item || !el.mediaDeleteDialog.open) return;
    renderMediaDeletePlan(plan);
    el.mediaDeleteConfirmation.focus();
  } catch (error) {
    if (state.mediaDeleteItem !== item || !el.mediaDeleteDialog.open) return;
    setMediaDeleteStatus(error.message, "error");
    updateMediaDeleteSubmit();
  }
}

function removeDeletedItem(item) {
  const ratingKey = String(item?.ratingKey || "");
  if (!ratingKey) return;
  const previousLength = state.currentItems.length;
  const keep = (candidate) => String(candidate?.ratingKey || "") !== ratingKey;
  state.stack.forEach((entry) => {
    entry.items = (entry.items || []).filter(keep);
  });
  const nextItems = state.currentItems.filter(keep);
  if (nextItems.length < previousLength && state.stack.length === 0) {
    state.libraryTotal = Math.max(0, state.libraryTotal - 1);
  }
  state.myListKeys.delete(ratingKey);
  state.metadataRequests.delete(ratingKey);
  clearLocalProgress(item);
  renderItems(nextItems);
}

async function submitMediaDelete() {
  const item = state.mediaDeleteItem;
  const plan = state.mediaDeletePlan;
  if (!item?.ratingKey || !plan?.confirmationToken || state.mediaDeleteBusy) return;
  state.mediaDeleteBusy = true;
  setMediaDeleteStatus("Deleting original files from disk...");
  updateMediaDeleteSubmit();
  try {
    const result = await api("/api/media-delete", {
      method: "POST",
      body: JSON.stringify({
        ratingKey: item.ratingKey,
        confirmationToken: plan.confirmationToken,
        confirmation: el.mediaDeleteConfirmation.value.trim(),
      }),
    });
    removeDeletedItem(item);
    el.mediaDeleteDialog.close();
    const scan = result.scanStarted ? " Plex is scanning the library." : "";
    setStatus(`Deleted ${displayTitle(item)} from disk.${scan}`, "success");
  } catch (error) {
    setMediaDeleteStatus(error.message, "error");
  } finally {
    state.mediaDeleteBusy = false;
    updateMediaDeleteSubmit();
  }
}

function setCollectionStatus(message, kind = "muted") {
  el.collectionStatus.textContent = message;
  el.collectionStatus.dataset.kind = kind;
}

function renderCollectionMembership() {
  const data = state.collectionMembership;
  const query = el.collectionFilter.value.trim().toLowerCase();
  const collections = (data?.collections || []).filter((collection) =>
    !query || collection.title.toLowerCase().includes(query)
  );
  el.collectionList.innerHTML = "";
  el.collectionCreate.disabled = state.collectionCreating;
  el.collectionCreateTitle.disabled = state.collectionCreating;
  el.collectionCreate.textContent = state.collectionCreating ? "Creating..." : "Create";
  if (!data) {
    setCollectionStatus("Loading collections...");
    return;
  }
  if (!collections.length) {
    setCollectionStatus(query ? "No collections match this filter." : "This library has no collections.");
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const collection of collections) {
    const row = document.createElement("div");
    row.className = `collection-row${collection.editable ? "" : " is-readonly"}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.id = `collection-membership-${collection.ratingKey}`;
    checkbox.checked = Boolean(collection.member);
    checkbox.disabled = !collection.editable || state.collectionUpdating.has(collection.ratingKey);
    checkbox.setAttribute("aria-label", `${collection.member ? "Remove from" : "Add to"} ${collection.title}`);
    checkbox.addEventListener("change", () => {
      setCollectionMembership(collection, checkbox.checked);
    });

    const label = document.createElement("label");
    label.htmlFor = checkbox.id;
    const title = document.createElement("strong");
    title.textContent = collection.title;
    label.append(title);
    if (!collection.editable) {
      const note = document.createElement("small");
      note.textContent = "Smart collection - managed automatically by Plex";
      label.append(note);
    }
    const count = document.createElement("span");
    count.className = "collection-count";
    count.textContent = `${collection.childCount} ${collection.childCount === 1 ? "movie" : "movies"}`;
    const actions = document.createElement("span");
    actions.className = "collection-actions";
    if (collection.editable) {
      const rename = document.createElement("button");
      rename.type = "button";
      rename.className = "collection-action";
      rename.textContent = "Rename";
      rename.disabled = state.collectionUpdating.has(collection.ratingKey);
      rename.addEventListener("click", () => openCollectionRename(collection));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "collection-action danger";
      remove.textContent = "Delete";
      remove.disabled = state.collectionUpdating.has(collection.ratingKey);
      remove.addEventListener("click", () => deleteCollection(collection));
      actions.append(rename, remove);
    }
    row.append(checkbox, label, count, actions);
    fragment.append(row);
  }
  el.collectionList.append(fragment);
  const memberCount = data.collections.filter((collection) => collection.member).length;
  setCollectionStatus(`${memberCount} of ${data.collections.length} collections selected.`);
}

async function openCollectionMembership(item) {
  if (!item?.ratingKey || item.type !== "movie") return;
  state.collectionItem = item;
  state.collectionMembership = null;
  state.collectionUpdating.clear();
  el.collectionTitle.textContent = `Collections for ${displayTitle(item)}`;
  el.collectionFilter.value = "";
  el.collectionCreateTitle.value = "";
  renderCollectionMembership();
  if (!el.collectionDialog.open) {
    el.collectionDialog.showModal();
  }
  try {
    state.collectionMembership = await api(
      `/api/collection-membership?${new URLSearchParams({ ratingKey: item.ratingKey })}`
    );
    if (state.collectionItem === item) {
      Object.assign(item, state.collectionMembership.item || {});
      renderCollectionMembership();
      if (el.detailsDialog.open) openDetails(item);
    }
  } catch (error) {
    setCollectionStatus(`Could not load collections: ${error.message}`, "error");
  }
}

function collectionErrorMessage(message) {
  const messages = {
    collection_title_already_exists: "A collection with this name already exists.",
    invalid_collection_title: "Enter a collection name between 1 and 120 characters.",
    smart_collection_read_only: "Smart collections are managed automatically by Plex.",
    collection_not_found: "This collection no longer exists in Plex.",
  };
  return messages[message] || message;
}

function applyCollectionManagementResponse(data) {
  const item = state.collectionItem;
  state.collectionMembership = data;
  if (item) {
    Object.assign(item, data.item || {});
    if (el.detailsDialog.open) openDetails(item);
  }
}

async function createCollection(title) {
  const item = state.collectionItem;
  if (!item?.ratingKey || state.collectionCreating) return;
  state.collectionCreating = true;
  renderCollectionMembership();
  setCollectionStatus(`Creating ${title}...`);
  let resultStatus;
  try {
    const data = await api("/api/collection-management", {
      method: "POST",
      body: JSON.stringify({ action: "create", ratingKey: item.ratingKey, title }),
    });
    applyCollectionManagementResponse(data);
    el.collectionCreateTitle.value = "";
    el.collectionFilter.value = "";
    resultStatus = [`Created ${title} and added ${displayTitle(item)}.`, "success"];
  } catch (error) {
    resultStatus = [`Could not create collection: ${collectionErrorMessage(error.message)}`, "error"];
  } finally {
    state.collectionCreating = false;
    renderCollectionMembership();
    setCollectionStatus(...resultStatus);
  }
}

function openCollectionRename(collection) {
  state.collectionRenameTarget = collection;
  el.collectionNameInput.value = collection.title;
  el.collectionNameStatus.textContent = "";
  el.collectionNameStatus.dataset.kind = "muted";
  el.collectionNameSave.disabled = false;
  el.collectionNameDialog.showModal();
  el.collectionNameInput.select();
}

async function renameCollection(collection, title) {
  const item = state.collectionItem;
  if (!item?.ratingKey || !collection?.ratingKey || state.collectionUpdating.has(collection.ratingKey)) return;
  state.collectionUpdating.add(collection.ratingKey);
  el.collectionNameSave.disabled = true;
  el.collectionNameStatus.textContent = "Renaming...";
  let successMessage = null;
  try {
    const data = await api("/api/collection-management", {
      method: "POST",
      body: JSON.stringify({
        action: "rename",
        ratingKey: item.ratingKey,
        collectionRatingKey: collection.ratingKey,
        title,
      }),
    });
    applyCollectionManagementResponse(data);
    const current = state.stack.at(-1);
    if (current?.item?.ratingKey === collection.ratingKey) {
      current.title = title;
      current.item.title = title;
      el.viewTitle.textContent = title;
      renderBreadcrumbs();
    }
    el.collectionNameDialog.close();
    successMessage = `Renamed ${collection.title} to ${title}.`;
  } catch (error) {
    el.collectionNameStatus.textContent = collectionErrorMessage(error.message);
    el.collectionNameStatus.dataset.kind = "error";
  } finally {
    state.collectionUpdating.delete(collection.ratingKey);
    el.collectionNameSave.disabled = false;
    if (successMessage && el.collectionDialog.open) {
      renderCollectionMembership();
      setCollectionStatus(successMessage, "success");
    }
  }
}

async function deleteCollection(collection) {
  const item = state.collectionItem;
  if (!item?.ratingKey || !collection?.ratingKey || state.collectionUpdating.has(collection.ratingKey)) return;
  const confirmed = window.confirm(
    `Delete ${collection.title}? The movies will remain in your Plex library.`
  );
  if (!confirmed) return;
  state.collectionUpdating.add(collection.ratingKey);
  renderCollectionMembership();
  setCollectionStatus(`Deleting ${collection.title}...`);
  let resultStatus;
  try {
    const data = await api("/api/collection-management", {
      method: "POST",
      body: JSON.stringify({
        action: "delete",
        ratingKey: item.ratingKey,
        collectionRatingKey: collection.ratingKey,
      }),
    });
    applyCollectionManagementResponse(data);
    const deletingOpenCollection = state.stack.at(-1)?.item?.ratingKey === collection.ratingKey;
    if (deletingOpenCollection) {
      el.collectionDialog.close();
      el.detailsDialog.close();
      state.stack = [];
      await loadLibrary();
      setStatus(`Deleted ${collection.title}. Movies remain in the library.`, "success");
      return;
    }
    resultStatus = [`Deleted ${collection.title}. Movies remain in the library.`, "success"];
  } catch (error) {
    resultStatus = [`Could not delete collection: ${collectionErrorMessage(error.message)}`, "error"];
  } finally {
    state.collectionUpdating.delete(collection.ratingKey);
    if (el.collectionDialog.open) {
      renderCollectionMembership();
      if (resultStatus) setCollectionStatus(...resultStatus);
    }
  }
}

async function deleteCollectionFromLibrary(item) {
  if (!state.selectedLibrary?.key || item?.type !== "collection" || !item.ratingKey || item.smart) return;
  const libraryKey = state.selectedLibrary.key;
  const requestId = state.libraryRequestId;
  const confirmed = window.confirm(
    `Delete ${item.title}? The movies will remain in your Plex library.`
  );
  if (!confirmed) return;
  setStatus(`Deleting ${item.title}...`);
  try {
    await api("/api/collection-management", {
      method: "POST",
      body: JSON.stringify({
        action: "delete",
        sectionKey: libraryKey,
        collectionRatingKey: item.ratingKey,
      }),
    });
    if (state.selectedLibrary?.key !== libraryKey || state.libraryRequestId !== requestId) {
      setStatus(`Deleted ${item.title}. Movies remain in the library.`, "success");
      return;
    }
    const remaining = state.currentItems.filter((candidate) => candidate.ratingKey !== item.ratingKey);
    state.libraryTotal = Math.max(0, state.libraryTotal - 1);
    renderItems(remaining);
    setStatus(`Deleted ${item.title}. Movies remain in the library.`, "success");
  } catch (error) {
    setStatus(`Could not delete collection: ${collectionErrorMessage(error.message)}`, "error");
  }
}

async function refreshOpenCollection() {
  const current = state.stack.at(-1);
  if (current?.item?.type !== "collection" || !current.item.ratingKey) return;
  const data = await api(`/api/children/${encodeURIComponent(current.item.ratingKey)}`);
  current.items = data.items || [];
  renderItems(current.items);
}

async function setCollectionMembership(collection, member) {
  const item = state.collectionItem;
  if (!item?.ratingKey || !collection?.ratingKey || state.collectionUpdating.has(collection.ratingKey)) return;
  state.collectionUpdating.add(collection.ratingKey);
  renderCollectionMembership();
  setCollectionStatus(`${member ? "Adding to" : "Removing from"} ${collection.title}...`);
  let resultStatus = null;
  try {
    const data = await api("/api/collection-membership", {
      method: "POST",
      body: JSON.stringify({
        ratingKey: item.ratingKey,
        collectionRatingKey: collection.ratingKey,
        member,
      }),
    });
    state.collectionMembership = data;
    Object.assign(item, data.item || {});
    if (el.detailsDialog.open) openDetails(item);
    try {
      await refreshOpenCollection();
    } catch {
      // The confirmed Plex membership remains authoritative if the background list refresh fails.
    }
    resultStatus = [`${member ? "Added to" : "Removed from"} ${collection.title}.`, "success"];
  } catch (error) {
    resultStatus = [`Could not update ${collection.title}: ${error.message}`, "error"];
  } finally {
    state.collectionUpdating.delete(collection.ratingKey);
    renderCollectionMembership();
    if (resultStatus) setCollectionStatus(...resultStatus);
  }
}

async function setMyList(item, saved) {
  if (!item?.ratingKey || state.myListUpdatingKey) return;
  state.myListUpdatingKey = item.ratingKey;
  openDetails(item);
  try {
    const data = await api("/api/my-list", {
      method: "POST",
      body: JSON.stringify({ ratingKey: item.ratingKey, saved }),
    });
    state.myListKeys = new Set((data.ratingKeys || []).map(String));
    item.inMyList = saved;
    const reloadMyList = state.stack.length === 0 && state.selectedView === "mylist";
    if (reloadMyList) {
      el.detailsDialog.close();
      await loadLibrary();
    } else {
      renderItems([...state.currentItems]);
    }
    setStatus(`${displayTitle(item)} ${saved ? "added to" : "removed from"} My List.`, "success");
  } catch (error) {
    setStatus(`Could not update My List: ${error.message}`, "error");
  } finally {
    state.myListUpdatingKey = null;
    if (el.detailsDialog.open) openDetails(item);
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
  if (item._metadataFailedAt && Date.now() - item._metadataFailedAt < 10000) {
    return item;
  }
  const key = String(item.ratingKey);
  let request = state.metadataRequests.get(key);
  if (!request) {
    request = api(`/api/metadata/${encodeURIComponent(key)}`)
      .finally(() => state.metadataRequests.delete(key));
    state.metadataRequests.set(key, request);
  }
  try {
    const data = await request;
    if (data?.item) {
      Object.assign(item, data.item, { _hydrated: true, _metadataFailedAt: 0 });
    }
  } catch {
    item._metadataFailedAt = Date.now();
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

function subtitlePreferenceStore() {
  try {
    const value = JSON.parse(localStorage.getItem(SUBTITLE_PREFERENCES_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function subtitlePreferenceIdentity(subtitle) {
  for (const [prefix, value] of [
    ["id", subtitle?.id],
    ["stream", subtitle?.streamId],
    ["key", subtitle?.key],
  ]) {
    const normalized = String(value || "").trim();
    if (normalized) {
      return `${prefix}:${normalized}`;
    }
  }
  return `meta:${[
    subtitle?.source,
    subtitle?.srclang || subtitle?.languageCode,
    subtitle?.label || subtitle?.displayTitle || subtitle?.title,
    subtitle?.codec,
    subtitle?.forced ? "forced" : "",
    subtitle?.hearingImpaired ? "sdh" : "",
  ].map((value) => String(value || "").trim().toLowerCase()).join("|")}`;
}

function subtitlePreferenceFor(item) {
  if (!item?.ratingKey) return null;
  const entry = subtitlePreferenceStore()[item.ratingKey];
  return entry && typeof entry.choice === "string" ? entry : null;
}

function rememberSubtitlePreference(item, index, subtitles = supportedSubtitles(item)) {
  if (!item?.ratingKey) return;
  const choice = index < 0 ? "off" : subtitlePreferenceIdentity(subtitles[index]);
  if (!choice || choice === "meta:|||||") return;
  const store = subtitlePreferenceStore();
  store[item.ratingKey] = { choice, updatedAt: Date.now() };
  const staleKeys = Object.entries(store)
    .sort((left, right) => Number(right[1]?.updatedAt || 0) - Number(left[1]?.updatedAt || 0))
    .slice(SUBTITLE_PREFERENCES_MAX_ITEMS)
    .map(([ratingKey]) => ratingKey);
  staleKeys.forEach((ratingKey) => delete store[ratingKey]);
  try {
    localStorage.setItem(SUBTITLE_PREFERENCES_KEY, JSON.stringify(store));
  } catch {
    // Plex persistence still applies when browser storage is unavailable.
  }
}

function preferredSubtitleIndex(item, subtitles) {
  const preference = subtitlePreferenceFor(item);
  if (preference?.choice === "off") {
    return -1;
  }
  if (preference?.choice) {
    const rememberedIndex = subtitles.findIndex(
      (subtitle) => subtitlePreferenceIdentity(subtitle) === preference.choice,
    );
    if (rememberedIndex >= 0) {
      return rememberedIndex;
    }
  }
  let selectedIndex = subtitles.findIndex((subtitle) => subtitle.selected);
  if (selectedIndex < 0) {
    selectedIndex = subtitles.findIndex((subtitle) => subtitle.default || subtitle.forced);
  }
  return selectedIndex;
}

async function persistSubtitleSelection(item, index) {
  const subtitles = supportedSubtitles(item);
  const subtitle = index >= 0 ? subtitles[index] : null;
  const partId = String(subtitle?.partId || item?.media?.partId || "").trim();
  const candidateStreamId = subtitle?.streamId || subtitle?.id;
  const streamId = index < 0 ? "0" : String(candidateStreamId || "").trim();
  if (!item?.ratingKey || !/^\d+$/.test(partId) || !/^\d+$/.test(streamId)) {
    return { ok: true, plexSaved: false };
  }
  const data = await api("/api/subtitle-selection", {
    method: "POST",
    body: JSON.stringify({ ratingKey: item.ratingKey, partId, streamId }),
    keepalive: true,
  });
  for (const candidate of item.subtitles || []) {
    const candidateId = String(candidate.streamId || candidate.id || "");
    candidate.selected = streamId !== "0" && candidateId === streamId;
  }
  return { ...data, plexSaved: true };
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
  const selectedIndex = preferredSubtitleIndex(item, subtitles);
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

function compatibilityTranscodeRequired(item) {
  return Boolean(
    item.playback?.compatibilityTranscodeRequired
    || item.playback?.audioTranscodeRequired
    || item.playback?.videoTranscodeRequired,
  );
}

function liveStreamUrlFor(item) {
  const needsCompatibility = compatibilityTranscodeRequired(item);
  const url = needsCompatibility && item.playback.compatibleStreamUrl
    ? item.playback.compatibleStreamUrl
    : item.streamUrl;
  if (!url) {
    return url;
  }
  const streamUrl = new URL(url, window.location.origin);
  if (needsCompatibility && el.player.canPlayType("application/vnd.apple.mpegurl")) {
    streamUrl.searchParams.set("format", "hls");
  }
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
  if (compatibilityTranscodeRequired(item)) {
    const nativeHls = Boolean(el.player.canPlayType("application/vnd.apple.mpegurl"));
    const videoTranscode = Boolean(item.playback?.videoTranscodeRequired);
    const audioTranscode = Boolean(item.playback?.audioTranscodeRequired);
    if (nativeHls) {
      el.playbackMode.textContent = "VOD H.264 + AAC";
    } else if (videoTranscode && audioTranscode) {
      el.playbackMode.textContent = "H.264 + AAC";
    } else if (videoTranscode) {
      el.playbackMode.textContent = "H.264 video";
    } else {
      el.playbackMode.textContent = nativeHls ? "VOD + AAC" : "AAC audio";
    }
    const reasons = [
      item.playback?.videoTranscodeReason,
      item.playback?.audioTranscodeReason,
    ].filter(Boolean);
    if (nativeHls) {
      reasons.push("Timestamp-aligned video segments keep subtitles synchronized.");
    }
    el.playbackMode.title = reasons.join("; ") || "Media is being converted for browser playback.";
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

function newHlsSessionId() {
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function stopActiveHlsSession({ keepalive = false } = {}) {
  const id = state.activeHlsSessionId;
  state.activeHlsSessionId = null;
  if (!id) return;
  fetch("/api/plex-hls-stop", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
    keepalive,
  }).catch(() => {});
}

function playerStreamUrl(streamUrl) {
  const url = new URL(streamUrl, window.location.origin);
  if (url.searchParams.get("format") !== "hls") {
    stopActiveHlsSession();
    return streamUrl;
  }
  let sessionId = url.searchParams.get("session") || "";
  if (!/^[a-f0-9]{32}$/.test(sessionId)) {
    sessionId = newHlsSessionId();
    url.searchParams.set("session", sessionId);
  }
  if (state.activeHlsSessionId && state.activeHlsSessionId !== sessionId) {
    stopActiveHlsSession();
  }
  state.activeHlsSessionId = sessionId;
  return url.origin === window.location.origin ? `${url.pathname}${url.search}` : url.href;
}

function loadPlayerSource(item, streamUrl, { resumeTime = 0, autoplay = true } = {}) {
  el.playerError.hidden = true;
  el.playerError.textContent = "";
  clearSubtitleTracks();
  const preparedStreamUrl = playerStreamUrl(streamUrl);
  const isHlsStream = new URL(preparedStreamUrl, window.location.origin).searchParams.get("format") === "hls";
  const applyResume = () => {
    reapplyActiveSubtitle();
    if ((isHlsStream || resumeTime > 0) && Number.isFinite(resumeTime)) {
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
  el.player.src = preparedStreamUrl;
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

function persistAutoplayNext() {
  try {
    localStorage.setItem(AUTOPLAY_NEXT_KEY, String(state.autoplayNext));
  } catch {
    // The setting remains active for this page when storage is unavailable.
  }
}

function cancelAutoplayCountdown() {
  if (state.autoplayTimer) {
    clearInterval(state.autoplayTimer);
    state.autoplayTimer = null;
  }
  state.autoplaySeconds = 0;
  el.playerUpNext.hidden = true;
}

function updatePlayerEpisodeControls(item = state.playerItem) {
  const isEpisode = item?.type === "episode";
  const next = isEpisode ? state.playerNeighbors?.next : null;
  el.autoplayNext.checked = state.autoplayNext;
  el.autoplayNextLabel.hidden = !isEpisode;
  el.playerNextEpisode.hidden = !next;
  if (next) {
    el.playerNextEpisode.textContent = `Next ${episodeCode(next)}`.trim();
    el.playerNextEpisode.title = displayTitle(next);
  }
}

async function loadPlayerEpisodeNeighbors(item) {
  state.playerNeighbors = null;
  updatePlayerEpisodeControls(item);
  if (item?.type !== "episode") return;
  try {
    const neighbors = await fetchEpisodeNeighbors(item);
    if (state.playerItem === item) {
      state.playerNeighbors = neighbors;
      updatePlayerEpisodeControls(item);
    }
  } catch {
    // Playback remains available if adjacent episode metadata cannot be loaded.
  }
}

function renderAutoplayCountdown(next) {
  const code = episodeCode(next);
  el.playerUpNextText.textContent = `Up next in ${state.autoplaySeconds}s: ${code} ${next.title}`.trim();
  el.playerUpNext.hidden = false;
}

async function playAdjacentEpisode(item, { ended = false } = {}) {
  if (!item || !state.playerItem) return;
  cancelAutoplayCountdown();
  if (!ended) {
    await reportPlaybackProgress("stopped", { force: true });
  }
  stopProgressReporting();
  stopSavePolling();
  el.player.pause();
  await playItem(item);
}

function scheduleAutoplayNext() {
  cancelAutoplayCountdown();
  const currentKey = state.playerItem?.ratingKey;
  const next = state.playerNeighbors?.next;
  if (!state.autoplayNext || !currentKey || !next) return;
  state.autoplaySeconds = 5;
  renderAutoplayCountdown(next);
  state.autoplayTimer = setInterval(() => {
    if (state.playerItem?.ratingKey !== currentKey || !state.autoplayNext) {
      cancelAutoplayCountdown();
      return;
    }
    state.autoplaySeconds -= 1;
    if (state.autoplaySeconds <= 0) {
      cancelAutoplayCountdown();
      playAdjacentEpisode(next, { ended: true }).catch((error) => {
        setStatus(`Could not play the next episode: ${error.message}`, "error");
      });
      return;
    }
    renderAutoplayCountdown(next);
  }, 1000);
}

async function playItem(item) {
  cancelAutoplayCountdown();
  const preparationId = ++state.playerPreparationId;
  state.playerItem = item;
  el.playerTitle.textContent = displayTitle(item);
  el.playbackMode.textContent = "Preparing...";
  el.playbackMode.title = "Checking the best available playback source.";
  el.playbackMode.hidden = false;
  if (!el.playerDialog.open) {
    el.playerDialog.showModal();
  }
  const results = await Promise.allSettled([
    hydrateItem(item),
    (async () => {
      await pruneDeviceCache();
      await refreshDevicePlayback(item);
    })(),
  ]);
  if (preparationId !== state.playerPreparationId || !el.playerDialog.open) return;
  if (results[1].status === "rejected") {
    item.devicePlayback = { state: "unsupported", ready: false, supported: false };
  }
  if (!item.savedPlayback || item.savedPlayback.state === "unknown") {
    try {
      await refreshSavedPlayback(item);
    } catch {
      item.savedPlayback = item.savedPlayback || { state: "unavailable", ready: false };
    }
  }
  if (preparationId !== state.playerPreparationId || !el.playerDialog.open) return;
  state.playerItem = item;
  state.playerNeighbors = null;
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
    revokeDeviceObjectUrls();
    const streamUrl = streamUrlFor(item);
    if (!streamUrl) {
      el.playbackMode.textContent = "No playable source";
      el.playbackMode.title = "Plex did not return a stream for this item.";
      setStatus(`Could not play ${displayTitle(item)}: no playable source.`, "error");
      return;
    }
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
  updatePlayerEpisodeControls(item);
  loadPlayerEpisodeNeighbors(item);
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
  state.searchAbortController?.abort();
  state.searchRequestId += 1;
  if (query.trim().length < 2) {
    await loadLibrary();
    return;
  }
  state.stack = [{ title: `Search: ${query}`, items: [] }];
  state.libraryTotal = 0;
  updateLoadMore();
  setStatus("Searching...");
  const controller = new AbortController();
  state.searchAbortController = controller;
  const requestId = state.searchRequestId;
  const data = await api(`/api/search?${new URLSearchParams({ query })}`, { signal: controller.signal });
  if (requestId !== state.searchRequestId) return;
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
  try {
    await search(el.searchInput.value);
  } catch (error) {
    if (error.name !== "AbortError") setStatus(`Could not search: ${error.message}`, "error");
  }
});

function prefetchCardMetadata(event) {
  const card = event.target.closest(".media-card[data-item-index]");
  if (!card) return;
  const item = state.currentItems[Number(card.dataset.itemIndex)];
  if (item?.ratingKey && itemIsPlayable(item) && !item._hydrated) {
    hydrateItem(item).catch(() => {});
  }
}

el.grid.addEventListener("pointerover", prefetchCardMetadata, { passive: true });
el.grid.addEventListener("focusin", prefetchCardMetadata);

el.grid.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  const card = event.target.closest(".media-card[data-item-index]");
  if (!button || !card) return;
  const item = state.currentItems[Number(card.dataset.itemIndex)];
  if (!item) return;
  const action = button.dataset.action;
  if (action === "play") {
    playItem(item);
  } else if (action === "delete-collection") {
    deleteCollectionFromLibrary(item);
  } else if (action === "details") {
    openDetails(item);
  } else if (action === "open") {
    if (itemCanOpen(item)) {
      openChildren(item);
    } else {
      openDetails(item);
    }
  }
});

el.loadMore.addEventListener("click", async () => {
  if (state.libraryLoadingMore) return;
  await loadLibrary({ append: true });
});

el.surpriseMe.addEventListener("click", surpriseMe);
el.scanLibrary.addEventListener("click", scanSelectedLibrary);

el.genreFilter.addEventListener("change", async () => {
  if (!state.selectedLibrary) return;
  const libraryKey = state.selectedLibrary.key;
  if (el.genreFilter.value) {
    state.genreKeysByLibrary[libraryKey] = el.genreFilter.value;
  } else {
    delete state.genreKeysByLibrary[libraryKey];
  }
  persistBrowsePreferences();
  state.stack = [];
  resetLibraryPaging();
  await loadLibrary();
});

el.sort.addEventListener("change", async () => {
  state.sort = el.sort.value;
  persistBrowsePreferences();
  state.stack = [];
  resetLibraryPaging();
  await loadLibrary();
});

for (const button of el.viewButtons) {
  button.addEventListener("click", async () => {
    state.selectedView = button.dataset.view;
    syncBrowseControls();
    updateSurpriseButton();
    persistBrowsePreferences();
    state.stack = [];
    resetLibraryPaging();
    await loadLibrary();
  });
}

el.detailsClose.addEventListener("click", () => el.detailsDialog.close());
el.detailsDialog.addEventListener("close", () => {
  state.detailsItem = null;
});
el.matchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  searchMediaMatches();
});
el.matchClose.addEventListener("click", () => {
  if (!state.matchApplyingGuid) el.matchDialog.close();
});
el.matchDialog.addEventListener("cancel", (event) => {
  if (state.matchApplyingGuid) event.preventDefault();
});
el.matchDialog.addEventListener("close", () => {
  state.matchAbortController?.abort();
  state.matchAbortController = null;
  state.matchRequestId += 1;
  state.matchItem = null;
  state.matchResults = [];
  state.matchSearching = false;
  state.matchApplyingGuid = null;
});
el.mediaDeleteConfirmation.addEventListener("input", updateMediaDeleteSubmit);
el.mediaDeleteForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitMediaDelete();
});
el.mediaDeleteCancel.addEventListener("click", () => el.mediaDeleteDialog.close());
el.mediaDeleteClose.addEventListener("click", () => el.mediaDeleteDialog.close());
el.mediaDeleteDialog.addEventListener("cancel", (event) => {
  if (state.mediaDeleteBusy) event.preventDefault();
});
el.mediaDeleteDialog.addEventListener("close", () => {
  state.mediaDeleteItem = null;
  state.mediaDeletePlan = null;
  state.mediaDeleteBusy = false;
  el.mediaDeleteConfirmation.value = "";
});
el.collectionFilter.addEventListener("input", renderCollectionMembership);
el.collectionCreateForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const title = el.collectionCreateTitle.value.trim();
  if (!title) {
    setCollectionStatus("Enter a name for the new collection.", "error");
    el.collectionCreateTitle.focus();
    return;
  }
  createCollection(title);
});
el.collectionClose.addEventListener("click", () => el.collectionDialog.close());
el.collectionDialog.addEventListener("close", () => {
  state.collectionItem = null;
  state.collectionMembership = null;
  state.collectionUpdating.clear();
  state.collectionCreating = false;
});
el.collectionNameCancel.addEventListener("click", () => el.collectionNameDialog.close());
el.collectionNameForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const title = el.collectionNameInput.value.trim();
  if (!title) {
    el.collectionNameStatus.textContent = "Enter a collection name.";
    el.collectionNameStatus.dataset.kind = "error";
    return;
  }
  renameCollection(state.collectionRenameTarget, title);
});
el.collectionNameDialog.addEventListener("close", () => {
  state.collectionRenameTarget = null;
  el.collectionNameStatus.textContent = "";
});
el.autoplayNext.addEventListener("change", () => {
  state.autoplayNext = el.autoplayNext.checked;
  persistAutoplayNext();
  if (!state.autoplayNext) {
    cancelAutoplayCountdown();
  }
});
el.playerNextEpisode.addEventListener("click", () => {
  const next = state.playerNeighbors?.next;
  playAdjacentEpisode(next).catch((error) => {
    setStatus(`Could not play the next episode: ${error.message}`, "error");
  });
});
el.playerUpNextCancel.addEventListener("click", cancelAutoplayCountdown);
el.playerClose.addEventListener("click", () => el.playerDialog.close());
el.playerDialog.addEventListener("close", async () => {
  state.playerPreparationId += 1;
  const hadPlayer = Boolean(state.playerItem);
  const reloadFilteredView = state.stack.length === 0 && ["continue", "unwatched"].includes(state.selectedView);
  const progressReport = reportPlaybackProgress("stopped", { force: true, keepalive: true });
  cancelAutoplayCountdown();
  state.playerItem = null;
  state.playerNeighbors = null;
  stopProgressReporting();
  stopSavePolling();
  stopActiveHlsSession({ keepalive: true });
  el.player.pause();
  el.player.removeAttribute("src");
  el.playbackMode.hidden = true;
  el.playerError.hidden = true;
  el.playerError.textContent = "";
  el.playerSubtitleSearch.hidden = true;
  el.playerSave.hidden = true;
  el.playerDeleteSave.hidden = true;
  el.playerDeviceSave.hidden = true;
  el.playerDeviceDelete.hidden = true;
  el.playerDownloadOriginal.hidden = true;
  el.autoplayNextLabel.hidden = true;
  el.playerNextEpisode.hidden = true;
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
  const index = Number(el.subtitleSelect.value);
  const item = state.playerItem;
  setActiveSubtitle(index);
  rememberSubtitlePreference(item, index);
  state.subtitleSelectionPromise = state.subtitleSelectionPromise
    .catch(() => {})
    .then(() => persistSubtitleSelection(item, index))
    .catch((error) => {
      setStatus(`Subtitle selected, but Plex could not remember it: ${error.message}`, "error");
    });
});
el.player.addEventListener("play", () => {
  startProgressReporting();
});
el.player.addEventListener("playing", () => {
  el.playerError.hidden = true;
  el.playerError.textContent = "";
  if (state.playerItem) {
    const mode = state.usingDevicePlayback ? "device" : state.usingSavedPlayback ? "saved" : "live";
    setPlaybackMode(state.playerItem, mode);
  }
});
el.player.addEventListener("error", () => {
  if (!state.playerItem || !el.player.currentSrc) return;
  const message = state.playerItem.savedPlayback?.ready
    ? "Playback failed. Select Play saved to retry with the prepared copy."
    : "Playback failed. Select Save to prepare a browser-compatible copy and retry.";
  el.playbackMode.textContent = "Playback failed";
  el.playbackMode.title = message;
  el.playbackMode.hidden = false;
  el.playerError.textContent = message;
  el.playerError.hidden = false;
  setStatus(`Could not play ${displayTitle(state.playerItem)}.`, "error");
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
  scheduleAutoplayNext();
});
window.addEventListener("pagehide", () => {
  reportPlaybackProgress("stopped", { force: true, keepalive: true }).catch(() => {});
  stopActiveHlsSession({ keepalive: true });
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
