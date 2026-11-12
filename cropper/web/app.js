"use strict";

const MIN_BOX = 20;
const HANDLE_DIRECTIONS = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

const els = {
  paperSelect: document.getElementById("paper-select"),
  uploadInput: document.getElementById("upload-input"),
  uploadButton: document.getElementById("upload-button"),
  deletePaper: document.getElementById("delete-paper"),
  clearProject: document.getElementById("clear-project"),
  welcomeUpload: document.getElementById("welcome-upload"),
  welcome: document.getElementById("welcome"),
  paperWorkspace: document.getElementById("paper-workspace"),
  results: document.getElementById("results"),
  resultsTitle: document.getElementById("results-title"),
  resultsSummary: document.getElementById("results-summary"),
  resultGrid: document.getElementById("result-grid"),
  manifestDownload: document.getElementById("manifest-download"),
  recropButton: document.getElementById("recrop-button"),
  downloadAll: document.getElementById("download-all"),
  detectionSummary: document.getElementById("detection-summary"),
  expectedInput: document.getElementById("expected-input"),
  existingPaper: document.querySelector(".existing-paper"),
  toolbar: document.getElementById("toolbar"),
  pageSelect: document.getElementById("page-select"),
  pageTotal: document.getElementById("page-total"),
  prevPage: document.getElementById("prev-page"),
  nextPage: document.getElementById("next-page"),
  addBox: document.getElementById("add-box"),
  deleteBox: document.getElementById("delete-box"),
  snapToggle: document.getElementById("snap-toggle"),
  reviewPanel: document.getElementById("review-panel"),
  reviewCount: document.getElementById("review-count"),
  reviewList: document.getElementById("review-list"),
  reviewEmpty: document.getElementById("review-empty"),
  nextFlagged: document.getElementById("next-flagged"),
  questionInput: document.getElementById("question-input"),
  saveButton: document.getElementById("save-button"),
  saveStatus: document.getElementById("save-status"),
  hint: document.getElementById("hint"),
  stage: document.getElementById("stage"),
  pageImage: document.getElementById("page-image"),
  overlay: document.getElementById("overlay"),
  loading: document.getElementById("loading"),
  loadingText: document.getElementById("loading-text"),
  loadingHint: document.getElementById("loading-hint"),
  toast: document.getElementById("toast"),
  themeToggle: document.getElementById("theme-toggle"),
};

const paperStates = new Map();
let papers = [];
let active = null;
let busy = false;
let saving = false;
// Snaps still on the wire. A save waits for them, so it posts the edges that
// are on screen rather than the ones a drag left behind.
const snapsInFlight = new Set();
let adding = false;
let addingQuestion = null;
let toastTimer = null;
// Hand-dragged edges jump to the nearest blank row while this is on.
let snapToText = true;

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

async function api(url, options = {}, timeoutMs = 0) {
  // A request that never settles would leave the Recrop button disabled for
  // good, so a long save can be given up on instead of freezing the page.
  const controller = timeoutMs > 0 ? new AbortController() : null;
  const timer = controller
    ? window.setTimeout(() => controller.abort(), timeoutMs)
    : null;
  let response;
  try {
    response = await fetch(url, controller ? { ...options, signal: controller.signal } : options);
  } finally {
    if (timer !== null) {
      window.clearTimeout(timer);
    }
  }
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null;
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    if (body && body.detail) {
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (Array.isArray(body.detail)) {
        message = body.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
      }
    }
    throw new Error(message);
  }
  return body;
}

function toast(message, isError = false) {
  window.clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.className = `toast${isError ? " error" : ""}`;
  toastTimer = window.setTimeout(() => {
    els.toast.classList.add("hidden");
  }, 7000);
}

function waitForSnaps() {
  if (snapsInFlight.size === 0) {
    return Promise.resolve();
  }
  return Promise.allSettled([...snapsInFlight]);
}

function showLoading(text, hint = "") {
  els.loadingText.textContent = text;
  if (els.loadingHint) {
    els.loadingHint.textContent = hint;
    els.loadingHint.classList.toggle("hidden", !hint);
  }
  els.loading.classList.remove("hidden");
}

function hideLoading() {
  els.loading.classList.add("hidden");
}

function setBusy(value) {
  busy = value;
  if (!busy) {
    hideLoading();
  }
  updateControls();
}

function makeState(data) {
  let nextId = 1;
  const boxes = (data.boxes || []).map((box) => ({
    id: nextId++,
    question: box.question,
    page: box.page,
    x: box.x,
    y: box.y,
    w: box.w,
    h: box.h,
    review: box.review || [],
  }));
  return {
    data,
    boxes,
    page: 1,
    selectedId: null,
    nextId,
    saveMessage: "",
    files: data.files || [],
    manifest: data.manifest || null,
    // Bumped by the server on every save so the browser fetches the new crops.
    revision: data.revision || 0,
    // True once the PNGs on disk came from the reader's boxes rather than the
    // detector's.
    savedFromEdits: (data.revision || 0) > 0,
    // True while the boxes on screen differ from the ones the PNGs came from.
    dirty: false,
  };
}

