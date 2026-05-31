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
  playerItem: null,
  subtitleTrackElements: [],
  savePollTimer: null,
  usingSavedPlayback: false,
  subtitleItem: null,
  subtitleResults: [],
};

const el = {
  login: document.querySelector("#login"),
  app: document.querySelector("#app"),
  loginForm: document.querySelector("#login-form"),
  loginError: document.querySelector("#login-error"),
  password: document.querySelector("#password"),
  libraries: document.querySelector("#libraries"),
  logout: document.querySelector("#logout"),
  serverName: document.querySelector("#server-name"),
  breadcrumbs: document.querySelector("#breadcrumbs"),
  viewTitle: document.querySelector("#view-title"),
  searchForm: document.querySelector("#search-form"),
  searchInput: document.querySelector("#search-input"),
  status: document.querySelector("#status"),
  grid: document.querySelector("#grid"),
  loadMore: document.querySelector("#load-more"),
  sort: document.querySelector("#sort"),
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
  detailsClose: document.querySelector("#details-close"),
  playerDialog: document.querySelector("#player-dialog"),
  playerTitle: document.querySelector("#player-title"),
  playbackMode: document.querySelector("#playback-mode"),
  playerClose: document.querySelector("#player-close"),
  subtitleLabel: document.querySelector("#subtitle-select-label"),
  subtitleSelect: document.querySelector("#subtitle-select"),
  playerSave: document.querySelector("#player-save"),
  playerDeleteSave: document.querySelector("#player-delete-save"),
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
    setStatus("No items found.", "muted");
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
  if (!state.selectedLibrary) return;
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
  } catch (error) {
    if (!append) {
      state.currentItems = [];
      state.libraryTotal = 0;
      el.grid.innerHTML = "";
    }
    setStatus(`Could not load media: ${error.message}`, "error");
  } finally {
    state.libraryLoadingMore = false;
    updateLoadMore();
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
  const meta = [item.contentRating, item.durationText, item.rating ? `${item.rating}/10` : "", item.media?.videoResolution]
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
  el.detailsDialog.showModal();
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

function isSavedPlaybackUrl(item, streamUrl) {
  const savedUrl = savedPlaybackUrlFor(item);
  return Boolean(savedUrl && streamUrl === savedUrl);
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
  return savedPlaybackUrlFor(item) || liveStreamUrlFor(item);
}

function setPlaybackMode(item, usingSaved) {
  if (usingSaved) {
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
  state.usingSavedPlayback = true;
  setPlaybackMode(item, true);
  loadPlayerSource(item, streamUrl, { resumeTime, autoplay });
  updateSaveControls(item);
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
    setPlaybackMode(item, false);
    loadPlayerSource(item, liveUrl, { resumeTime, autoplay });
  }
  updateSaveControls(item);
}

async function playItem(item) {
  item = await hydrateItem(item);
  try {
    await refreshSavedPlayback(item);
  } catch {
    item.savedPlayback = item.savedPlayback || { state: "unavailable", ready: false };
  }
  const streamUrl = streamUrlFor(item);
  if (!streamUrl) return;
  state.playerItem = item;
  state.usingSavedPlayback = isSavedPlaybackUrl(item, streamUrl);
  el.playerTitle.textContent = displayTitle(item);
  setPlaybackMode(item, state.usingSavedPlayback);
  loadPlayerSource(item, streamUrl);
  el.playerSubtitleSearch.hidden = !item.ratingKey;
  el.playerSubtitleSearch.onclick = () => openSubtitleDialog(item);
  updateSaveControls(item);
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

el.searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await search(el.searchInput.value);
});

el.loadMore.addEventListener("click", async () => {
  if (state.libraryLoadingMore) return;
  await loadLibrary({ append: true });
});

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
    state.stack = [];
    resetLibraryPaging();
    await loadLibrary();
  });
}

el.detailsClose.addEventListener("click", () => el.detailsDialog.close());
el.playerClose.addEventListener("click", () => {
  stopSavePolling();
  el.player.pause();
  el.player.removeAttribute("src");
  el.playbackMode.hidden = true;
  el.playerSubtitleSearch.hidden = true;
  el.playerSave.hidden = true;
  el.playerDeleteSave.hidden = true;
  state.usingSavedPlayback = false;
  state.playerItem = null;
  clearSubtitleTracks();
  el.player.load();
  el.playerDialog.close();
});
el.playerDialog.addEventListener("close", () => {
  stopSavePolling();
  el.player.pause();
  el.player.removeAttribute("src");
  el.playbackMode.hidden = true;
  el.playerSubtitleSearch.hidden = true;
  el.playerSave.hidden = true;
  el.playerDeleteSave.hidden = true;
  state.usingSavedPlayback = false;
  state.playerItem = null;
  clearSubtitleTracks();
  el.player.load();
});
el.subtitleSelect.addEventListener("change", () => {
  setActiveSubtitle(Number(el.subtitleSelect.value));
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
el.subtitleClose.addEventListener("click", () => el.subtitleDialog.close());
el.subtitleForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await searchSubtitles();
});

boot().catch((error) => {
  showLogin();
  el.loginError.textContent = error.message;
});
