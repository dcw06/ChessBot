import { api, ApiError, CancelledRequest, cancelRequest, post } from "./api.js";
import { classifyMoveQuality, evaluationPercent } from "./chess-utils.js";
import { createManagedBoard } from "./managed-board.js";

const $ = (id) => document.getElementById(id);
function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Cache is optional. */
  }
}
let board;
let boardObserver;
let boardResizeObserver;
let viewer = new Chess();
let mainMoves = [];
let mainIndex = 0;
let variation = [];
const branches = new Map();
let games = [];
let page = 1;
const pageSize = 8;
let debounce;
let lastAnalysisRequestAt = 0;
const ANALYSIS_DEBOUNCE_MS = 50;
const ANALYSIS_MIN_INTERVAL_MS = 300;
const evaluations = new Map();
let analysisGeneration = 0;
let returnIndex = 0;
let sourcePgn = "";
let analysisModified = false;
let analysisSelected = null;
let analysisDragActive = false;
let suppressStationaryAnalysisClick = false;
let analysisOrientation = "white";
let engineUnavailable = false;

function button(label, className = "button secondary") {
  const element = document.createElement("button");
  element.type = "button";
  element.className = className;
  element.textContent = label;
  return element;
}

function notify(message, kind = "info") {
  window.chessbotUI?.toast(message, kind);
}

function setAnalysisContext(active) {
  $("analysis-context-controls").hidden = !active;
}

function filteredGames() {
  const query = $("game-search").value.trim().toLowerCase();
  const result = $("game-result-filter").value;
  const speed = $("game-speed-filter").value;
  return games.filter(
    (game) =>
      (!query ||
        `${game.opponent} ${game.opening}`.toLowerCase().includes(query)) &&
      (!result || game.result === result) &&
      (!speed || game.time_class === speed),
  );
}

function renderGames() {
  const container = $("recent-games");
  container.replaceChildren();
  const filtered = filteredGames();
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  page = Math.min(page, totalPages);
  $("games-page").textContent = `${page} / ${totalPages}`;
  $("games-prev").disabled = page <= 1;
  $("games-next").disabled = page >= totalPages;
  const slice = filtered.slice((page - 1) * pageSize, page * pageSize);
  if (!slice.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const title = document.createElement("strong");
    title.textContent = games.length
      ? "No matching games"
      : "No recent games available";
    const detail = document.createElement("p");
    detail.textContent = games.length
      ? "Try clearing one of the filters."
      : "Complete a game against Alan Dai to see it here.";
    const start = button("Analyze starting position", "button primary");
    start.addEventListener("click", openStartingPosition);
    const importPgn = button("Import PGN");
    importPgn.addEventListener("click", () => $("import-pgn-btn").click());
    empty.append(title, detail, start, importPgn);
    container.append(empty);
    return;
  }
  slice.forEach((game) => {
    const card = button("", "game-card");
    const badge = document.createElement("span");
    badge.className = `result-badge result-${game.result.toLowerCase()}`;
    badge.textContent = game.result;
    const content = document.createElement("span");
    content.className = "game-card-content";
    const title = document.createElement("strong");
    title.textContent = `vs ${game.opponent}`;
    const meta = document.createElement("span");
    meta.textContent = `${game.opening} · ${game.time_class} · ${game.date}`;
    content.append(title, meta);
    card.append(badge, content);
    card.addEventListener("click", () => openGame(game));
    container.append(card);
  });
}

export async function loadRecentGames(force = false) {
  const cached = storageGet("chessbot:recent-games");
  if (!force && cached) {
    try {
      const parsed = JSON.parse(cached);
      if (parsed.version === 2 && Date.now() - parsed.savedAt < 300000) {
        games = parsed.games.filter((game) => game.source === "local");
        renderGames();
        return;
      }
    } catch {
      try {
        localStorage.removeItem("chessbot:recent-games");
      } catch {
        /* Ignore unavailable storage. */
      }
    }
  }
  const skeleton = document.createElement("div");
  skeleton.className = "skeleton-list";
  skeleton.append(
    document.createElement("span"),
    document.createElement("span"),
    document.createElement("span"),
  );
  $("recent-games").replaceChildren(skeleton);
  try {
    const data = await api("/api/local-games", {
      key: "local-games",
      timeout: 5000,
      retries: 1,
    });
    games = Array.isArray(data.games) ? data.games : [];
    storageSet(
      "chessbot:recent-games",
      JSON.stringify({ version: 2, savedAt: Date.now(), games }),
    );
    renderGames();
  } catch (error) {
    games = [];
    renderGames();
    notify(error.message, "error");
  }
}