function currentPage() {
  if (!active) {
    return null;
  }
  return active.data.pages[active.page - 1];
}

function selectedBox() {
  if (!active) {
    return null;
  }
  return active.boxes.find((box) => box.id === active.selectedId) || null;
}

async function openPaper(paperId, expected = expectedValue()) {
  const existing = paperStates.get(paperId);
  if (existing && existing.data.expected === expected) {
    activate(existing);
    return;
  }

  const previousId = active ? active.data.id : null;
  setBusy(true);
  showLoading(
    "Analysing the PDF and finding the questions…",
    "Every page is read for question starts, so a long paper can take a couple of minutes."
  );
  try {
    const data = await api(`/api/papers/${paperId}?expected=${expected}`);
    const state = makeState(data);
    paperStates.set(paperId, state);
    activate(state);
  } catch (error) {
    toast(error.message, true);
    if (previousId !== null) {
      els.paperSelect.value = String(previousId);
    }
  } finally {
    setBusy(false);
  }
}

function activate(state) {
  active = state;
  paperStates.set(state.data.id, state);
  adding = false;
  els.overlay.classList.remove("adding");
  els.welcome.classList.add("hidden");
  els.paperWorkspace.classList.remove("hidden");
  els.toolbar.classList.remove("hidden");
  // The results area holds the recrop and download controls, so it stays on
  // screen even before the first save.
  els.results.classList.remove("hidden");
  els.existingPaper.classList.toggle("hidden", papers.length < 2);
  renderResults();
  els.paperSelect.value = String(state.data.id);
  els.saveStatus.textContent = state.saveMessage || "";
  renderSummary(state);

  els.pageSelect.innerHTML = "";
  for (const page of state.data.pages) {
    const option = document.createElement("option");
    option.value = String(page.number);
    option.textContent = String(page.number);
    els.pageSelect.appendChild(option);
  }
  els.pageTotal.textContent = `/ ${state.data.pages.length}`;

  showPage(state.page);
}

function showPage(pageNumber) {
  if (!active) {
    return;
  }
  const total = active.data.pages.length;
  active.page = clamp(Math.round(pageNumber) || 1, 1, total);
  active.selectedId = null;

  const page = currentPage();
  els.stage.style.aspectRatio = `${page.width} / ${page.height}`;
  if (els.pageImage.dataset.url !== page.url) {
    els.pageImage.dataset.url = page.url;
    els.pageImage.src = page.url;
  }
  els.pageImage.alt = `Page ${page.number}`;
  els.pageSelect.value = String(active.page);
  els.pageTotal.textContent = `/ ${total}`;

  renderBoxes();
  updateControls();
}

function applyBoxRect(element, box, page) {
  element.style.left = `${(box.x / page.width) * 100}%`;
  element.style.top = `${(box.y / page.height) * 100}%`;
  element.style.width = `${(box.w / page.width) * 100}%`;
  element.style.height = `${(box.h / page.height) * 100}%`;
}

function renderBoxes() {
  els.overlay.innerHTML = "";
  renderSummary(active);
  renderReviewList();
  if (!active) {
    updateSelectionUi();
    return;
  }

  const page = currentPage();
  for (const box of active.boxes.filter((item) => item.page === active.page)) {
    const element = document.createElement("div");
    const flagged = (box.review || []).length > 0;
    element.className = `box${box.id === active.selectedId ? " selected" : ""}${
      flagged ? " flagged" : ""
    }`;
    element.dataset.id = String(box.id);
    element.title = flagged ? box.review.join("\n") : "";
    applyBoxRect(element, box, page);

    const label = document.createElement("span");
    label.className = "box-label";
    label.textContent = flagged ? `Q${box.question} !` : `Q${box.question}`;
    element.appendChild(label);

    for (const direction of HANDLE_DIRECTIONS) {
      const handle = document.createElement("span");
      handle.className = `handle h-${direction}`;
      handle.dataset.dir = direction;
      handle.addEventListener("pointerdown", (event) => startResize(event, box, direction, element));
      element.appendChild(handle);
    }

    element.addEventListener("pointerdown", (event) => {
      if (busy || saving || event.button !== 0) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (adding) {
        setAdding(false);
      }
      selectBox(box.id);
      startMove(event, box, element, page);
    });

    els.overlay.appendChild(element);
  }

  updateSelectionUi();
}

