import { api, post } from "./api.js";
import { evaluationPercent } from "./chess-utils.js";

const $ = (id) => document.getElementById(id);
let board;
let viewer = new Chess();
let mainMoves = [];
let mainIndex = 0;
let variation = [];
const branches = new Map();
let games = [];
let page = 1;
const pageSize = 8;
let debounce;
const evaluations = new Map();

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
      : "Import a PGN or refresh when Chess.com is available.";
    empty.append(title, detail, button("Import PGN", "button primary"));
    empty.lastElementChild.addEventListener("click", () =>
      $("import-pgn-btn").click(),
    );
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
  const cached = localStorage.getItem("chessbot:recent-games");
  if (!force && cached) {
    try {
      const parsed = JSON.parse(cached);
      if (Date.now() - parsed.savedAt < 300000) {
        games = parsed.games;
        renderGames();
        return;
      }
    } catch {
      localStorage.removeItem("chessbot:recent-games");
    }
  }
  $("recent-games").innerHTML =
    '<div class="skeleton-list"><span></span><span></span><span></span></div>';
  try {
    const data = await api("/api/games", {
      key: "recent-games",
      timeout: 20000,
      retries: 1,
    });
    games = Array.isArray(data.games) ? data.games : [];
    localStorage.setItem(
      "chessbot:recent-games",
      JSON.stringify({ savedAt: Date.now(), games }),
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
    branches.set(
      mainIndex,
      variation.map((move) => move.san),
    );
  mainIndex = Math.max(0, Math.min(index, mainMoves.length));
  viewer = new Chess();
  for (let i = 0; i < mainIndex; i += 1) viewer.move(mainMoves[i]);
  variation = [];
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
  branches.forEach((moves, branchIndex) => {
    const branch = document.createElement("div");
    branch.className = "variation-branch";
    branch.textContent = `After move ${branchIndex}: ${moves.join(" ")}`;
    list.append(branch);
  });
}

function onDrop(from, to) {
  const move = viewer.move({ from, to, promotion: "q" });
  if (!move) return "snapback";
  variation.push(move);
  branches.set(
    mainIndex,
    variation.map((item) => item.san),
  );
  scheduleAnalysis();
  renderMoveTree();
}

function initializeBoard(orientation = "white") {
  board?.destroy();
  board = Chessboard("av-board", {
    position: viewer.fen(),
    orientation,
    draggable: true,
    showNotation: window.chessbotPrefs?.coordinates !== false,
    onDrop,
    onSnapEnd: () => board.position(viewer.fen()),
    pieceTheme: "/static/pieces/{piece}.png",
  });
}

export function openGame(game) {
  const loader = new Chess();
  if (!loader.load_pgn(game.pgn || "")) {
    notify("That PGN could not be parsed.", "error");
    return;
  }
  mainMoves = loader.history({ verbose: true });
  mainIndex = 0;
  variation = [];
  branches.clear();
  evaluations.clear();
  viewer = new Chess();
  initializeBoard(game.user_color || "white");
  $("recent-games-panel").hidden = true;
  $("av-game-view").hidden = false;
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
  debounce = setTimeout(analyzePosition, 120);
}

async function analyzePosition() {
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
    renderLines(data.lines || [], data.depth || 0);
    updateEval(data.eval_cp, data.eval_is_mate, data.eval_mate);
    const previous = evaluations.get(mainIndex - 1);
    const swing =
      previous?.cp == null ? 0 : Math.abs(data.eval_cp - previous.cp);
    const quality =
      swing >= 250
        ? "blunder"
        : swing >= 120
          ? "mistake"
          : swing >= 60
            ? "inaccuracy"
            : "";
    evaluations.set(mainIndex, {
      cp: data.eval_cp,
      label: data.eval_is_mate
        ? `M${Math.abs(data.eval_mate)}`
        : `${(data.eval_cp / 100).toFixed(1)}`,
      quality,
    });
    renderMoveTree();
  } catch (error) {
    notify(error.message, "error");
  } finally {
    $("analysis-progress").hidden = true;
  }
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
  branches.set(
    mainIndex,
    variation.map((move) => move.san),
  );
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
        viewer = loaded;
        variation = [];
        initializeBoard();
        $("recent-games-panel").hidden = true;
        $("av-game-view").hidden = false;
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

function download(name, contents, type = "text/plain") {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([contents], { type }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

export function initAnalysis() {
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
    $("av-game-view").hidden = true;
    $("recent-games-panel").hidden = false;
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
  $("av-return-btn").addEventListener("click", () => resetTo(mainIndex));
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
    download("analysis.pgn", viewer.pgn()),
  );
  $("copy-fen-btn").addEventListener("click", async () => {
    await navigator.clipboard.writeText(viewer.fen());
    notify("FEN copied.", "success");
  });
  $("share-analysis-btn").addEventListener("click", async () => {
    location.hash = `fen=${encodeURIComponent(viewer.fen())}`;
    await navigator.clipboard.writeText(location.href);
    notify("Share link copied.", "success");
  });
  $("download-report-btn").addEventListener("click", () =>
    download(
      "chess-analysis.txt",
      `Chess analysis\n\nFEN: ${viewer.fen()}\nPGN: ${viewer.pgn()}\nEvaluation: ${$("av-eval-label").textContent}\n`,
    ),
  );
  if (location.hash.startsWith("#fen=")) {
    const fen = decodeURIComponent(location.hash.slice(5));
    setTimeout(() => {
      $("import-fen-btn").click();
      $("input-value").value = fen;
    }, 0);
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