function resetTo(index) {
  if (variation.length)
    saveBranch(
      mainIndex,
      variation.map((move) => move.san),
    );
  mainIndex = Math.max(0, Math.min(index, mainMoves.length));
  returnIndex = mainIndex;
  viewer = new Chess();
  for (let i = 0; i < mainIndex; i += 1) viewer.move(mainMoves[i]);
  variation = [];
  analysisModified ||= mainIndex !== 0;
  board.position(viewer.fen(), false);
  renderMoveTree();
  scheduleAnalysis();
}

function renderMoveTree() {
  const list = $("av-move-list");
  list.replaceChildren();
  mainMoves.forEach((move, index) => {
    if (index % 2 === 0) {
      const number = document.createElement("span");
      number.className = "move-number";
      number.textContent = `${Math.floor(index / 2) + 1}.`;
      list.append(number);
    }
    const moveButton = button(
      move.san,
      `tree-move${mainIndex === index + 1 ? " current" : ""}`,
    );
    const score = evaluations.get(index + 1);
    if (score) {
      moveButton.dataset.evaluation = score.label;
      if (score.quality) {
        moveButton.classList.add(`move-${score.quality}`);
        moveButton.setAttribute("aria-label", `${move.san}, ${score.quality}`);
      }
    }
    moveButton.addEventListener("click", () => resetTo(index + 1));
    list.append(moveButton);
  });
  branches.forEach((lines, branchIndex) => {
    lines.forEach((moves) => {
      const branch = button(
        `After move ${branchIndex}: ${moves.join(" ")}`,
        "variation-branch",
      );
      branch.addEventListener("click", () => {
        resetTo(branchIndex);
        playVariation(moves);
      });
      list.append(branch);
    });
  });
}

function saveBranch(index, moves) {
  if (!moves.length) return;
  const lines = branches.get(index) || [];
  const signature = moves.join(" ");
  if (!lines.some((line) => line.join(" ") === signature))
    lines.push([...moves]);
  branches.set(index, lines);
}

function onDrop(from, to) {
  analysisDragActive = false;
  if (from === to) {
    suppressStationaryAnalysisClick = true;
    setTimeout(() => {
      suppressStationaryAnalysisClick = false;
    }, 0);
    return "snapback";
  }
  clearAnalysisSelection();
  const piece = viewer.get(from);
  const isPromotion = piece?.type === "p" && ["1", "8"].includes(to[1]);
  if (isPromotion) {
    window.queueMicrotask(() => makeAnalysisMove(from, to, true));
    return "snapback";
  }
  const move = viewer.move({ from, to, promotion: "q" });
  if (!move) return "snapback";
  recordAnalysisMove(move);
  return undefined;
}

function recordAnalysisMove(move, animateBoard = false) {
  variation.push(move);
  saveBranch(
    mainIndex,
    variation.map((item) => item.san),
  );
  analysisModified = true;
  if (animateBoard) board.position(viewer.fen(), true);
  scheduleAnalysis();
  renderMoveTree();
}

async function makeAnalysisMove(from, to, animateBoard = true) {
  const piece = viewer.get(from);
  let promotion = "q";
  if (piece?.type === "p" && ["1", "8"].includes(to[1])) {
    promotion = await window.chessbotUI.choosePromotion();
    if (!promotion) {
      board.position(viewer.fen(), false);
      return false;
    }
  }
  const move = viewer.move({ from, to, promotion });
  if (!move) return false;
  recordAnalysisMove(move, animateBoard);
  return true;
}

function clearAnalysisSelection() {
  analysisSelected = null;
  document
    .querySelectorAll(
      "#av-board .sq-selected,#av-board .legal-dot,#av-board .legal-ring",
    )
    .forEach((element) => {
      if (element.classList.contains("sq-selected"))
        element.classList.remove("sq-selected");
      else element.remove();
    });
}