function selectBox(boxId) {
  if (!active) {
    return;
  }
  active.selectedId = boxId;
  for (const element of els.overlay.querySelectorAll(".box")) {
    element.classList.toggle("selected", Number(element.dataset.id) === boxId);
  }
  updateSelectionUi();
}

function updateSelectionUi() {
  const box = selectedBox();
  const disabled = !box || busy || saving;
  els.questionInput.disabled = disabled;
  els.deleteBox.disabled = disabled;
  if (box) {
    if (document.activeElement !== els.questionInput) {
      els.questionInput.value = String(box.question);
    }
  } else {
    els.questionInput.value = "";
  }
  for (const item of els.reviewList.querySelectorAll(".review-item")) {
    item.classList.toggle("active", !!box && Number(item.dataset.id) === box.id);
  }
}

function updateControls() {
  const total = active ? active.data.pages.length : 0;
  const locked = busy || saving;
  els.paperSelect.disabled = locked;
  els.uploadButton.disabled = locked;
  els.deletePaper.disabled = !active || locked;
  els.clearProject.disabled = locked;
  // Before a paper is open the header keeps only what makes sense there:
  // choosing a PDF, the question count and the theme. Deleting, clearing and
  // saving appear once a paper is loaded and cropped.
  const paperOnly = !active;
  els.deletePaper.classList.toggle("hidden", paperOnly);
  els.clearProject.classList.toggle("hidden", paperOnly);
  els.saveButton.classList.toggle("hidden", paperOnly);
  els.pageSelect.disabled = !active || locked;
  els.prevPage.disabled = !active || locked || active.page <= 1;
  els.nextPage.disabled = !active || locked || active.page >= total;
  els.addBox.disabled = !active || busy || saving;
  els.addBox.classList.toggle("active", adding);
  els.saveButton.disabled = !active || busy || saving || active.boxes.length === 0;
  els.saveButton.textContent = saving ? "Saving…" : "Save PNGs";
  // Guarded so an old cached page cannot take the whole editor down.
  if (els.recropButton) {
    els.recropButton.disabled = !active || busy || saving || active.boxes.length === 0;
    els.recropButton.textContent = saving ? "Recropping…" : "Recrop from my boxes";
  }
  els.hint.textContent = adding
    ? `Drag on the page to draw Q${addingQuestion ?? "?"}. Click Add box again to cancel.`
    : "Click a box to select it. Drag to move, drag a handle to resize. Shortcuts: ← → pages, Delete removes.";
  updateSelectionUi();
}

function resetWorkspace() {
  active = null;
  paperStates.clear();
  els.welcome.classList.remove("hidden");
  els.paperWorkspace.classList.add("hidden");
  els.toolbar.classList.add("hidden");
  els.results.classList.add("hidden");
  els.reviewPanel.classList.add("hidden");
  els.paperSelect.replaceChildren();
  els.pageSelect.replaceChildren();
  els.pageTotal.textContent = "";
  els.detectionSummary.textContent = "";
  updateControls();
}

async function refreshPapers(selectedId = null) {
  papers = await api("/api/papers");
  els.paperSelect.replaceChildren();
  for (const paper of papers) {
    const option = document.createElement("option");
    option.value = String(paper.id);
    option.textContent = paper.name;
    els.paperSelect.appendChild(option);
  }
  if (!papers.length) {
    resetWorkspace();
    return;
  }
  const paperId = papers.some((paper) => paper.id === selectedId)
    ? selectedId
    : papers[0].id;
  await openPaper(paperId, expectedValue());
}

function setAdding(value) {
  adding = value;
  // A new box fills the first number the paper is missing.
  addingQuestion = adding && active ? nextQuestionNumber() : null;
  els.overlay.classList.toggle("adding", adding);
  updateControls();
}

function formatRanges(numbers) {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  const parts = [];
  let start = sorted[0];
  let previous = sorted[0];
  for (const value of sorted.slice(1)) {
    if (value === previous + 1) {
      previous = value;
      continue;
    }
    parts.push(start === previous ? String(start) : `${start}-${previous}`);
    start = previous = value;
  }
  parts.push(start === previous ? String(start) : `${start}-${previous}`);
  return parts.join(", ");
}

function expectedValue() {
  const parsed = Number.parseInt(els.expectedInput.value, 10);
  if (!Number.isFinite(parsed) || parsed < 1) {
    return 50;
  }
  return Math.min(parsed, 200);
}

