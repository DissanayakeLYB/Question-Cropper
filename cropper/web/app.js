"use strict";

const MIN_BOX = 20;
const HANDLE_DIRECTIONS = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

const els = {
  paperSelect: document.getElementById("paper-select"),
  pageSelect: document.getElementById("page-select"),
  pageTotal: document.getElementById("page-total"),
  prevPage: document.getElementById("prev-page"),
  nextPage: document.getElementById("next-page"),
  addBox: document.getElementById("add-box"),
  deleteBox: document.getElementById("delete-box"),
  questionInput: document.getElementById("question-input"),
  saveButton: document.getElementById("save-button"),
  saveStatus: document.getElementById("save-status"),
  hint: document.getElementById("hint"),
  stage: document.getElementById("stage"),
  pageImage: document.getElementById("page-image"),
  overlay: document.getElementById("overlay"),
  loading: document.getElementById("loading"),
  loadingText: document.getElementById("loading-text"),
  toast: document.getElementById("toast"),
};

const paperStates = new Map();
let papers = [];
let active = null;
let busy = false;
let saving = false;
let adding = false;
let toastTimer = null;

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
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

function showLoading(text) {
  els.loadingText.textContent = text;
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
  }));
  return {
    data,
    boxes,
    page: 1,
    selectedId: null,
    nextId,
    saveMessage: "",
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

async function openPaper(paperId) {
  const existing = paperStates.get(paperId);
  if (existing) {
    activate(existing);
    return;
  }

  const previousId = active ? active.data.id : null;
  setBusy(true);
  showLoading("Reading PDF…");
  try {
    const data = await api(`/api/papers/${paperId}`);
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
  els.paperSelect.value = String(state.data.id);
  els.saveStatus.textContent = state.saveMessage || "";

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
  if (!active) {
    updateSelectionUi();
    return;
  }

  const page = currentPage();
  for (const box of active.boxes.filter((item) => item.page === active.page)) {
    const element = document.createElement("div");
    element.className = `box${box.id === active.selectedId ? " selected" : ""}`;
    element.dataset.id = String(box.id);
    applyBoxRect(element, box, page);

    const label = document.createElement("span");
    label.className = "box-label";
    label.textContent = `Q${box.question}`;
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
}

function updateControls() {
  const total = active ? active.data.pages.length : 0;
  const locked = busy || saving;
  els.paperSelect.disabled = locked;
  els.pageSelect.disabled = !active || locked;
  els.prevPage.disabled = !active || locked || active.page <= 1;
  els.nextPage.disabled = !active || locked || active.page >= total;
  els.addBox.disabled = !active || busy || saving;
  els.addBox.classList.toggle("active", adding);
  els.saveButton.disabled = !active || busy || saving || active.boxes.length === 0;
  els.saveButton.textContent = saving ? "Saving…" : "Save PNGs";
  els.hint.textContent = adding
    ? "Drag on the page to draw a new box. Click Add box again to cancel."
    : "Click a box to select it. Drag to move, drag a handle to resize.";
  updateSelectionUi();
}

function setAdding(value) {
  adding = value;
  els.overlay.classList.toggle("adding", adding);
  updateControls();
}

function nextQuestionNumber() {
  return active.boxes.reduce((maximum, box) => Math.max(maximum, box.question), 0) + 1;
}

function clearSaveStatus() {
  if (active) {
    active.saveMessage = "";
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
    box.x = Math.round(box.x);
    box.y = Math.round(box.y);
    box.w = Math.round(box.w);
    box.h = Math.round(box.h);
    applyBoxRect(element, box, page);
    clearSaveStatus();
    updateSelectionUi();
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
    box.x = Math.round(box.x);
    box.y = Math.round(box.y);
    box.w = Math.round(box.w);
    box.h = Math.round(box.h);
    applyBoxRect(element, box, page);
    clearSaveStatus();
    updateSelectionUi();
  }

  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);
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
      question: nextQuestionNumber(),
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
  };
}

async function saveAll() {
  if (!active || busy || saving) {
    return;
  }
  if (active.boxes.length === 0) {
    toast("Add at least one box before saving.", true);
    return;
  }

  saving = true;
  updateControls();
  els.saveStatus.textContent = "";
  try {
    const result = await api(`/api/papers/${active.data.id}/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ boxes: active.boxes.map(serializeBox) }),
    });
    active.saveMessage = `Saved ${result.files.length} PNG(s) + ${result.manifest} → ${result.output_dir}`;
    els.saveStatus.textContent = active.saveMessage;
    toast(`Saved ${result.files.length} question PNG(s) to ${result.output_dir}`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    saving = false;
    updateControls();
  }
}

function bindEvents() {
  els.paperSelect.addEventListener("change", () => {
    const paperId = Number.parseInt(els.paperSelect.value, 10);
    if (Number.isFinite(paperId)) {
      openPaper(paperId);
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
  els.questionInput.addEventListener("input", onQuestionInput);
  els.questionInput.addEventListener("change", onQuestionChange);
  els.saveButton.addEventListener("click", saveAll);
  els.overlay.addEventListener("pointerdown", onOverlayPointerDown);
  els.pageImage.addEventListener("error", () => {
    toast("Could not load the page image.", true);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Delete" && event.key !== "Backspace") {
      return;
    }
    const tag = document.activeElement ? document.activeElement.tagName : "";
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
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
    await openPaper(papers[0].id);
  } catch (error) {
    toast(error.message, true);
  }
}

init();