function selectAnalysisSquare(square) {
  clearAnalysisSelection();
  analysisSelected = square;
  document
    .querySelector(`#av-board .square-${square}`)
    ?.classList.add("sq-selected");
  viewer.moves({ square, verbose: true }).forEach((move) => {
    const target = document.querySelector(`#av-board .square-${move.to}`);
    if (!target) return;
    const marker = document.createElement("span");
    marker.className = viewer.get(move.to) ? "legal-ring" : "legal-dot";
    marker.setAttribute("aria-hidden", "true");
    target.append(marker);
  });
}

function activateAnalysisSquare(square) {
  const piece = viewer.get(square);
  if (!analysisSelected) {
    if (piece?.color === viewer.turn()) selectAnalysisSquare(square);
    return;
  }
  if (square === analysisSelected) {
    clearAnalysisSelection();
    return;
  }
  if (piece?.color === viewer.turn()) {
    selectAnalysisSquare(square);
    return;
  }
  const from = analysisSelected;
  clearAnalysisSelection();
  makeAnalysisMove(from, square);
}

function makeAnalysisBoardAccessible(focusSquare = "e2") {
  const names = {
    p: "pawn",
    n: "knight",
    b: "bishop",
    r: "rook",
    q: "queen",
    k: "king",
  };
  document
    .querySelectorAll("#av-board [class*='square-']")
    .forEach((element) => {
      const square = element.className.match(/square-([a-h][1-8])/)?.[1];
      if (!square) return;
      const piece = viewer.get(square);
      element.setAttribute("role", "button");
      element.tabIndex = square === focusSquare ? 0 : -1;
      element.onclick = analysisEmptySquareClick;
      element.onkeydown = analysisBoardKeydown;
      element.setAttribute(
        "aria-label",
        piece
          ? `${square}, ${piece.color === "w" ? "white" : "black"} ${names[piece.type]}`
          : `${square}, empty`,
      );
    });
}

function analysisEmptySquareClick(event) {
  if (suppressStationaryAnalysisClick) {
    suppressStationaryAnalysisClick = false;
    return;
  }
  const square =
    event.currentTarget.className.match(/square-([a-h][1-8])/)?.[1];
  if (!square) return;
  activateAnalysisSquare(square);
}

function analysisBoardKeydown(event) {
  event.stopPropagation();
  const current = event.target.closest('[class*="square-"]');
  const square = current?.className.match(/square-([a-h][1-8])/)?.[1];
  if (!square) return;
  if (["Enter", " "].includes(event.key)) {
    event.preventDefault();
    activateAnalysisSquare(square);
    return;
  }
  let delta = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, 1],
    ArrowDown: [0, -1],
  }[event.key];
  if (!delta) return;
  if (analysisOrientation === "black") delta = delta.map((value) => -value);
  event.preventDefault();
  const file = Math.max(0, Math.min(7, square.charCodeAt(0) - 97 + delta[0]));
  const rank = Math.max(1, Math.min(8, Number(square[1]) + delta[1]));
  const next = `${"abcdefgh"[file]}${rank}`;
  makeAnalysisBoardAccessible(next);
  document.querySelector(`#av-board .square-${next}`)?.focus();
}

function initializeBoard(orientation = "white") {
  analysisOrientation = orientation;
  board?.destroy();
  board = createManagedBoard("av-board", {
    position: viewer.fen(),
    orientation,
    draggable: true,
    showNotation: window.chessbotPrefs?.coordinates !== false,
    onDragStart: (source, piece) => {
      if (piece[0] !== viewer.turn()) return false;
      analysisDragActive = true;
      selectAnalysisSquare(source);
      return true;
    },
    onDrop,
    onSnapEnd: () => {
      board.position(viewer.fen());
      makeAnalysisBoardAccessible();
    },
    pieceTheme: "/static/pieces/{piece}.png",
  });
  $("av-board").addEventListener("keydown", analysisBoardKeydown);
  boardObserver?.disconnect();
  boardObserver = new window.MutationObserver(() =>
    makeAnalysisBoardAccessible(),
  );
  boardObserver.observe($("av-board"), { childList: true, subtree: true });
  makeAnalysisBoardAccessible();
  setTimeout(makeAnalysisBoardAccessible, 0);
  boardResizeObserver ??= new window.ResizeObserver((entries) => {
    if (entries.some((entry) => entry.contentRect.width > 0)) board?.resize();
  });
  boardResizeObserver.observe($("av-board-wrap"));
  window.requestAnimationFrame(() =>
    window.requestAnimationFrame(() => board?.resize()),
  );
}