function renderSummary(state) {
  if (!active) {
    els.detectionSummary.textContent = "";
    els.detectionSummary.classList.remove("has-gaps", "has-review");
    return;
  }
  const expected = state.data.expected || expectedValue();
  const covered = new Set(active.boxes.map((box) => box.question));
  // Count from the boxes on the page, so adding or deleting one shows up.
  const missing = [];
  for (let number = 1; number <= expected; number += 1) {
    if (!covered.has(number)) {
      missing.push(number);
    }
  }
  const found = Math.max(0, expected - missing.length);
  const flagged = active.boxes.filter((box) => (box.review || []).length > 0).length;
  const parts = [
    missing.length
      ? `Found ${found} of ${expected} · missing ${formatRanges(missing)}`
      : `Found ${found} of ${expected}`,
  ];
  if (flagged) {
    parts.push(`${flagged} to check`);
  }
  els.detectionSummary.textContent = parts.join(" · ");
  els.detectionSummary.classList.toggle("has-gaps", missing.length > 0);
  els.detectionSummary.classList.toggle("has-review", flagged > 0);
}

function outputUrl(filename) {
  // The revision changes whenever the PNGs are recropped, so the browser cannot
  // answer with the previous crop for the same filename.
  return `/outputs/${active.data.id}/${encodeURIComponent(filename)}?v=${active.revision || 0}`;
}

function renderResultsSummary() {
  if (!active) {
    return;
  }
  const files = active.files || [];
  const count = files.length;
  const noun = `question image${count === 1 ? "" : "s"}`;
  let message;
  if (!count) {
    message = "No crops yet. Adjust the boxes, then recrop to write PNGs from them.";
  } else if (active.dirty) {
    message = `${count} ${noun} on disk from the last save. You have changed the boxes since — recrop to crop them from your boxes.`;
  } else if (active.savedFromEdits) {
    message = `${count} ${noun} cropped from the boxes you set, saved to ${active.data.output}`;
  } else {
    message = `${count} ${noun} cropped from the automatic detection, saved to ${active.data.output}`;
  }
  els.resultsSummary.textContent = message;
  els.resultsSummary.classList.toggle("dirty", Boolean(active.dirty) && count > 0);
  if (els.downloadAll) {
    els.downloadAll.classList.toggle("hidden", count === 0);
    if (count) {
      els.downloadAll.href = `/outputs/${active.data.id}/download.zip?v=${active.revision || 0}`;
    }
  }
}

function renderResults() {
  if (!active) {
    return;
  }
  const files = active.files || [];
  els.results.classList.remove("hidden");
  els.resultsTitle.textContent = `Cropped questions · ${active.data.name}`;
  renderResultsSummary();
  els.manifestDownload.classList.toggle("hidden", !active.manifest);
  els.manifestDownload.href = active.manifest ? outputUrl(active.manifest) : "#";
  els.resultGrid.replaceChildren();

  for (const filename of files) {
    const card = document.createElement("article");
    card.className = "result-card";
    const image = document.createElement("img");
    const url = outputUrl(filename);
    image.src = url;
    image.alt = filename;
    image.loading = "lazy";
    const info = document.createElement("div");
    info.className = "result-info";
    const name = document.createElement("span");
    name.textContent = filename;
    const download = document.createElement("a");
    download.className = "download-link";
    download.href = url;
    download.download = filename;
    download.textContent = "Download PNG";
    info.append(name, download);
    card.append(image, info);
    els.resultGrid.appendChild(card);
  }
}

async function uploadFile(file) {
  if (!file || busy || saving) {
    return;
  }
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    toast("Choose a PDF file.", true);
    els.uploadInput.value = "";
    return;
  }

  busy = true;
  showLoading(
    `Uploading and cropping ${file.name}…`,
    "Detecting the questions, then writing one PNG for each of them."
  );
  updateControls();
  try {
    const expected = expectedValue();
    const result = await api(
      `/api/upload?filename=${encodeURIComponent(file.name)}&expected=${expected}`,
      {
      method: "POST",
      headers: { "Content-Type": "application/pdf" },
      body: file,
    });
    const state = makeState(result);
    state.files = result.files || [];
    state.manifest = result.manifest || null;
    paperStates.set(result.id, state);

    const existingOption = [...els.paperSelect.options].find((option) => Number(option.value) === result.id);
    if (!existingOption) {
      const option = document.createElement("option");
      option.value = String(result.id);
      option.textContent = result.name;
      els.paperSelect.appendChild(option);
    }
    if (!papers.some((paper) => paper.id === result.id)) {
      papers.push({ id: result.id, name: result.name, page_count: result.page_count });
    }
    activate(state);
    els.results.classList.remove("hidden");
    renderResults();
    if (state.files.length) {
      toast(`Cropped ${state.files.length} question image(s). Download them below.`);
    } else {
      toast("No question starts were detected. Add boxes below, then save.", true);
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    busy = false;
    hideLoading();
    updateControls();
    els.uploadInput.value = "";
  }
}

function nextQuestionNumber() {
  // The first number the paper is missing, not one past the largest, so a new
  // box fills a hole instead of extending the run.
  const used = new Set(active.boxes.map((box) => box.question));
  const expected = active.data.expected || expectedValue();
  for (let number = 1; number <= expected; number += 1) {
    if (!used.has(number)) {
      return number;
    }
  }
  return Math.max(0, ...used) + 1;
}

