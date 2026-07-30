import { api, CancelledRequest, post } from "./api.js";
import { playSound, setSoundEnabled, soundEnabled } from "./sounds.js";
import { classifyResult, formatClock } from "./chess-utils.js";

const $ = (id) => document.getElementById(id);
function stored(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function store(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Preferences remain in memory. */
  }
}
const prefs = {
  theme: stored("chessbot:theme", "dark"),
  coordinates: stored("chessbot:coordinates", "on") !== "off",
  boardTheme: stored("chessbot:board-theme", "classic"),
  pieceTheme: stored("chessbot:piece-theme", "wikipedia"),
  highContrast: stored("chessbot:contrast", "normal") === "high",
};
window.chessbotPrefs = prefs;

let board;
let boardObserver;
let game = new Chess();
let botColor = "black";
let humanColor = "white";
let serverVersion = 0;
let pollTimer;
let clockTimer;
let pollFailures = 0;
let gameOver = true;
let requestBusy = false;
let selectedSquare = null;
let lastState = null;
let lastMoveCount = 0;
let lastEvaluatedVersion = -1;
let clocks = { bot: 180, human: 180, at: performance.now(), turn: "white" };
const clockAnnouncements = { top: new Set(), bottom: new Set() };
let analysisModule;

async function getAnalysis() {
  if (!analysisModule) {
    analysisModule = await import("./analysis.js");
    analysisModule.initAnalysis();
  }
  return analysisModule;
}

function toast(message, kind = "info") {
  const element = document.createElement("div");
  element.className = `toast ${kind}`;
  element.textContent = message;
  $("toast-region").append(element);
  setTimeout(() => element.remove(), 4500);
}
function announce(message) {
  $("sr-announcer").textContent = "";
  requestAnimationFrame(() => {
    $("sr-announcer").textContent = message;
  });
}
window.chessbotUI = { toast, announce };

function setConnected(connected, message = "") {
  $("connection-dot").classList.toggle("online", connected);
  $("connection-dot").classList.toggle("offline", !connected);
  $("connection-label").textContent = connected ? "Online" : "Offline";
  $("connection-banner").hidden = connected;
  if (!connected)
    $("connection-message").textContent =
      message || "Connection lost. Your board is preserved.";
}

async function refreshHealth() {
  try {
    const data = await api("/health/ready", { timeout: 5000 });
    $("model-chip").textContent = `Model ${data.model}`;
    $("model-chip").className =
      `status-chip ${data.model === "ready" ? "ready" : "error"}`;
    $("stockfish-chip").textContent = `Stockfish ${data.stockfish}`;
    $("stockfish-chip").className =
      `status-chip ${data.stockfish === "ready" ? "ready" : "error"}`;
    setConnected(true);
  } catch (error) {
    const data = error.data || {};
    $("model-chip").textContent = `Model ${data.model || "unavailable"}`;
    $("model-chip").className = "status-chip error";
    $("stockfish-chip").textContent =
      `Stockfish ${data.stockfish || "unavailable"}`;
    $("stockfish-chip").className = "status-chip error";
    setConnected(
      false,
      error.status === 503
        ? "Services are temporarily unavailable. Retry shortly."
        : error.message,
    );
    if (error.status === 503) $("connection-label").textContent = "Maintenance";
  }
}

function setBusy(busy, text = "Working…") {
  requestBusy = busy;
  document.body.classList.toggle("request-busy", busy);
  $("start-btn").disabled = busy;
  document.body.dataset.busyLabel = busy ? text : "";
  updateInteraction();
}

function updateInteraction() {
  const humanTurn = !gameOver && game.turn() === humanColor[0] && !requestBusy;
  $("board-wrap").classList.toggle("board-disabled", !humanTurn);
  $("board").setAttribute("aria-disabled", String(!humanTurn));
  ["abort-btn", "resign-btn", "undo-btn"].forEach((id) => {
    if (!gameOver) $(id).disabled = requestBusy;
  });
}