function recoverCancelledAnalysisTouch() {
  if (!analysisDragActive) return;
  analysisDragActive = false;
  const selected = analysisSelected;
  initializeBoard(analysisOrientation);
  if (selected && viewer.get(selected)?.color === viewer.turn())
    selectAnalysisSquare(selected);
}

export function openGame(game) {
  const loader = new Chess();
  if (!loader.load_pgn(game.pgn || "")) {
    notify("That PGN could not be parsed.", "error");
    return;
  }
  mainMoves = loader.history({ verbose: true });
  mainIndex = 0;
  returnIndex = 0;
  variation = [];
  branches.clear();
  evaluations.clear();
  viewer = new Chess();
  sourcePgn = game.pgn || "";
  analysisModified = false;
  $("recent-games-panel").hidden = true;
  $("av-game-view").hidden = false;
  setAnalysisContext(true);
  initializeBoard(game.user_color || "white");
  $("av-game-title").textContent =
    `${game.result || "Analysis"} · ${game.opponent || "Imported game"} · ${game.opening || ""}`;
  const userBlack = game.user_color === "black";
  $("av-top-name").textContent = userBlack
    ? "You"
    : game.opponent || "Opponent";
  $("av-bottom-name").textContent = userBlack
    ? game.opponent || "Opponent"
    : "You";
  $("av-top-sub").textContent = "Black";
  $("av-bottom-sub").textContent = "White";
  renderMoveTree();
  scheduleAnalysis();
}

function scheduleAnalysis() {
  clearTimeout(debounce);
  if (engineUnavailable) {
    renderEngineUnavailable();
    return;
  }
  debounce = setTimeout(analyzePosition, ANALYSIS_DEBOUNCE_MS);
}

async function analyzePosition() {
  const elapsed = performance.now() - lastAnalysisRequestAt;
  if (elapsed < ANALYSIS_MIN_INTERVAL_MS) {
    // Silently coalesce frequent navigation into one request for the final
    // position instead of calling the API or showing an error.
    clearTimeout(debounce);
    debounce = setTimeout(
      analyzePosition,
      Math.ceil(ANALYSIS_MIN_INTERVAL_MS - elapsed),
    );
    return;
  }
  lastAnalysisRequestAt = performance.now();
  const generation = ++analysisGeneration;
  $("analysis-progress").hidden = false;
  try {
    const data = await post(
      "/api/lines",
      {
        fen: viewer.fen(),
        lines: Number($("engine-lines").value),
        time: Number($("engine-time").value),
      },
      { key: "analysis-lines", timeout: 10000 },
    );
    if (generation !== analysisGeneration) return;
    renderLines(data.lines || [], data.depth || 0);
    updateEval(data.eval_cp, data.eval_is_mate, data.eval_mate);
    const previous = evaluations.get(mainIndex - 1);
    const moverWasWhite = mainIndex % 2 === 1;
    const quality = classifyMoveQuality(
      previous?.cp,
      data.eval_cp,
      moverWasWhite,
    );
    evaluations.set(mainIndex, {
      cp: data.eval_cp,
      label: data.eval_is_mate
        ? `M${Math.abs(data.eval_mate)}`
        : `${(data.eval_cp / 100).toFixed(1)}`,
      quality,
    });
    renderMoveTree();
  } catch (error) {
    if (error instanceof ApiError && error.status === 503) {
      engineUnavailable = true;
      renderEngineUnavailable();
      return;
    }
    if (
      !(error instanceof CancelledRequest) &&
      !(error instanceof ApiError && error.status === 429)
    )
      notify(
        error instanceof ApiError && error.status === 0
          ? "You're moving too quickly. Please wait a moment and try again."
          : error.message,
        "error",
      );
  } finally {
    if (generation === analysisGeneration) $("analysis-progress").hidden = true;
  }
}