function flaggedBoxes() {
  if (!active) {
    return [];
  }
  return active.boxes
    .filter((box) => (box.review || []).length > 0)
    .sort((a, b) => a.page - b.page || a.y - b.y || a.x - b.x);
}

function renderReviewList() {
  if (!active) {
    els.reviewPanel.classList.add("hidden");
    return;
  }
  const flagged = flaggedBoxes();
  els.reviewPanel.classList.remove("hidden");
  els.reviewCount.textContent = String(flagged.length);
  els.reviewCount.classList.toggle("zero", flagged.length === 0);
  els.nextFlagged.disabled = flagged.length === 0;
  els.reviewEmpty.classList.toggle("hidden", flagged.length > 0);
  els.reviewList.replaceChildren();

  for (const box of flagged) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = `review-item${box.id === active.selectedId ? " active" : ""}`;
    button.dataset.id = String(box.id);

    const title = document.createElement("strong");
    title.textContent = `Q${box.question} · page ${box.page}`;
    const reasons = document.createElement("span");
    reasons.textContent = box.review.join("; ");
    button.append(title, reasons);
    button.addEventListener("click", () => focusBox(box));
    item.appendChild(button);
    els.reviewList.appendChild(item);
  }
}

function focusBox(box) {
  if (!active || !box) {
    return;
  }
  if (box.page !== active.page) {
    showPage(box.page);
  }
  selectBox(box.id);
  renderReviewList();
  const element = els.overlay.querySelector(`.box[data-id="${box.id}"]`);
  if (element) {
    element.scrollIntoView({ block: "center", inline: "nearest" });
    element.classList.add("flash");
    window.setTimeout(() => element.classList.remove("flash"), 900);
  }
}

function nextFlagged() {
  if (!active) {
    return;
  }
  const flagged = flaggedBoxes();
  if (!flagged.length) {
    toast("No box needs review.");
    return;
  }
  const current = selectedBox();
  if (!current) {
    focusBox(flagged[0]);
    return;
  }
  const here = flagged.findIndex((box) => box.id === current.id);
  if (here >= 0) {
    focusBox(flagged[(here + 1) % flagged.length]);
    return;
  }
  const next = flagged.find(
    (box) => box.page > current.page || (box.page === current.page && box.y > current.y)
  );
  focusBox(next || flagged[0]);
}