function confirmAction(title, message, label = "Confirm") {
  const dialog = $("confirm-dialog");
  $("confirm-title").textContent = title;
  $("confirm-message").textContent = message;
  $("confirm-accept").textContent = label;
  dialog.showModal();
  return new Promise((resolve) =>
    dialog.addEventListener(
      "close",
      () => resolve(dialog.returnValue === "confirm"),
      { once: true },
    ),
  );
}

function choosePromotion() {
  const dialog = $("promotion-dialog");
  dialog.showModal();
  return new Promise((resolve) =>
    dialog.addEventListener(
      "close",
      () =>
        resolve(
          ["q", "r", "b", "n"].includes(dialog.returnValue)
            ? dialog.returnValue
            : null,
        ),
      { once: true },
    ),
  );
}

async function makeMove(from, to) {
  if (requestBusy || gameOver || game.turn() !== humanColor[0]) return false;
  const piece = game.get(from);
  let promotion = "q";
  if (piece?.type === "p" && ["1", "8"].includes(to[1])) {
    promotion = await choosePromotion();
    if (!promotion) return false;
  }
  const move = game.move({ from, to, promotion });
  if (!move) return false;
  board.position(game.fen(), true);
  clearSelection();
  setBusy(true, "Submitting your move…");
  try {
    const data = await post(
      "/move",
      {
        uci: move.from + move.to + (move.promotion || ""),
        expected_version: serverVersion,
      },
      { timeout: 10000 },
    );
    handleState(data, move);
    if (!data.over && data.turn === botColor) await requestBotMove();
  } catch (error) {
    game.undo();
    board.position(game.fen(), false);
    showError(error);
  } finally {
    if (!lastState?.bot_busy) setBusy(false);
  }
  return true;
}

function onDragStart(source, piece) {
  if (
    gameOver ||
    requestBusy ||
    game.turn() !== humanColor[0] ||
    piece[0] !== humanColor[0]
  )
    return false;
  selectSquare(source);
  return true;
}
function onDrop(from, to) {
  clearLegalMoves();
  makeMove(from, to);
  return "snapback";
}
function onSnapEnd() {
  board.position(game.fen(), false);
  applyHighlights();
}

function boardClick(event) {
  if (gameOver || requestBusy || game.turn() !== humanColor[0]) return;
  const square = event.target
    .closest('[class*="square-"]')
    ?.className.match(/square-([a-h][1-8])/)?.[1];
  if (!square) return clearSelection();
  activateSquare(square);
}

function activateSquare(square) {
  const piece = game.get(square);
  if (selectedSquare) {
    if (square === selectedSquare) return clearSelection();
    if (piece?.color === humanColor[0]) return selectSquare(square);
    const from = selectedSquare;
    clearSelection();
    makeMove(from, square);
  } else if (piece?.color === humanColor[0]) selectSquare(square);
}

function makeBoardAccessible(
  focusSquare = humanColor === "white" ? "e2" : "e7",
) {
  const pieceNames = {
    p: "pawn",
    n: "knight",
    b: "bishop",
    r: "rook",
    q: "queen",
    k: "king",
  };
  document.querySelectorAll("#board [class*='square-']").forEach((square) => {
    const coordinate = square.className.match(/square-([a-h][1-8])/)?.[1];
    if (!coordinate) return;
    const piece = game.get(coordinate);
    square.setAttribute("role", "button");
    square.setAttribute("tabindex", coordinate === focusSquare ? "0" : "-1");
    square.onkeydown = boardKeydown;
    square.setAttribute(
      "aria-label",
      piece
        ? `${coordinate}, ${piece.color === "w" ? "white" : "black"} ${pieceNames[piece.type]}`
        : `${coordinate}, empty`,
    );
  });
}

function boardKeydown(event) {
  event.stopPropagation();
  const current = event.target.closest('[class*="square-"]');
  const coordinate = current?.className.match(/square-([a-h][1-8])/)?.[1];
  if (!coordinate) return;
  if (["Enter", " "].includes(event.key)) {
    event.preventDefault();
    activateSquare(coordinate);
    return;
  }
  const delta = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, 1],
    ArrowDown: [0, -1],
  }[event.key];
  if (!delta) return;
  event.preventDefault();
  const file = Math.max(
    0,
    Math.min(7, coordinate.charCodeAt(0) - 97 + delta[0]),
  );
  const rank = Math.max(1, Math.min(8, Number(coordinate[1]) + delta[1]));
  const next = `${"abcdefgh"[file]}${rank}`;
  makeBoardAccessible(next);
  document.querySelector(`#board .square-${next}`)?.focus();
}