function renderEngineUnavailable() {
  const panel = $("av-lines-panel");
  panel.replaceChildren();
  const meta = document.createElement("div");
  meta.className = "engine-meta";
  meta.textContent =
    "Stockfish is unavailable. You can still move pieces and explore variations.";
  const retry = button("Retry engine", "button secondary small");
  retry.addEventListener("click", () => {
    engineUnavailable = false;
    scheduleAnalysis();
  });
  panel.append(meta, retry);
  $("av-eval-label").textContent = "Engine unavailable";
}

function renderLines(lines, depth) {
  const panel = $("av-lines-panel");
  panel.replaceChildren();
  const meta = document.createElement("div");
  meta.className = "engine-meta";
  meta.textContent = `Stockfish · depth ${depth || "—"}`;
  panel.append(meta);
  lines.forEach((line) => {
    const row = button("", "engine-line");
    const score = document.createElement("span");
    score.className = `evaluation ${line.cp > 15 ? "positive" : line.cp < -15 ? "negative" : ""}`;
    score.textContent = line.is_mate
      ? `M${Math.abs(line.mate)}`
      : `${line.cp >= 0 ? "+" : ""}${(line.cp / 100).toFixed(2)}`;
    const pv = document.createElement("span");
    pv.textContent = line.pv.join(" ");
    row.append(score, pv);
    row.addEventListener("click", () => playVariation(line.pv));
    panel.append(row);
  });
}

function playVariation(sanMoves) {
  for (const san of sanMoves) {
    const move = viewer.move(san);
    if (!move) break;
    variation.push(move);
  }
  saveBranch(
    mainIndex,
    variation.map((move) => move.san),
  );
  analysisModified = true;
  board.position(viewer.fen(), true);
  renderMoveTree();
  scheduleAnalysis();
}

function updateEval(cp = 0, mate = false, mateIn = null) {
  const percent = evaluationPercent(cp, mate);
  $("av-eval-fill").style.width = `${percent}%`;
  $("av-eval-label").textContent = mate
    ? `M${Math.abs(mateIn)}`
    : `${cp >= 0 ? "+" : ""}${(cp / 100).toFixed(1)}`;
}

function importText(kind) {
  const dialog = $("input-dialog");
  $("input-title").textContent = `Import ${kind.toUpperCase()}`;
  $("input-value").value = "";
  dialog.showModal();
  dialog.addEventListener(
    "close",
    () => {
      if (dialog.returnValue !== "submit") return;
      const text = $("input-value").value.trim();
      if (kind === "fen") {
        const loaded = new Chess();
        if (!loaded.load(text)) {
          notify("Invalid FEN.", "error");
          return;
        }
        mainMoves = [];
        mainIndex = 0;
        returnIndex = 0;
        viewer = loaded;
        variation = [];
        sourcePgn = "";
        analysisModified = true;
        $("recent-games-panel").hidden = true;
        $("av-game-view").hidden = false;
        setAnalysisContext(true);
        initializeBoard();
        $("av-game-title").textContent = "Custom position";
        renderMoveTree();
        scheduleAnalysis();
      } else
        openGame({
          pgn: text,
          user_color: "white",
          opponent: "Imported game",
          opening: "PGN",
          result: "Analysis",
        });
    },
    { once: true },
  );
}

function openStartingPosition() {
  mainMoves = [];
  mainIndex = 0;
  returnIndex = 0;
  viewer = new Chess();
  variation = [];
  branches.clear();
  evaluations.clear();
  sourcePgn = "";
  analysisModified = true;
  $("recent-games-panel").hidden = true;
  $("av-game-view").hidden = false;
  setAnalysisContext(true);
  initializeBoard();
  $("av-game-title").textContent = "Starting position";
  $("av-top-name").textContent = "Black";
  $("av-bottom-name").textContent = "White";
  $("av-top-sub").textContent = "Black";
  $("av-bottom-sub").textContent = "White";
  renderMoveTree();
  scheduleAnalysis();
}