async function snapBox(box, element) {
  if (!active || !box || !snapToText) {
    return;
  }
  const page = active.data.pages[box.page - 1];
  box.snapToken = (box.snapToken || 0) + 1;
  const token = box.snapToken;
  try {
    const request = api(`/api/papers/${active.data.id}/snap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        page: box.page,
        x: clamp(Math.round(box.x), 0, page.width - 2),
        y: clamp(Math.round(box.y), 0, page.height - 2),
        w: Math.max(2, Math.round(box.w)),
        h: Math.max(2, Math.round(box.h)),
      }),
    });
    snapsInFlight.add(request);
    let snapped;
    try {
      snapped = await request;
    } finally {
      snapsInFlight.delete(request);
    }
    if (!active || box.snapToken !== token || box.page !== active.page) {
      return;
    }
    box.x = snapped.x;
    box.y = snapped.y;
    box.w = snapped.w;
    box.h = snapped.h;
    const node = element || els.overlay.querySelector(`.box[data-id="${box.id}"]`);
    if (node) {
      applyBoxRect(node, box, page);
    }
  } catch (error) {
    toast(error.message, true);
  }
}

function clearSaveStatus() {
  if (active) {
    active.saveMessage = "";
    // The boxes no longer match the PNGs that were written from them.
    active.dirty = true;
    renderResultsSummary();
  }
  els.saveStatus.textContent = "";
}

function toPageCoords(event) {
  const page = currentPage();
  const rect = els.pageImage.getBoundingClientRect();
  const width = Math.max(1, rect.width);
  const height = Math.max(1, rect.height);
  return {
    x: clamp(((event.clientX - rect.left) * page.width) / width, 0, page.width),
    y: clamp(((event.clientY - rect.top) * page.height) / height, 0, page.height),
  };
}

function clearReview(box, element) {
  if (!box || !(box.review || []).length) {
    return;
  }
  // The reader has just moved the edges by hand, so the old reasons are stale.
  box.review = [];
  renderReviewList();
  const node = element || els.overlay.querySelector(`.box[data-id="${box.id}"]`);
  if (node) {
    node.classList.remove("flagged");
    node.title = "";
    const label = node.querySelector(".box-label");
    if (label) {
      label.textContent = `Q${box.question}`;
    }
  }
  renderSummary(active);
}

function startMove(event, box, element, page) {
  const start = toPageCoords(event);
  const original = { x: box.x, y: box.y };

  function move(moveEvent) {
    const point = toPageCoords(moveEvent);
    box.x = clamp(original.x + point.x - start.x, 0, page.width - box.w);
    box.y = clamp(original.y + point.y - start.y, 0, page.height - box.h);
    applyBoxRect(element, box, page);
  }

  function finish() {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
    fitBoxToPage(box, page);
    applyBoxRect(element, box, page);
    clearReview(box, element);
    clearSaveStatus();
    updateSelectionUi();
    snapBox(box, element);
  }

  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);
}

function startResize(event, box, direction, element) {
  event.preventDefault();
  event.stopPropagation();
  if (busy || saving || event.button !== 0) {
    return;
  }
  selectBox(box.id);

  const page = currentPage();
  const start = toPageCoords(event);
  const original = {
    x0: box.x,
    y0: box.y,
    x1: box.x + box.w,
    y1: box.y + box.h,
  };

  function move(moveEvent) {
    const point = toPageCoords(moveEvent);
    const dx = point.x - start.x;
    const dy = point.y - start.y;
    let { x0, y0, x1, y1 } = original;

    if (direction.includes("w")) {
      x0 = clamp(original.x0 + dx, 0, original.x1 - MIN_BOX);
    }
    if (direction.includes("e")) {
      x1 = clamp(original.x1 + dx, original.x0 + MIN_BOX, page.width);
    }
    if (direction.includes("n")) {
      y0 = clamp(original.y0 + dy, 0, original.y1 - MIN_BOX);
    }
    if (direction.includes("s")) {
      y1 = clamp(original.y1 + dy, original.y0 + MIN_BOX, page.height);
    }

    box.x = x0;
    box.y = y0;
    box.w = x1 - x0;
    box.h = y1 - y0;
    applyBoxRect(element, box, page);
  }

  function finish() {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
    fitBoxToPage(box, page);
    applyBoxRect(element, box, page);
    clearReview(box, element);
    clearSaveStatus();
    updateSelectionUi();
    snapBox(box, element);
  }

  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);
}

function fitBoxToPage(box, page) {
  // Round to whole points, then clamp, so x + w can never poke a pixel past the
  // page edge and get the save rejected.
  const width = Math.floor(page.width);
  const height = Math.floor(page.height);
  box.w = clamp(Math.round(box.w), MIN_BOX, width);
  box.h = clamp(Math.round(box.h), MIN_BOX, height);
  box.x = clamp(Math.round(box.x), 0, width - box.w);
  box.y = clamp(Math.round(box.y), 0, height - box.h);
}

function drawDefaultBox(page, start) {
  const width = Math.min(600, Math.round(page.width * 0.5));
  const height = Math.min(250, Math.round(page.height * 0.12));
  const x = clamp(Math.round(start.x - width / 2), 0, page.width - width);
  const y = clamp(Math.round(start.y - height / 2), 0, page.height - height);
  return { x, y, w: width, h: height };
}

function onOverlayPointerDown(event) {
  if (!active || busy || saving || event.button !== 0) {
    return;
  }
  if (!adding) {
    if (event.target === els.overlay) {
      selectBox(null);
    }
    return;
  }

  event.preventDefault();
  const page = currentPage();
  const start = toPageCoords(event);
  let last = start;

  const ghost = document.createElement("div");
  ghost.className = "drawing";
  els.overlay.appendChild(ghost);

  function drawGhost(x0, y0, x1, y1) {
    ghost.style.left = `${(Math.min(x0, x1) / page.width) * 100}%`;
    ghost.style.top = `${(Math.min(y0, y1) / page.height) * 100}%`;
    ghost.style.width = `${(Math.abs(x1 - x0) / page.width) * 100}%`;
    ghost.style.height = `${(Math.abs(y1 - y0) / page.height) * 100}%`;
  }

  drawGhost(start.x, start.y, start.x, start.y);

  function move(moveEvent) {
    last = toPageCoords(moveEvent);
    drawGhost(start.x, start.y, last.x, last.y);
  }

  function finish(finishEvent) {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
    if (finishEvent && typeof finishEvent.clientX === "number") {
      last = toPageCoords(finishEvent);
    }
    ghost.remove();

    let x = Math.min(start.x, last.x);
    let y = Math.min(start.y, last.y);
    let width = Math.abs(last.x - start.x);
    let height = Math.abs(last.y - start.y);

    if (width < MIN_BOX || height < MIN_BOX) {
      const fallback = drawDefaultBox(page, start);
      x = fallback.x;
      y = fallback.y;
      width = fallback.w;
      height = fallback.h;
    } else {
      x = Math.round(x);
      y = Math.round(y);
      width = Math.round(width);
      height = Math.round(height);
    }

    const box = {
      id: active.nextId++,
      question: addingQuestion ?? nextQuestionNumber(),
      page: active.page,
      x,
      y,
      w: width,
      h: height,
    };
    active.boxes.push(box);
    active.selectedId = box.id;
    setAdding(false);
    clearSaveStatus();
    renderBoxes();
    updateControls();
  }

  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);
}

function onQuestionInput() {
  const box = selectedBox();
  if (!box || busy || saving) {
    return;
  }
  const value = Number.parseInt(els.questionInput.value, 10);
  if (!Number.isFinite(value) || value < 1) {
    return;
  }
  box.question = value;
  const label = els.overlay.querySelector(`.box[data-id="${box.id}"] .box-label`);
  if (label) {
    label.textContent = `Q${value}`;
  }
  clearSaveStatus();
  renderSummary(active);
}

function onQuestionChange() {
  const box = selectedBox();
  if (!box || busy || saving) {
    return;
  }
  let value = Number.parseInt(els.questionInput.value, 10);
  if (!Number.isFinite(value) || value < 1) {
    value = 1;
  }
  box.question = value;
  els.questionInput.value = String(value);
  const label = els.overlay.querySelector(`.box[data-id="${box.id}"] .box-label`);
  if (label) {
    label.textContent = `Q${value}`;
  }
  clearSaveStatus();
  renderSummary(active);
}

function deleteSelected() {
  const box = selectedBox();
  if (!box || !active || busy || saving) {
    return;
  }
  active.boxes = active.boxes.filter((item) => item.id !== box.id);
  active.selectedId = null;
  clearSaveStatus();
  renderBoxes();
  updateControls();
}

async function deletePaper() {
  if (!active || busy || saving) {
    return;
  }
  const deletedName = active.data.name;
  if (!window.confirm(`Delete "${deletedName}" and all of its renders, boxes, and PNGs?`)) {
    return;
  }
  busy = true;
  showLoading("Deleting PDF…");
  updateControls();
  try {
    await api(`/api/papers/${active.data.id}`, { method: "DELETE" });
    await refreshPapers();
    toast(`Deleted ${deletedName}.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    busy = false;
    hideLoading();
    updateControls();
  }
}

async function clearProject() {
  if (busy || saving) {
    return;
  }
  if (!window.confirm("Clear the project and delete all PDFs, renders, boxes, and PNGs from this session?")) {
    return;
  }
  busy = true;
  showLoading("Clearing project…");
  updateControls();
  try {
    await api("/api/clear", { method: "POST" });
    await refreshPapers();
    toast("Project cleared.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    busy = false;
    hideLoading();
    updateControls();
  }
}

function serializeBox(box) {
  const page = active.data.pages[box.page - 1];
  const x0 = clamp(Math.round(box.x), 0, Math.max(0, page.width - 2));
  const y0 = clamp(Math.round(box.y), 0, Math.max(0, page.height - 2));
  const x1 = clamp(Math.round(box.x + box.w), x0 + 2, page.width);
  const y1 = clamp(Math.round(box.y + box.h), y0 + 2, page.height);
  return {
    page: box.page,
    x: x0,
    y: y0,
    w: x1 - x0,
    h: y1 - y0,
    question: Math.max(1, Math.round(box.question)),
    review: box.review || [],
  };
}

// Recropping a long paper writes tens of megabytes of PNGs. Give up rather
// than leave the button disabled forever when the server never answers.
const RECROP_TIMEOUT_MS = 240000;

async function saveAll() {
  if (!active) {
    return;
  }
  // Returning silently here is what made the button look frozen: a click while
  // a recrop was running did nothing at all.
  if (saving) {
    toast("Still recropping the boxes you changed. The crops refresh when it finishes.");
    return;
  }
  if (busy) {
    toast("Wait for the current job to finish, then recrop.");
    return;
  }
  if (active.boxes.length === 0) {
    toast("Add at least one box before saving.", true);
    return;
  }

  saving = true;
  const count = active.boxes.length;
  const boxes = `box${count === 1 ? "" : "es"}`;
  showLoading(
    `Recropping ${count} ${boxes}…`,
    "Writing the PNGs from the boxes you set. A long paper takes a minute or two."
  );
  updateControls();
  els.saveStatus.textContent = "";
  toast(`Recropping ${count} ${boxes} from your edges — this can take a minute on a long paper.`);
  try {
    // Post the snapped edges, not the ones a drag left in flight.
    await waitForSnaps();
    const result = await api(
      `/api/papers/${active.data.id}/save`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ boxes: active.boxes.map(serializeBox) }),
      },
      RECROP_TIMEOUT_MS
    );
    active.files = result.files;
    active.manifest = result.manifest;
    active.data.output = result.output_dir;
    active.revision = result.revision || (active.revision || 0) + 1;
    active.savedFromEdits = true;
    active.dirty = false;
    active.saveMessage = `Recropped ${result.files.length} PNG(s) from your boxes`;
    els.saveStatus.textContent = active.saveMessage;
    renderResults();
    els.results.scrollIntoView({ behavior: "smooth", block: "nearest" });
    toast(`Recropped ${result.files.length} question PNG(s) from your boxes. Download them above.`);
  } catch (error) {
    if (error && error.name === "AbortError") {
      toast(
        "The recrop did not finish in time. Check the server window, then try again.",
        true
      );
    } else {
      toast(error.message, true);
    }
  } finally {
    saving = false;
    hideLoading();
    updateControls();
  }
}