function selectSquare(square) {
  clearSelection();
  selectedSquare = square;
  document
    .querySelector(`#board .square-${square}`)
    ?.classList.add("sq-selected");
  game.moves({ square, verbose: true }).forEach((move) => {
    const target = document.querySelector(`#board .square-${move.to}`);
    if (!target) return;
    const marker = document.createElement("span");
    marker.className = game.get(move.to) ? "legal-ring" : "legal-dot";
    marker.setAttribute("aria-hidden", "true");
    target.append(marker);
  });
}
function clearLegalMoves() {
  document
    .querySelectorAll("#board .legal-dot,#board .legal-ring")
    .forEach((node) => node.remove());
}
function clearSelection() {
  clearLegalMoves();
  document
    .querySelectorAll("#board .sq-selected")
    .forEach((node) => node.classList.remove("sq-selected"));
  selectedSquare = null;
}

function applyHighlights() {
  document
    .querySelectorAll("#board .sq-last,#board .sq-check")
    .forEach((node) => node.classList.remove("sq-last", "sq-check"));
  if (lastState?.last_move)
    lastState.last_move.forEach((index) =>
      document
        .querySelector(
          `#board .square-${"abcdefgh"[index % 8]}${Math.floor(index / 8) + 1}`,
        )
        ?.classList.add("sq-last"),
    );
  if (lastState?.in_check) {
    game
      .board()
      .flat()
      .filter(Boolean)
      .filter((piece) => piece.type === "k" && piece.color === game.turn())
      .forEach(() => {
        for (const square of Array.from(
          { length: 64 },
          (_, index) => `${"abcdefgh"[index % 8]}${Math.floor(index / 8) + 1}`,
        ))
          if (
            game.get(square)?.type === "k" &&
            game.get(square)?.color === game.turn()
          )
            document
              .querySelector(`#board .square-${square}`)
              ?.classList.add("sq-check");
      });
  }
}

function initializeBoard(state) {
  game = new Chess(state.fen);
  board?.destroy();
  board = Chessboard("board", {
    position: state.fen,
    orientation: humanColor,
    draggable: true,
    showNotation: prefs.coordinates,
    onDragStart,
    onDrop,
    onSnapEnd,
    onMoveEnd: () => {
      applyHighlights();
      makeBoardAccessible();
    },
    pieceTheme: "/static/pieces/{piece}.png",
  });
  $("board").addEventListener("click", boardClick, true);
  $("board").addEventListener("keydown", boardKeydown);
  boardObserver?.disconnect();
  boardObserver = new window.MutationObserver(() => makeBoardAccessible());
  boardObserver.observe($("board"), { childList: true, subtree: true });
  makeBoardAccessible();
  setTimeout(makeBoardAccessible, 0);
  resizeBoards();
}