function download(name, contents, type = "text/plain") {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([contents], { type }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

export function initAnalysis() {
  setAnalysisContext(false);
  window.addEventListener("touchcancel", recoverCancelledAnalysisTouch);
  window.addEventListener("chessbot:resize-analysis", () => board?.resize());
  ["game-search", "game-result-filter", "game-speed-filter"].forEach((id) =>
    $(id).addEventListener("input", () => {
      page = 1;
      renderGames();
    }),
  );
  $("games-prev").addEventListener("click", () => {
    page -= 1;
    renderGames();
  });
  $("games-next").addEventListener("click", () => {
    page += 1;
    renderGames();
  });
  $("refresh-games-btn").addEventListener("click", () => loadRecentGames(true));
  $("analysis-back-btn").addEventListener("click", () => {
    cancelRequest("analysis-lines");
    clearTimeout(debounce);
    $("analysis-progress").hidden = true;
    $("av-game-view").hidden = true;
    $("recent-games-panel").hidden = false;
    setAnalysisContext(false);
  });
  document.querySelectorAll("[data-analysis-nav]").forEach((control) =>
    control.addEventListener("click", () => {
      const action = control.dataset.analysisNav;
      resetTo(
        action === "start"
          ? 0
          : action === "end"
            ? mainMoves.length
            : mainIndex + (action === "back" ? -1 : 1),
      );
    }),
  );
  $("av-return-btn").addEventListener("click", () => resetTo(returnIndex));
  $("av-reset-btn").addEventListener("click", () => {
    branches.clear();
    resetTo(0);
  });
  $("engine-lines").addEventListener("change", scheduleAnalysis);
  $("engine-time").addEventListener("change", scheduleAnalysis);
  $("import-pgn-btn").addEventListener("click", () => importText("pgn"));
  $("import-fen-btn").addEventListener("click", () => importText("fen"));
  $("analysis-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (file)
      openGame({
        pgn: await file.text(),
        user_color: "white",
        opponent: file.name,
        opening: "Imported PGN",
        result: "Analysis",
      });
  });
  $("export-pgn-btn").addEventListener("click", () =>
    download(
      "analysis.pgn",
      !analysisModified && sourcePgn ? sourcePgn : viewer.pgn(),
    ),
  );
  $("copy-fen-btn").addEventListener("click", async () => {
    await copyText(viewer.fen(), "FEN");
  });
  $("share-analysis-btn").addEventListener("click", async () => {
    location.hash = `fen=${encodeURIComponent(viewer.fen())}`;
    await copyText(location.href, "Share link");
  });
  $("download-report-btn").addEventListener("click", () =>
    download(
      "chess-analysis.txt",
      `Chess analysis\n\nFEN: ${viewer.fen()}\nPGN: ${viewer.pgn()}\nEvaluation: ${$("av-eval-label").textContent}\n`,
    ),
  );
  if (location.hash.startsWith("#fen=")) {
    try {
      const fen = decodeURIComponent(location.hash.slice(5));
      const loaded = new Chess();
      if (!loaded.load(fen)) throw new Error("Invalid FEN");
      mainMoves = [];
      mainIndex = 0;
      returnIndex = 0;
      viewer = loaded;
      variation = [];
      sourcePgn = "";
      analysisModified = true;
      $("recent-games-panel").hidden = true;
      $("av-game-view").hidden = false;
      setAnalysisContext(true);
      initializeBoard();
      $("av-game-title").textContent = "Shared position";
      renderMoveTree();
      scheduleAnalysis();
    } catch {
      notify("This analysis link contains an invalid position.", "error");
    }
  }
}

export function deactivateAnalysis() {
  clearTimeout(debounce);
  cancelRequest("analysis-lines");
  $("analysis-progress").hidden = true;
}

async function copyText(value, label) {
  try {
    await navigator.clipboard.writeText(value);
    notify(`${label} copied.`, "success");
  } catch {
    const fallback = document.createElement("textarea");
    fallback.value = value;
    fallback.setAttribute("aria-hidden", "true");
    fallback.className = "clipboard-fallback";
    document.body.append(fallback);
    fallback.select();
    const copied = document.execCommand("copy");
    fallback.remove();
    notify(
      copied ? `${label} copied.` : `Unable to copy ${label.toLowerCase()}.`,
      copied ? "success" : "error",
    );
  }
}

export function analysisKeyHandler(event) {
  if (
    !$("analyze-section").hidden &&
    !["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)
  ) {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      resetTo(mainIndex - 1);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      resetTo(mainIndex + 1);
    }
    if (event.key === "Home") resetTo(0);
    if (event.key === "End") resetTo(mainMoves.length);
  }
}