const THEME_KEY = "question-cropper-theme";

function themeSetting() {
  // Dark is the default; a saved choice only ever overrides it with light.
  try {
    return window.localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  } catch (error) {
    return "dark";
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const dark = theme === "dark";
  if (els.themeToggle) {
    els.themeToggle.textContent = dark ? "Light mode" : "Dark mode";
    els.themeToggle.title = `Switch to ${dark ? "light" : "dark"} mode`;
    els.themeToggle.setAttribute("aria-pressed", String(dark));
  }
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  try {
    window.localStorage.setItem(THEME_KEY, next);
  } catch (error) {
    // Blocked storage only costs the preference, not the toggle itself.
  }
  applyTheme(next);
}

function bindEvents() {
  const openFilePicker = () => els.uploadInput.click();
  if (els.themeToggle) {
    els.themeToggle.addEventListener("click", toggleTheme);
  }
  els.uploadButton.addEventListener("click", openFilePicker);
  els.deletePaper.addEventListener("click", deletePaper);
  els.clearProject.addEventListener("click", clearProject);
  els.welcomeUpload.addEventListener("click", openFilePicker);
  els.uploadInput.addEventListener("change", () => {
    const file = els.uploadInput.files && els.uploadInput.files[0];
    if (file) {
      uploadFile(file);
    }
  });
  els.paperSelect.addEventListener("change", () => {
    const paperId = Number.parseInt(els.paperSelect.value, 10);
    if (Number.isFinite(paperId)) {
      openPaper(paperId, expectedValue());
    }
  });
  els.expectedInput.addEventListener("change", () => {
    const expected = expectedValue();
    els.expectedInput.value = String(expected);
    if (active) {
      paperStates.delete(active.data.id);
      openPaper(active.data.id, expected);
    }
  });
  els.pageSelect.addEventListener("change", () => {
    showPage(Number.parseInt(els.pageSelect.value, 10));
  });
  els.prevPage.addEventListener("click", () => {
    if (active) {
      showPage(active.page - 1);
    }
  });
  els.nextPage.addEventListener("click", () => {
    if (active) {
      showPage(active.page + 1);
    }
  });
  els.addBox.addEventListener("click", () => {
    if (busy || saving) {
      return;
    }
    setAdding(!adding);
  });
  els.deleteBox.addEventListener("click", deleteSelected);
  els.nextFlagged.addEventListener("click", nextFlagged);
  els.snapToggle.addEventListener("click", () => {
    snapToText = !snapToText;
    els.snapToggle.setAttribute("aria-pressed", snapToText ? "true" : "false");
    toast(snapToText ? "Snap to text is on." : "Snap to text is off.");
  });
  els.questionInput.addEventListener("input", onQuestionInput);
  els.questionInput.addEventListener("change", onQuestionChange);
  els.saveButton.addEventListener("click", saveAll);
  if (els.recropButton) {
    els.recropButton.addEventListener("click", saveAll);
  }
  els.overlay.addEventListener("pointerdown", onOverlayPointerDown);
  els.pageImage.addEventListener("error", () => {
    toast("Could not load the page image.", true);
  });

  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement ? document.activeElement.tagName : "";
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
      return;
    }
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      if (!active || busy || saving) {
        return;
      }
      event.preventDefault();
      showPage(active.page + (event.key === "ArrowRight" ? 1 : -1));
      return;
    }
    if (event.key !== "Delete" && event.key !== "Backspace") {
      return;
    }
    if (!selectedBox()) {
      return;
    }
    event.preventDefault();
    deleteSelected();
  });
}

async function init() {
  applyTheme(themeSetting());
  bindEvents();
  updateControls();
  try {
    papers = await api("/api/papers");
    if (!papers.length) {
      toast("No PDFs loaded.", true);
      return;
    }
    for (const paper of papers) {
      const option = document.createElement("option");
      option.value = String(paper.id);
      option.textContent = paper.name;
      els.paperSelect.appendChild(option);
    }
    await openPaper(papers[0].id, expectedValue());
  } catch (error) {
    toast(error.message, true);
  }
}

init();