async function requestBotMove() {
  if (requestBusy && lastState?.bot_busy) return;
  setBusy(true, "Searching candidate moves…");
  $("status").textContent = "Alan Dai is thinking";
  announce("Alan Dai is thinking.");
  try {
    handleState(
      await post(
        "/bot_move",
        { expected_version: serverVersion },
        { key: "bot-move", timeout: 120000 },
      ),
    );
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

function soundForMove(move, state) {
  if (state.over) return "end";
  if (state.in_check) return "check";
  if (move?.flags?.includes("k") || move?.flags?.includes("q")) return "castle";
  if (move?.captured) return "capture";
  return "move";
}

function handleState(state, localMove = null) {
  if (state.error) return showError({ message: state.error, data: state });
  if (Number.isInteger(state.version) && state.version < serverVersion) return;
  const previousMoves = lastMoveCount;
  lastState = state;
  serverVersion = Number.isInteger(state.version)
    ? state.version
    : serverVersion;
  const replay = new Chess();
  const replayed = (state.moves || []).every((san) =>
    Boolean(replay.move(san)),
  );
  const samePosition =
    replayed &&
    replay.fen().split(" ").slice(0, 4).join(" ") ===
      state.fen.split(" ").slice(0, 4).join(" ");
  game = samePosition ? replay : new Chess(state.fen);
  board?.position(state.fen, true);
  applyHighlights();
  updateClocks(state);
  renderMoveList(state.moves || []);
  renderCaptured();
  $("opening-name").textContent = state.opening_name || "Opening phase";
  $("decision-source").textContent = state.decision_source || "—";
  lastMoveCount = state.moves?.length || 0;
  if (lastMoveCount > previousMoves)
    playSound(
      soundForMove(localMove || game.history({ verbose: true }).at(-1), state),
    );
  const moveAnnouncement =
    lastMoveCount > previousMoves
      ? `${state.moves.at(-1)}${state.in_check ? ", check" : ""}. `
      : "";
  gameOver = Boolean(state.over);
  if ($("live-eval-toggle").checked && state.version !== lastEvaluatedVersion) {
    lastEvaluatedVersion = state.version;
    updateLiveEvaluation(state.fen);
  }
  updateGameButtons();
  updateInteraction();
  if (state.over) {
    stopPolling();
    presentResult(state.result);
    return;
  }
  const yourTurn = state.turn === humanColor;
  $("status").textContent = yourTurn ? "Your turn" : "Alan Dai is thinking";
  $("turn-status").classList.toggle("your-turn", yourTurn);
  announce(
    `${moveAnnouncement}${yourTurn ? "Your turn." : "Alan Dai is thinking."}`,
  );
}

async function updateLiveEvaluation(fen) {
  try {
    const data = await post(
      "/api/eval",
      { fen },
      { key: "live-evaluation", timeout: 10000 },
    );
    const percent = data.is_mate
      ? data.cp > 0
        ? 100
        : 0
      : Math.max(3, Math.min(97, 50 + 50 * Math.tanh(data.cp / 300)));
    $("live-eval-fill").style.height = `${percent}%`;
    $("live-eval-label").textContent = data.is_mate
      ? `M${Math.abs(data.mate)}`
      : `${data.cp >= 0 ? "+" : ""}${(data.cp / 100).toFixed(1)}`;
  } catch (error) {
    if (!(error instanceof CancelledRequest))
      toast("Live evaluation is unavailable.", "error");
  }
}

function presentResult(result) {
  const { kind, icon, title } = classifyResult(result);
  const dialog = $("result-dialog");
  dialog.className = `result-${kind}`;
  $("result-icon").textContent = icon;
  $("result-title").textContent = title;
  $("result-message").textContent = result || "The game has ended.";
  announce(result || title);
  if (!dialog.open) dialog.showModal();
}

function renderMoveList(moves) {
  const list = $("move-list");
  list.replaceChildren();
  if (!moves.length) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = "No moves yet";
    list.append(empty);
    return;
  }
  for (let index = 0; index < moves.length; index += 2) {
    const row = document.createElement("div");
    row.className = "move-row";
    const number = document.createElement("span");
    number.className = "move-number";
    number.textContent = `${index / 2 + 1}.`;
    const white = document.createElement("span");
    white.textContent = moves[index] || "";
    const black = document.createElement("span");
    black.textContent = moves[index + 1] || "";
    row.append(number, white, black);
    list.append(row);
  }
  list.scrollTop = list.scrollHeight;
}

function renderCaptured() {
  const initial = { p: 8, n: 2, b: 2, r: 2, q: 1 };
  const counts = { w: { ...initial }, b: { ...initial } };
  game
    .board()
    .flat()
    .filter(Boolean)
    .forEach((piece) => {
      if (piece.type !== "k") counts[piece.color][piece.type] -= 1;
    });
  const symbols = {
    wp: "♙",
    wn: "♘",
    wb: "♗",
    wr: "♖",
    wq: "♕",
    bp: "♟",
    bn: "♞",
    bb: "♝",
    br: "♜",
    bq: "♛",
  };
  const values = { p: 1, n: 3, b: 3, r: 5, q: 9 };
  const whiteGain = Object.entries(counts.b).reduce(
    (sum, [piece, amount]) => sum + values[piece] * amount,
    0,
  );
  const blackGain = Object.entries(counts.w).reduce(
    (sum, [piece, amount]) => sum + values[piece] * amount,
    0,
  );
  const display = $("captured-pieces");
  display.replaceChildren();
  ["w", "b"].forEach((color) => {
    const row = document.createElement("div");
    Object.entries(counts[color === "w" ? "b" : "w"]).forEach(
      ([piece, amount]) => {
        for (let i = 0; i < amount; i += 1) {
          const span = document.createElement("span");
          span.textContent = symbols[`${color === "w" ? "b" : "w"}${piece}`];
          row.append(span);
        }
      },
    );
    const advantage =
      color === "w" ? whiteGain - blackGain : blackGain - whiteGain;
    if (advantage > 0) row.append(` +${advantage}`);
    display.append(row);
  });
}

function updateClocks(state) {
  clocks = {
    bot: state.bot_clock,
    human: state.human_clock,
    at: performance.now(),
    turn: state.turn,
  };
  renderClocks();
}
function renderClocks() {
  let bot = clocks.bot;
  let human = clocks.human;
  const elapsed = (performance.now() - clocks.at) / 1000;
  if (!gameOver)
    clocks.turn === botColor ? (bot -= elapsed) : (human -= elapsed);
  const top = botColor === "black" ? bot : human;
  const bottom = botColor === "black" ? human : bot;
  setClock($("top-time"), top);
  setClock($("bottom-time"), bottom);
}
function setClock(element, seconds) {
  seconds = Math.max(0, seconds);
  element.textContent = formatClock(seconds);
  element.classList.toggle("warning", seconds < 30);
  element.classList.toggle("critical", seconds < 10);
  const key = element === $("top-time") ? "top" : "bottom";
  for (const threshold of [30, 10]) {
    if (seconds < threshold && !clockAnnouncements[key].has(threshold)) {
      clockAnnouncements[key].add(threshold);
      announce(
        `${element === $("bottom-time") ? "Your" : "Opponent"} clock is below ${threshold} seconds.`,
      );
    }
  }
}

function updateGameButtons() {
  ["abort-btn", "resign-btn", "undo-btn"].forEach(
    (id) => ($(id).hidden = gameOver),
  );
  ["rematch-btn", "switch-color-btn", "review-btn", "new-game-btn"].forEach(
    (id) => ($(id).hidden = !gameOver),
  );
  $("abort-btn").disabled = game.history().length > 4;
}

function showError(error) {
  setBusy(false);
  toast(error.message || "Something went wrong.", "error");
  $("status").textContent = error.message || "Request failed";
  if (!error.status) setConnected(false, error.message);
  if (error.data?.fen) handleState(error.data);
}

async function startGame({ rematch = false, switchColor = false } = {}) {
  if (requestBusy) return;
  if (
    !gameOver &&
    !(await confirmAction(
      "Replace this game?",
      "Your current game will be closed.",
      "Start new game",
    ))
  )
    return;
  const tcButton = document.querySelector(".tc-btn.selected");
  let tc = tcButton.dataset.tc;
  let initial;
  let increment = 0;
  if (tc === "custom") {
    initial = Number($("custom-minutes").value) * 60;
    increment = Number($("custom-increment").value);
    if (
      !Number.isFinite(initial) ||
      initial < 60 ||
      initial > 10800 ||
      !Number.isFinite(increment) ||
      increment < 0 ||
      increment > 60
    ) {
      toast(
        "Choose 1–180 minutes and an increment from 0–60 seconds.",
        "error",
      );
      return;
    }
    tc = initial <= 90 ? "bullet" : initial <= 300 ? "blitz" : "rapid";
  }
  const selectedColor = document.querySelector(".color-btn.selected").dataset
    .color;
  humanColor = switchColor
    ? humanColor === "white"
      ? "black"
      : "white"
    : selectedColor;
  botColor = humanColor === "white" ? "black" : "white";
  setBusy(true, "Creating your game…");
  try {
    const state = await post(
      "/new_game",
      {
        tc,
        bot_color: botColor,
        is_rematch: rematch,
        initial_seconds: initial,
        increment_seconds: increment,
      },
      { timeout: 30000 },
    );
    $("setup-panel").hidden = true;
    $("game-panel").hidden = false;
    gameOver = false;
    lastMoveCount = 0;
    clockAnnouncements.top.clear();
    clockAnnouncements.bottom.clear();
    initializeBoard(state);
    serverVersion = -1;
    handleState(state);
    startPolling();
    if (state.turn === botColor) await requestBotMove();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

function startPolling() {
  stopPolling();
  clockTimer = setInterval(renderClocks, 200);
  schedulePoll(1000);
}
function stopPolling() {
  clearTimeout(pollTimer);
  clearInterval(clockTimer);
}
function schedulePoll(delay) {
  clearTimeout(pollTimer);
  if (!gameOver && !document.hidden) pollTimer = setTimeout(syncState, delay);
}
async function syncState() {
  if (gameOver || document.hidden) return;
  try {
    handleState(await api("/state", { key: "game-state", timeout: 5000 }));
    pollFailures = 0;
    setConnected(true);
  } catch (error) {
    pollFailures += 1;
    if (error.status !== 404) setConnected(false, error.message);
  }
  schedulePoll(Math.min(15000, 1000 * 2 ** pollFailures));
}

async function gameAction(path, confirmation) {
  if (confirmation && !(await confirmAction(...confirmation))) return;
  setBusy(true);
  try {
    handleState(await post(path, {}, { timeout: 10000 }));
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function switchTab(tab) {
  const analyze = tab === "analyze";
  $("play-section").hidden = analyze;
  $("analyze-section").hidden = !analyze;
  document.querySelectorAll(".nav-tab").forEach((button) => {
    const active = button.dataset.tab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  if (analyze) (await getAnalysis()).loadRecentGames();
  resizeBoards();
}

function resizeBoards() {
  board?.resize();
  window.dispatchEvent(new CustomEvent("chessbot:resize-analysis"));
}
function applyPreferences() {
  document.documentElement.dataset.theme = prefs.theme;
  document.documentElement.dataset.boardTheme = prefs.boardTheme;
  document.documentElement.dataset.pieceTheme = prefs.pieceTheme;
  document.documentElement.classList.toggle(
    "high-contrast",
    prefs.highContrast,
  );
  $("coordinates-toggle").setAttribute(
    "aria-pressed",
    String(prefs.coordinates),
  );
  $("sound-toggle").textContent = soundEnabled() ? "🔊" : "🔇";
  $("sound-toggle").setAttribute("aria-pressed", String(soundEnabled()));
}

function bindEvents() {
  document
    .querySelectorAll(".nav-tab")
    .forEach((button) =>
      button.addEventListener("click", () => switchTab(button.dataset.tab)),
    );
  document.querySelectorAll(".tc-btn").forEach((button) =>
    button.addEventListener("click", () => {
      document
        .querySelectorAll(".tc-btn")
        .forEach((item) => item.classList.remove("selected"));
      button.classList.add("selected");
      document
        .querySelectorAll(".tc-btn")
        .forEach((item) =>
          item.setAttribute("aria-pressed", String(item === button)),
        );
      $("custom-time-fields").hidden = button.dataset.tc !== "custom";
    }),
  );
  document.querySelectorAll(".color-btn").forEach((button) =>
    button.addEventListener("click", () => {
      document
        .querySelectorAll(".color-btn")
        .forEach((item) => item.classList.remove("selected"));
      button.classList.add("selected");
      document
        .querySelectorAll(".color-btn")
        .forEach((item) =>
          item.setAttribute("aria-pressed", String(item === button)),
        );
    }),
  );
  $("start-btn").addEventListener("click", () => startGame());
  $("retry-btn").addEventListener("click", () => {
    refreshHealth();
    syncState();
  });
  $("abort-btn").addEventListener("click", () =>
    gameAction("/abort", [
      "Abort game?",
      "This game will end without a result.",
      "Abort",
    ]),
  );
  $("resign-btn").addEventListener("click", () =>
    gameAction("/resign", [
      "Resign game?",
      "Alan Dai will win this game.",
      "Resign",
    ]),
  );
  $("undo-btn").addEventListener("click", () => gameAction("/undo"));
  $("rematch-btn").addEventListener("click", () =>
    startGame({ rematch: true }),
  );
  $("switch-color-btn").addEventListener("click", () =>
    startGame({ rematch: true, switchColor: true }),
  );
  $("new-game-btn").addEventListener("click", () => {
    $("game-panel").hidden = true;
    $("setup-panel").hidden = false;
    gameOver = true;
    stopPolling();
  });
  $("review-btn").addEventListener("click", async () => {
    await switchTab("analyze");
    (await getAnalysis()).openGame({
      pgn: game.pgn(),
      user_color: humanColor,
      opponent: "Alan Dai",
      opening: lastState?.opening_name,
      result: lastState?.result || "Review",
    });
  });
  $("result-rematch").addEventListener("click", () =>
    setTimeout(() => startGame({ rematch: true }), 0),
  );
  $("result-switch-color").addEventListener("click", () =>
    setTimeout(() => startGame({ rematch: true, switchColor: true }), 0),
  );
  $("sound-toggle").addEventListener("click", () => {
    setSoundEnabled(!soundEnabled());
    applyPreferences();
    playSound("notify");
  });
  $("theme-toggle").addEventListener("click", () => {
    prefs.theme = prefs.theme === "dark" ? "light" : "dark";
    store("chessbot:theme", prefs.theme);
    applyPreferences();
  });
  $("coordinates-toggle").addEventListener("click", () => {
    prefs.coordinates = !prefs.coordinates;
    store("chessbot:coordinates", prefs.coordinates ? "on" : "off");
    if (lastState) initializeBoard(lastState);
    applyPreferences();
  });
  $("settings-btn").addEventListener("click", () =>
    $("settings-dialog").showModal(),
  );
  $("board-theme").addEventListener("change", (event) => {
    prefs.boardTheme = event.target.value;
    store("chessbot:board-theme", prefs.boardTheme);
    applyPreferences();
  });
  $("piece-theme").addEventListener("change", (event) => {
    prefs.pieceTheme = event.target.value;
    store("chessbot:piece-theme", prefs.pieceTheme);
    applyPreferences();
  });
  $("high-contrast-toggle").addEventListener("change", (event) => {
    prefs.highContrast = event.target.checked;
    store("chessbot:contrast", prefs.highContrast ? "high" : "normal");
    applyPreferences();
  });
  $("live-eval-toggle").addEventListener("change", (event) => {
    $("live-eval-wrap").hidden = !event.target.checked;
    if (event.target.checked && lastState) {
      lastEvaluatedVersion = -1;
      updateLiveEvaluation(lastState.fen);
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearTimeout(pollTimer);
    else {
      syncState();
      refreshHealth();
    }
  });
  window.addEventListener("resize", resizeBoards);
  document.addEventListener("keydown", (event) => {
    analysisModule?.analysisKeyHandler(event);
    if (
      event.target.matches('[role="tab"]') &&
      ["ArrowLeft", "ArrowRight"].includes(event.key)
    ) {
      event.preventDefault();
      const tab = event.target.dataset.tab === "play" ? "analyze" : "play";
      switchTab(tab);
      document.querySelector(`[data-tab="${tab}"]`).focus();
      return;
    }
    if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
    if (event.key.toLowerCase() === "n") startGame();
    if (event.key.toLowerCase() === "r" && !gameOver) $("resign-btn").click();
    if (event.key.toLowerCase() === "f" && board) board.flip();
  });
}

applyPreferences();
bindEvents();
refreshHealth();
setInterval(refreshHealth, 30000);
