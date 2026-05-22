import json
import os
import shutil
import time
import threading
from dotenv import load_dotenv
load_dotenv()
import re
import datetime
import chess
import chess.engine
import chess.pgn
import requests as _requests
from flask import Flask, jsonify, request, render_template_string

from bot.engine import ChessBotEngine
from bot.think_timer import ThinkTimer

app = Flask(__name__)

USERNAME         = os.environ.get("CHESS_USERNAME", os.environ.get("USERNAME", "yuandan"))
MODEL_PATH       = os.environ.get("MODEL_PATH",       "best_model.pt")
BOOK_PATH        = os.environ.get("BOOK_PATH",        "opening_book.json")
_sf_env = os.environ.get("STOCKFISH_PATH", "")
STOCKFISH_PATH   = (
    _sf_env if _sf_env and os.path.isfile(_sf_env)
    else shutil.which("stockfish") or "/usr/games/stockfish"
)

TIME_CONTROLS = {
    "bullet": 60,
    "blitz":  180,
    "rapid":  600,
}

# Single shared game state (one game at a time)
_lock          = threading.Lock()
_analysis_lock = threading.Lock()
state: dict    = {}


def _new_state(tc: str, bot_color: str) -> dict:
    bot_side = chess.WHITE if bot_color == "white" else chess.BLACK
    engine = ChessBotEngine(
        time_control=tc,
        model_path=MODEL_PATH,
        username=USERNAME,
        opening_book_path=BOOK_PATH,
        stockfish_path=STOCKFISH_PATH,
    )
    total = float(TIME_CONTROLS[tc])
    return {
        "engine":      engine,
        "think_timer": ThinkTimer(tc),
        "board":       chess.Board(),
        "bot_color":   bot_side,
        "bot_clock":   total,
        "human_clock": total,
        "last_tick":   time.time(),
        "over":        False,
        "result":      None,
        "is_rematch":  False,
        "moves":       [],          # list of SAN strings
        "last_move":   None,        # (from_sq, to_sq) for highlighting
    }


def _board_json(s: dict) -> dict:
    board: chess.Board = s["board"]
    return {
        "fen":         board.fen(),
        "bot_color":   "white" if s["bot_color"] == chess.WHITE else "black",
        "bot_clock":   round(s["bot_clock"],   1),
        "human_clock": round(s["human_clock"], 1),
        "over":        s["over"],
        "result":      s["result"],
        "turn":        "white" if board.turn == chess.WHITE else "black",
        "moves":       s["moves"],
        "last_move":   s["last_move"],
        "in_check":    board.is_check(),
    }


def _tick_clock(s: dict):
    """Subtract wall time from whichever clock is running."""
    now = time.time()
    elapsed = now - s["last_tick"]
    s["last_tick"] = now
    board: chess.Board = s["board"]
    if board.turn == s["bot_color"]:
        s["bot_clock"] = max(0.0, s["bot_clock"] - elapsed)
    else:
        s["human_clock"] = max(0.0, s["human_clock"] - elapsed)


def _check_game_over(s: dict):
    board: chess.Board = s["board"]
    if board.is_game_over(claim_draw=True):
        s["over"] = True
        if board.is_checkmate():
            loser = s["bot_color"] if board.turn == s["bot_color"] else "human"
            s["result"] = "You win by checkmate!" if loser == s["bot_color"] else "Alan Dai wins by checkmate!"
        elif board.is_stalemate():
            s["result"] = "Draw — stalemate."
        elif board.is_insufficient_material():
            s["result"] = "Draw — insufficient material."
        elif board.is_fifty_moves():
            s["result"] = "Draw — 50-move rule."
        elif board.is_repetition(3):
            s["result"] = "Draw — threefold repetition."
        else:
            s["result"] = f"Game over: {board.result()}"
    if s["bot_clock"] <= 0:
        s["over"] = True
        s["result"] = "Alan Dai flagged — you win on time!"
    if s["human_clock"] <= 0:
        s["over"] = True
        s["result"] = "You flagged — Alan Dai wins on time!"


# ── Analysis Stockfish (separate from the game engine) ────────────────────
_analysis_sf = None

def _get_analysis_sf():
    global _analysis_sf
    if _analysis_sf is None:
        try:
            _analysis_sf = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
            _analysis_sf.configure({"Threads": 1, "Hash": 64})
        except Exception:
            pass
    return _analysis_sf


@app.route("/api/games")
def api_games():
    """Fetch the 20 most recent chess.com games for the username."""
    hdrs = {"User-Agent": "ChessBot/1.0"}
    try:
        archives = _requests.get(
            f"https://api.chess.com/pub/player/{USERNAME}/games/archives",
            headers=hdrs, timeout=10,
        ).json().get("archives", [])
        if not archives:
            return jsonify({"games": []})

        # Collect games from most-recent months until we have 20
        collected = []
        for url in reversed(archives):
            month = _requests.get(url, headers=hdrs, timeout=15).json().get("games", [])
            collected = month + collected
            if len(collected) >= 20:
                break
        collected = collected[-20:][::-1]   # newest first

        _WIN  = {"win"}
        _LOSS = {"lose", "resigned", "timeout", "checkmated", "abandoned"}

        result = []
        for g in collected:
            white, black = g.get("white", {}), g.get("black", {})
            is_white = white.get("username", "").lower() == USERNAME.lower()
            if is_white:
                opponent, opp_rating, my_result = black.get("username","?"), black.get("rating","?"), white.get("result","?")
            else:
                opponent, opp_rating, my_result = white.get("username","?"), white.get("rating","?"), black.get("result","?")

            pgn_text = g.get("pgn", "")
            eco_m    = re.search(r'\[ECOUrl "([^"]+)"\]', pgn_text)
            opening  = eco_m.group(1).split("/")[-1].replace("-", " ").title()[:42] if eco_m else "Unknown"

            result_char = "W" if my_result in _WIN else ("L" if my_result in _LOSS else "D")
            date_str    = datetime.datetime.fromtimestamp(g.get("end_time", 0)).strftime("%b %d")

            result.append({
                "url":        g.get("url", ""),
                "opponent":   opponent,
                "opp_rating": opp_rating,
                "result":     result_char,
                "time_class": g.get("time_class", ""),
                "date":       date_str,
                "pgn":        pgn_text,
                "opening":    opening,
                "user_color": "white" if is_white else "black",
            })
        return jsonify({"games": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/eval", methods=["POST"])
def api_eval():
    """Run Stockfish depth-15 on a FEN, return centipawn score from White's POV."""
    fen = request.json.get("fen", "")
    try:
        with _analysis_lock:
            sf = _get_analysis_sf()
            if sf is None:
                return jsonify({"error": "Stockfish unavailable"}), 500
            board = chess.Board(fen)
            if board.is_game_over():
                return jsonify({"cp": 0, "is_mate": False, "mate": None})
            info  = sf.analyse(board, chess.engine.Limit(depth=15))
            score = info["score"].white()
            if score.is_mate():
                m = score.mate()
                return jsonify({"cp": 10000 if m > 0 else -10000, "is_mate": True, "mate": m})
            return jsonify({"cp": score.score(), "is_mate": False, "mate": None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/lines", methods=["POST"])
def api_lines():
    """Run Stockfish multipv=3 depth-15 on a FEN; return top-3 lines with SAN continuations."""
    fen = request.json.get("fen", "")
    try:
        with _analysis_lock:
            sf = _get_analysis_sf()
            if sf is None:
                return jsonify({"error": "Stockfish unavailable"}), 500
            board = chess.Board(fen)
            if board.is_game_over():
                return jsonify({"lines": []})
            infos = sf.analyse(board, chess.engine.Limit(time=0.3), multipv=3)
            if not isinstance(infos, list):
                infos = [infos]

        lines = []
        for info in infos:
            score  = info["score"].white()
            pv     = info.get("pv", [])
            tmp    = board.copy()
            san_pv = []
            for m in pv[:8]:
                try:
                    san_pv.append(tmp.san(m))
                    tmp.push(m)
                except Exception:
                    break
            if score.is_mate():
                mate = score.mate()
                lines.append({"cp": 10000 if mate > 0 else -10000,
                               "is_mate": True, "mate": mate, "pv": san_pv})
            else:
                lines.append({"cp": score.score(), "is_mate": False,
                               "mate": None, "pv": san_pv})
        # First line's score serves as the position eval for the eval bar
        ev = lines[0] if lines else {"cp": 0, "is_mate": False, "mate": None}
        return jsonify({"lines": lines,
                        "eval_cp": ev["cp"], "eval_is_mate": ev["is_mate"], "eval_mate": ev["mate"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Alan Dai</title>
<link rel="stylesheet"
      href="https://unpkg.com/@chrisoakman/chessboardjs@1.0.0/dist/chessboard-1.0.0.min.css">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Segoe UI', sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    min-height: 100vh;
    padding: 8px 16px 8px;
  }
  h1 { font-size: 1.2rem; letter-spacing: 1px; margin-bottom: 6px; color: #a78bfa; }

  /* ── Navigation tabs ───────────────────────────────────────────────── */
  #main-nav {
    display: flex;
    gap: 6px;
    margin-bottom: 10px;
    background: #262421;
    padding: 4px;
    border-radius: 8px;
  }
  .nav-tab {
    padding: 7px 22px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: #8a8784;
    font-size: 0.88rem;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
  }
  .nav-tab:hover  { color: #e8e6e3; }
  .nav-tab.active { background: #3d3a37; color: #e8e6e3; }

  /* ── Analysis section ──────────────────────────────────────────────── */
  #analyze-section { width: 100%; max-width: 480px; }

  /* Games list */
  #av-games-list { display: flex; flex-direction: column; gap: 4px; }
  .game-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 12px;
    background: #262421;
    border-radius: 6px;
    cursor: pointer;
    border-left: 3px solid transparent;
    transition: background 0.12s;
  }
  .game-row:hover { background: #3d3a37; }
  .result-badge {
    width: 22px; height: 22px;
    border-radius: 3px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.78rem; font-weight: 700; flex-shrink: 0;
  }
  .result-W { background: #4d8c2f; color: #fff; }
  .result-L { background: #922; color: #fff; }
  .result-D { background: #555; color: #fff; }
  .game-info { flex: 1; min-width: 0; }
  .game-opp  { font-size: 0.88rem; font-weight: 600; color: #e8e6e3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .game-opening { font-size: 0.73rem; color: #8a8784; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .game-meta { font-size: 0.73rem; color: #666; text-align: right; flex-shrink: 0; }

  /* Eval bar */
  #av-eval-wrap {
    width: 100%; height: 12px;
    background: #1a1a1a;
    border-radius: 3px;
    overflow: hidden;
    position: relative;
    margin-bottom: 2px;
  }
  #av-eval-fill {
    position: absolute; right: 0; top: 0; bottom: 0;
    width: 50%;
    background: #e8e6e3;
    transition: width 0.25s ease;
  }
  #av-eval-label {
    position: absolute; width: 100%; text-align: center;
    line-height: 12px; font-size: 0.62rem; font-weight: 700;
    color: rgba(128,128,128,0.9); pointer-events: none; z-index: 1;
  }

  /* Analysis move list */
  #av-move-list {
    width: 100%;
    background: #262421;
    border-radius: 6px;
    padding: 5px 10px;
    max-height: 68px;
    overflow-y: auto;
    font-size: 0.80rem;
    color: #ccc;
    margin-top: 4px;
    line-height: 1.7;
  }
  .av-move {
    display: inline-block;
    padding: 1px 5px;
    border-radius: 3px;
    cursor: pointer;
  }
  .av-move:hover { background: #3d3a37; }
  .av-move.current { background: #5b21b6; color: #fff; }

  #setup-panel {
    background: #16213e;
    border: 1px solid #2d2d5e;
    border-radius: 12px;
    padding: 28px 32px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    width: 320px;
  }
  #setup-panel h2 { font-size: 1rem; color: #a78bfa; margin-bottom: 4px; }
  .row { display: flex; gap: 8px; }
  .btn {
    flex: 1;
    padding: 10px;
    border: 1px solid #3d3d6e;
    border-radius: 8px;
    background: #1a1a3e;
    color: #e0e0e0;
    cursor: pointer;
    font-size: 0.9rem;
    transition: background 0.15s, border-color 0.15s;
  }
  .btn:hover { background: #2a2a5e; }
  .btn.selected { background: #5b21b6; border-color: #7c3aed; color: #fff; }
  #start-btn {
    padding: 12px;
    background: #5b21b6;
    border: none;
    border-radius: 8px;
    color: #fff;
    font-size: 1rem;
    cursor: pointer;
    margin-top: 6px;
    transition: background 0.15s;
  }
  #start-btn:hover { background: #7c3aed; }

  #game-panel { display: none; flex-direction: column; align-items: center; gap: 0; width: 100%; max-width: 480px; }

  /* ── Chess.com-style board square colours ──────────────────────────── */
  .white-1e1d7 { background-color: #f0d9b5 !important; }
  .black-3c85d { background-color: #b58863 !important; }

  /* Coordinate labels: contrasting colour on each square type */
  .white-1e1d7 .notation-322f9 { color: #b58863 !important; font-weight: 700; }
  .black-3c85d .notation-322f9 { color: #f0d9b5 !important; font-weight: 700; }

  /* ── Player info strip (avatar + name + clock) ─────────────────────── */
  .player-strip {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 8px;
    background: #262421;   /* chess.com's dark panel colour */
    border-left: 3px solid transparent;
  }
  .player-strip.strip-top    { border-radius: 6px 6px 0 0; }
  .player-strip.strip-bottom { border-radius: 0 0 6px 6px; }
  .player-strip.active       { border-left-color: #81b64c; }

  .player-avatar {
    width: 36px; height: 36px;
    background: #3d3a37;
    border-radius: 4px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.3rem;
    flex-shrink: 0;
  }
  .player-meta { flex: 1; display: flex; flex-direction: column; gap: 1px; }
  .player-name { font-size: 0.95rem; font-weight: 600; color: #e8e6e3; }
  .player-sub  { font-size: 0.72rem; color: #8a8784; }

  .player-clock {
    font-size: 1.55rem;
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    letter-spacing: 0.02em;
    padding: 5px 12px;
    background: #161412;
    border-radius: 5px;
    color: #e8e6e3;
    min-width: 78px;
    text-align: center;
    transition: background 0.2s, color 0.2s;
  }
  .player-strip.active .player-clock {
    background: #e8e6e3;
    color: #161412;
  }
  /* Low-time warning */
  .player-strip.low-time .player-clock { background: #c62d2d; color: #fff; }

  /* ── Board frame ───────────────────────────────────────────────────── */
  #board-wrap {
    width: 100%;
    border-left: 3px solid #262421;
    border-right: 3px solid #262421;
  }

  /* ── Status bar ────────────────────────────────────────────────────── */
  #status {
    width: 100%;
    font-size: 0.85rem;
    color: #8a8784;
    min-height: 1.4em;
    text-align: center;
    padding: 6px 0 2px;
  }

  /* ── Move list ─────────────────────────────────────────────────────── */
  #move-list {
    width: 100%;
    background: #262421;
    border-radius: 6px;
    padding: 8px 12px;
    max-height: 110px;
    overflow-y: auto;
    font-size: 0.83rem;
    color: #ccc;
    line-height: 1.9;
    margin-top: 8px;
  }
  .move-pair { display: inline; }
  .move-num  { color: #666; margin-right: 2px; }
  .move-san  {
    display: inline-block;
    padding: 1px 5px;
    border-radius: 3px;
    cursor: default;
  }
  .move-san:hover { background: #3d3a37; }

  /* ── Action buttons ────────────────────────────────────────────────── */
  .action-btn {
    flex: 1;
    padding: 9px;
    border: 1px solid #3d3a37;
    border-radius: 6px;
    background: #262421;
    color: #e0e0e0;
    font-size: 0.88rem;
    cursor: pointer;
    transition: background 0.15s;
  }
  .action-btn:hover:not(:disabled) { background: #3d3a37; }
  .action-btn:disabled { opacity: 0.30; cursor: default; }
  .action-btn.danger   { color: #e57373; border-color: #7a2222; }
  .action-btn.danger:hover:not(:disabled) { background: #2a1515; }
  .action-btn.primary  { background: #4d8c2f; border-color: #5fa836; color: #fff; }
  .action-btn.primary:hover:not(:disabled) { background: #5fa836; }
  #action-row { display: flex; gap: 8px; width: 100%; margin-top: 10px; }

  /* ── Square highlights ─────────────────────────────────────────────── */
  #board .sq-last    { background-color: rgba(205, 195, 45, 0.62) !important; }
  #board .sq-sel     { background-color: rgba(20, 130, 20, 0.52) !important;
                       animation: sel-pulse 1.4s ease-in-out infinite; }
  #board .sq-check   { background: radial-gradient(ellipse at center,
                         rgba(255,0,0,0.88) 0%, rgba(231,0,0,0.38) 55%,
                         transparent 100%) !important; }
  #board .sq-premove { background-color: rgba(100, 100, 255, 0.40) !important; }

  /* ── Legal-move indicators ─────────────────────────────────────────── */
  .legal-dot {
    position: absolute;
    width: 30%; height: 30%;
    background: rgba(0, 0, 0, 0.22);
    border-radius: 50%;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    pointer-events: none;
    z-index: 10;
    box-shadow: 0 1px 4px rgba(0,0,0,0.25);
  }
  .legal-ring {
    position: absolute;
    width: 100%; height: 100%;
    box-shadow: inset 0 0 0 5px rgba(0, 0, 0, 0.22);
    top: 0; left: 0;
    pointer-events: none;
    z-index: 10;
    border-radius: 2px;
  }

  /* ── Piece drag aesthetics ─────────────────────────────────────────── */
  #board img, #av-board img {
    cursor: grab;
    user-select: none;
    -webkit-user-select: none;
  }
  /* Dragged ghost piece (appended to <body> by chessboard.js during drag) */
  body.is-dragging > img {
    filter: drop-shadow(0 10px 24px rgba(0,0,0,0.70)) brightness(1.06) !important;
    cursor: grabbing !important;
    z-index: 9999 !important;
  }
  /* Pulse animation for selected square */
  @keyframes sel-pulse {
    0%, 100% { filter: brightness(1.0); }
    50%       { filter: brightness(1.35); }
  }

  /* ── Analyze sub-nav ─────────────────────────────────────────────────── */
  #analyze-sub-nav {
    display: flex;
    gap: 4px;
    margin-bottom: 10px;
    background: #262421;
    padding: 3px;
    border-radius: 7px;
    width: 100%;
  }

  /* ── Engine lines panel ──────────────────────────────────────────────── */
  #av-lines-panel { width: 100%; display: flex; flex-direction: column; gap: 2px; margin-top: 4px; }
  .engine-line {
    display: flex;
    align-items: baseline;
    gap: 7px;
    background: #262421;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 0.78rem;
  }

  /* ── Analysis: compact board + player strips ─────────────────────────── */
  #av-board-wrap { max-width: 360px; }
  #av-board .sq-sel  { background-color: rgba(20, 130, 20, 0.52) !important;
                       animation: sel-pulse 1.4s ease-in-out infinite; }
  #av-board .sq-last { background-color: rgba(205, 195, 45, 0.62) !important; }
  #av-top-strip, #av-bottom-strip { padding: 3px 6px; }
  #av-top-strip .player-avatar,
  #av-bottom-strip .player-avatar  { width: 24px; height: 24px; font-size: 0.88rem; }
  #av-top-strip .player-name,
  #av-bottom-strip .player-name    { font-size: 0.82rem; }
  #av-top-strip .player-sub,
  #av-bottom-strip .player-sub     { font-size: 0.62rem; }
  #analyze-sub-nav { margin-bottom: 6px; }
  .ev-badge {
    font-size: 0.72rem;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 3px;
    flex-shrink: 0;
    min-width: 46px;
    text-align: center;
    font-family: monospace;
  }
  .ev-pos { background: #4d8c2f; color: #fff; }
  .ev-neg { background: #922;    color: #fff; }
  .ev-eq  { background: #555;    color: #fff; }
  .ev-pv  { color: #b0aba6; line-height: 1.5; word-break: break-word; }
</style>
</head>
<body>
<h1>Alan Dai</h1>

<nav id="main-nav">
  <button class="nav-tab active" id="tab-play"    onclick="switchTab('play')">Play</button>
</nav>

<div id="play-section" style="width:100%;display:flex;flex-direction:column;align-items:center;">

<!-- Setup -->
<div id="setup-panel">
  <div>
    <h2>Time control</h2>
    <div class="row">
      <button class="btn tc-btn" data-tc="bullet">1 min</button>
      <button class="btn tc-btn selected" data-tc="blitz">3 min</button>
      <button class="btn tc-btn" data-tc="rapid">10 min</button>
    </div>
  </div>
  <div>
    <h2>You play as</h2>
    <div class="row">
      <button class="btn color-btn selected" data-color="white">White</button>
      <button class="btn color-btn" data-color="black">Black</button>
    </div>
  </div>
  <button id="start-btn">Start game</button>
</div>

<!-- Game -->
<div id="game-panel">
  <!-- Opponent (top) -->
  <div class="player-strip strip-top" id="top-strip">
    <div class="player-avatar" id="top-avatar">♟</div>
    <div class="player-meta">
      <div class="player-name" id="top-name">Alan Dai</div>
      <div class="player-sub"  id="top-sub"></div>
    </div>
    <div class="player-clock" id="top-time">3:00</div>
  </div>

  <div id="board-wrap"><div id="board"></div></div>

  <!-- Player (bottom) -->
  <div class="player-strip strip-bottom" id="bottom-strip">
    <div class="player-avatar" id="bottom-avatar">♙</div>
    <div class="player-meta">
      <div class="player-name" id="bottom-name">You</div>
      <div class="player-sub"  id="bottom-sub"></div>
    </div>
    <div class="player-clock" id="bottom-time">3:00</div>
  </div>

  <div id="status"></div>
  <div id="move-list"><span class="move-num" style="color:#555">No moves yet</span></div>
  <div id="action-row">
    <button id="abort-btn"    class="action-btn">Abort</button>
    <button id="resign-btn"   class="action-btn danger">Resign</button>
    <button id="rematch-btn"  class="action-btn primary" style="display:none">Rematch</button>
    <button id="review-btn"   class="action-btn"         style="display:none">Review</button>
    <button id="new-game-btn" class="action-btn"         style="display:none">New Game</button>
  </div>
</div><!-- #game-panel -->
</div><!-- #play-section -->

<!-- ── Review section ────────────────────────────────────────────── -->
<div id="analyze-section" style="display:none">

  <!-- Game viewer -->
  <div id="av-game-view" style="display:flex;flex-direction:column;align-items:center;gap:0;">
    <div style="width:100%;display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <button class="action-btn" style="flex:0;padding:6px 14px;" onclick="switchTab('play')">← Play</button>
      <span id="av-game-title" style="font-size:0.82rem;color:#8a8784;text-align:right;flex:1;padding-left:10px;"></span>
    </div>
    <div id="av-eval-wrap">
      <div id="av-eval-fill"></div>
      <div id="av-eval-label">–</div>
    </div>
    <div class="player-strip strip-top" id="av-top-strip">
      <div class="player-avatar" id="av-top-avatar">♟</div>
      <div class="player-meta">
        <div class="player-name" id="av-top-name">Opponent</div>
        <div class="player-sub"  id="av-top-sub">Black</div>
      </div>
    </div>
    <div id="av-board-wrap" style="width:100%;border-left:3px solid #262421;border-right:3px solid #262421;">
      <div id="av-board"></div>
    </div>
    <div class="player-strip strip-bottom" id="av-bottom-strip">
      <div class="player-avatar" id="av-bottom-avatar">♙</div>
      <div class="player-meta">
        <div class="player-name" id="av-bottom-name">You (yuandan)</div>
        <div class="player-sub"  id="av-bottom-sub">White</div>
      </div>
    </div>
    <div style="display:flex;gap:6px;margin-top:10px;width:100%;">
      <button class="action-btn" onclick="avGoTo(0)">|◀</button>
      <button class="action-btn" onclick="avStepBack()">◀</button>
      <button class="action-btn" onclick="avStep(1)">▶</button>
      <button class="action-btn" onclick="avGoTo(avMoves.length)">▶|</button>
    </div>
    <button id="av-var-back" class="action-btn"
            style="display:none;margin-top:4px;width:100%;color:#f0a830;border-color:#5a4015;"
            onclick="avClearVariation()">← Back to main line</button>
    <div id="av-move-list"></div>
    <div id="av-lines-panel"></div>
  </div>
</div><!-- #analyze-section -->

<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://unpkg.com/@chrisoakman/chessboardjs@1.0.0/dist/chessboard-1.0.0.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/chess.js/0.10.3/chess.min.js"></script>
<script>
let board, game;
let botColor, humanColor;
let pollInterval, clockInterval;
let gameOver    = false;
let premove     = null;   // {from, to} queued during bot's turn
let selSquare   = null;   // currently click-selected square ("e2" etc.)
let _justDropped = false;  // suppress click event that fires right after a drag-drop
let _pendingSelect = null; // square to re-select after snapback re-renders the board
let _lastData    = null;  // most recent server state — re-applied after board re-renders

// ── Helpers ────────────────────────────────────────────────────────────────

// chess.py integer square (0=a1 … 63=h8) → algebraic string
function sqToAlg(n) { return 'abcdefgh'[n % 8] + (Math.floor(n / 8) + 1); }

// Walk up from a clicked DOM element to find the enclosing ".square-XY" name
function squareFromEl(el) {
  for (let i = 0; i < 5 && el && el.id !== 'board'; i++, el = el.parentElement) {
    const m = (el.className || '').match(/square-([a-h][1-8])/);
    if (m) return m[1];
  }
  return null;
}

// Is pieceCode ('wP', 'bN' …) one of the human player's pieces?
function isOwnPiece(pieceCode) {
  if (!pieceCode) return false;
  return humanColor === 'white' ? pieceCode[0] === 'w' : pieceCode[0] === 'b';
}

// ── Legal-move indicators ──────────────────────────────────────────────────

function showLegalDots(square) {
  let moves;
  if (game.turn() === humanColor[0]) {
    moves = game.moves({ square, verbose: true });
  } else {
    // Bot's turn: swap active colour in a temp game so we can see premove options.
    try {
      const parts = game.fen().split(' ');
      parts[1] = humanColor[0];
      parts[3] = '-';  // clear en-passant to avoid FEN validation errors
      const tmp = new Chess(parts.join(' '));
      moves = tmp ? tmp.moves({ square, verbose: true }) : [];
    } catch(e) { moves = []; }
  }
  moves.forEach(mv => {
    const $sq = $(`#board .square-${mv.to}`);
    const occupied = !!game.get(mv.to);
    $sq.append(occupied ? '<div class="legal-ring"></div>'
                        : '<div class="legal-dot"></div>');
  });
}

function clearLegalDots() { $('#board .legal-dot, #board .legal-ring').remove(); }

// ── Square-colour highlights ───────────────────────────────────────────────

function highlightLastMove(lm) {
  $('#board .sq-last').removeClass('sq-last');
  if (!lm) return;
  $(`#board .square-${sqToAlg(lm[0])}`).addClass('sq-last');
  $(`#board .square-${sqToAlg(lm[1])}`).addClass('sq-last');
}

function highlightCheck(inCheck) {
  $('#board .sq-check').removeClass('sq-check');
  if (!inCheck) return;
  const color = game.turn();
  game.board().forEach((row, r) => row.forEach((p, f) => {
    if (p && p.type === 'k' && p.color === color)
      $(`#board .square-${'abcdefgh'[f]}${8 - r}`).addClass('sq-check');
  }));
}

function applyHighlights(data) {
  if (data) _lastData = data; else data = _lastData;
  if (!data) return;
  highlightLastMove(data.last_move);
  highlightCheck(data.in_check);
}

// ── Click-to-move selection ────────────────────────────────────────────────

function selectSquare(sq) {
  clearSelection();
  selSquare = sq;
  $(`#board .square-${sq}`).addClass('sq-sel');
  showLegalDots(sq);
}

function clearSelection() {
  clearLegalDots();
  $('#board .sq-sel').removeClass('sq-sel');
  selSquare = null;
}

// ── Premove ────────────────────────────────────────────────────────────────

function setPremove(from, to) {
  clearPremove();
  premove = { from, to };
  $(`#board .square-${from}`).addClass('sq-premove');
  $(`#board .square-${to}`).addClass('sq-premove');
}

function clearPremove() {
  premove = null;
  $('#board .sq-premove').removeClass('sq-premove');
}

// ── Drag callbacks ─────────────────────────────────────────────────────────

function onDragStart(source, piece) {
  if (gameOver) return false;
  if (humanColor === 'white' && piece[0] === 'b') return false;
  if (humanColor === 'black' && piece[0] === 'w') return false;
  document.body.classList.add('is-dragging');
  clearSelection();
  if (game.turn() === humanColor[0]) {
    showLegalDots(source);
    $(`#board .square-${source}`).addClass('sq-sel');
  }
}

function onDrop(source, target) {
  document.body.classList.remove('is-dragging');

  // Prevent the click event that fires right after mouseup
  _justDropped = true;
  setTimeout(() => { _justDropped = false; }, 80);

  if (gameOver) { clearLegalDots(); $('#board .sq-sel').removeClass('sq-sel'); return 'snapback'; }

  if (source === target) {
    // Click-release: dots and sq-sel are already showing from onDragStart — keep them.
    // Just lock in the logical selection so destination clicks work immediately.
    const pieceObj = game.get(source);
    if (pieceObj && isOwnPiece(pieceObj.color + pieceObj.type.toUpperCase())) {
      selSquare = source;
      _pendingSelect = source; // re-apply after onSnapEnd in case board re-renders
    } else {
      clearLegalDots();
      $('#board .sq-sel').removeClass('sq-sel');
    }
    return 'snapback';
  }

  clearLegalDots();
  $('#board .sq-sel').removeClass('sq-sel');

  if (game.turn() !== humanColor[0]) {
    // Bot's turn → queue premove
    setPremove(source, target);
    document.getElementById('status').textContent = 'Premove queued — waiting for bot…';
    return;
  }

  clearPremove();
  const move = game.move({ from: source, to: target, promotion: 'q' });
  if (move === null) return 'snapback';

  document.getElementById('status').textContent = 'Alan Dai is thinking…';
  submitMove(move.from + move.to + (move.promotion || ''));
}

function onSnapEnd() {
  document.body.classList.remove('is-dragging');
  if (_pendingSelect) {
    // Click-release on same square: position unchanged, just restore visual selection
    clearLegalDots();
    const sq = _pendingSelect;
    _pendingSelect = null;
    selectSquare(sq);
  } else {
    clearLegalDots();
    if (!premove) { board.position(game.fen()); applyHighlights(null); }
  }
}

// ── Click-to-move ──────────────────────────────────────────────────────────
// Use capture phase so the handler fires before chessboard.js can stop propagation.
// Wired up once after the board div exists; survives board.destroy()/reinit.

function _boardClickHandler(e) {
  if (_justDropped || gameOver) return;

  const sq = squareFromEl(e.target);
  if (!sq) { clearSelection(); return; }

  const pieceObj  = game.get(sq);
  const pieceCode = pieceObj ? (pieceObj.color + pieceObj.type.toUpperCase()) : null;
  const own       = isOwnPiece(pieceCode);

  if (selSquare) {
    if (selSquare === sq) { clearSelection(); return; }

    if (game.turn() !== humanColor[0]) {
      if (own) { clearSelection(); selectSquare(sq); }
      else {
        setPremove(selSquare, sq);
        clearSelection();
        document.getElementById('status').textContent = 'Premove queued — waiting for bot…';
      }
      return;
    }

    const mv = game.move({ from: selSquare, to: sq, promotion: 'q' });
    if (mv) {
      clearSelection();
      board.position(game.fen()); applyHighlights(null);
      document.getElementById('status').textContent = 'Alan Dai is thinking…';
      submitMove(mv.from + mv.to + (mv.promotion || ''));
    } else if (own) {
      clearSelection(); selectSquare(sq);
    } else {
      clearSelection();
    }
  } else {
    if (own) selectSquare(sq);
  }
}

document.getElementById('board').addEventListener('click', _boardClickHandler, true);

// ── Server communication ───────────────────────────────────────────────────

function submitMove(uci) {
  fetch('/move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uci })
  }).then(r => r.json()).then(handleStateUpdate);
}

function triggerBotMove() {
  document.getElementById('status').textContent = 'Alan Dai is thinking…';
  fetch('/bot_move', { method: 'POST' }).then(r => r.json()).then(handleStateUpdate);
}

function handleStateUpdate(data) {
  if (data.error) {
    document.getElementById('status').textContent = data.error;
    if (data.error === 'No game in progress.') {
      gameOver = true;
      clearInterval(pollInterval); clearInterval(clockInterval);
      setGameButtons(true);
    }
    return;
  }
  game.load(data.fen);
  board.position(data.fen, true);
  updateClocks(data);
  updateMoveList(data.moves);
  applyHighlights(data);

  if (data.over) {
    gameOver = true;
    clearPremove(); clearSelection();
    clearInterval(pollInterval); clearInterval(clockInterval);
    document.getElementById('status').textContent = data.result;
    setGameButtons(true);
    return;
  }
  if (data.turn === botColor) {
    triggerBotMove();
  } else if (premove) {
    const pm = premove;
    clearPremove();
    const mv = game.move({ from: pm.from, to: pm.to, promotion: 'q' });
    if (mv === null) {
      board.position(game.fen()); applyHighlights(null);
      document.getElementById('status').textContent = 'Your turn';
    } else {
      board.position(game.fen(), true);
      document.getElementById('status').textContent = 'Alan Dai is thinking…';
      submitMove(pm.from + pm.to + (mv.promotion || ''));
    }
  } else {
    document.getElementById('status').textContent = 'Your turn';
  }
}

// ── Setup panel ────────────────────────────────────────────────────────────

document.querySelectorAll('.tc-btn').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.tc-btn').forEach(x => x.classList.remove('selected'));
    b.classList.add('selected');
  });
});
document.querySelectorAll('.color-btn').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.color-btn').forEach(x => x.classList.remove('selected'));
    b.classList.add('selected');
  });
});

let _lastTc, _lastBotColor;  // remembered for rematch

document.getElementById('start-btn').addEventListener('click', startGame);

document.getElementById('abort-btn').addEventListener('click', () => {
  fetch('/abort', { method: 'POST' }).then(r => r.json()).then(data => {
    if (data.error) { document.getElementById('status').textContent = data.error; return; }
    handleStateUpdate(data);
  });
});

document.getElementById('resign-btn').addEventListener('click', () => {
  if (!confirm('Resign this game?')) return;
  fetch('/resign', { method: 'POST' }).then(r => r.json()).then(handleStateUpdate);
});

document.getElementById('rematch-btn').addEventListener('click', () => {
  startGameWith(_lastTc, _lastBotColor, true);
});

document.getElementById('review-btn').addEventListener('click', () => {
  closeGameViewer();
  const pgn = game ? game.pgn() : '';
  if (!pgn) return;
  switchTab('analyze');
  openGameViewer({ pgn, user_color: humanColor, opponent: 'Alan Dai', opp_rating: '', result: '', time_class: _lastTc || 'blitz' });
});

document.getElementById('new-game-btn').addEventListener('click', () => {
  clearInterval(pollInterval);
  clearInterval(clockInterval);
  document.getElementById('game-panel').style.display = 'none';
  document.getElementById('setup-panel').style.display = 'flex';
});

function startGame() {
  const tc    = document.querySelector('.tc-btn.selected').dataset.tc;
  const color = document.querySelector('.color-btn.selected').dataset.color;
  startGameWith(tc, color === 'white' ? 'black' : 'white', false);
}

function startGameWith(tc, botSide, isRematch) {
  humanColor = botSide === 'white' ? 'black' : 'white';
  botColor   = botSide;
  _lastTc       = tc;
  _lastBotColor = botSide;

  fetch('/new_game', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tc, bot_color: botSide, is_rematch: isRematch })
  }).then(r => r.json()).then(data => {
    document.getElementById('setup-panel').style.display = 'none';
    document.getElementById('game-panel').style.display  = 'flex';
    initBoard(data);
    if (data.turn === botColor) triggerBotMove();
    startClockTick();
    pollInterval = setInterval(syncState, 1000);
  });
}

function initBoard(data) {
  game = new Chess();
  gameOver = false;
  clearPremove();
  clearSelection();

  document.getElementById('top-name').textContent    = 'Alan Dai';
  document.getElementById('top-sub').textContent     = botColor === 'black' ? 'Black' : 'White';
  document.getElementById('top-avatar').textContent  = botColor === 'black' ? '♟' : '♙';
  document.getElementById('bottom-name').textContent = 'You';
  document.getElementById('bottom-sub').textContent  = humanColor === 'white' ? 'White' : 'Black';
  document.getElementById('bottom-avatar').textContent = humanColor === 'white' ? '♙' : '♟';

  if (board) board.destroy();
  board = Chessboard('board', {
    position:        data.fen,
    orientation:     humanColor,
    draggable:       true,
    showNotation:    true,
    moveSpeed:       180,
    snapSpeed:       70,
    snapbackSpeed:   80,
    onDragStart,
    onDrop,
    onSnapEnd,
    onMoveEnd:       () => applyHighlights(null),
    pieceTheme:      'https://chessboardjs.com/img/chesspieces/wikipedia/{piece}.png',
  });

  updateClocks(data);
  updateMoveList(data.moves);
  applyHighlights(data);
  setGameButtons(false);
  document.getElementById('status').textContent = data.turn === humanColor ? 'Your turn' : 'Alan Dai is thinking…';
}

function setGameButtons(over) {
  document.getElementById('abort-btn').style.display    = over ? 'none' : '';
  document.getElementById('resign-btn').style.display   = over ? 'none' : '';
  document.getElementById('rematch-btn').style.display  = over ? '' : 'none';
  document.getElementById('review-btn').style.display   = over ? '' : 'none';
  document.getElementById('new-game-btn').style.display = over ? '' : 'none';
  if (!over && game) {
    document.getElementById('abort-btn').disabled = game.history().length > 4;
  }
}

// ── Poll / clock ───────────────────────────────────────────────────────────

function syncState() {
  if (gameOver) return;
  fetch('/state').then(r => r.json()).then(data => {
    updateClocks(data);
    if (data.over && !gameOver) {
      gameOver = true;
      document.getElementById('status').textContent = data.result;
      setGameButtons(true);
      clearInterval(pollInterval); clearInterval(clockInterval);
    }
  });
}

let _botClock, _humanClock, _lastTick, _activeSide;
function startClockTick() {
  clearInterval(clockInterval);
  clockInterval = setInterval(() => {
    if (gameOver) return;
    const now = Date.now() / 1000;
    const elapsed = now - _lastTick;
    _lastTick = now;
    if (_activeSide === botColor)        _botClock   = Math.max(0, _botClock   - elapsed);
    else if (_activeSide === humanColor) _humanClock = Math.max(0, _humanClock - elapsed);
    renderClocks();
  }, 100);
}

function updateClocks(data) {
  _botClock   = data.bot_clock;
  _humanClock = data.human_clock;
  _activeSide = data.turn;
  _lastTick   = Date.now() / 1000;
  renderClocks();
}

function renderClocks() {
  document.getElementById('top-time').textContent    = formatTime(_botClock);
  document.getElementById('bottom-time').textContent = formatTime(_humanClock);

  const topActive    = _activeSide === botColor;
  const bottomActive = _activeSide === humanColor;
  document.getElementById('top-strip').classList.toggle('active',    topActive);
  document.getElementById('bottom-strip').classList.toggle('active', bottomActive);

  // Low-time warning: < 30 s on active player's clock
  document.getElementById('top-strip').classList.toggle('low-time',    topActive    && _botClock   < 30);
  document.getElementById('bottom-strip').classList.toggle('low-time', bottomActive && _humanClock < 30);
}

function formatTime(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

function updateMoveList(moves) {
  const el = document.getElementById('move-list');
  if (!moves.length) {
    el.innerHTML = '<span class="move-num" style="color:#555">No moves yet</span>';
    return;
  }
  let html = '';
  for (let i = 0; i < moves.length; i += 2) {
    const num = Math.floor(i / 2) + 1;
    const w = moves[i]   ? `<span class="move-san">${moves[i]}</span>`   : '';
    const b = moves[i+1] ? `<span class="move-san">${moves[i+1]}</span>` : '';
    html += `<span class="move-num">${num}.</span>${w} ${b} `;
  }
  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;
}

// ── Tab switching ──────────────────────────────────────────────────────────

function switchTab(tab) {
  document.getElementById('play-section').style.display    = tab === 'play'    ? 'flex' : 'none';
  document.getElementById('analyze-section').style.display = tab === 'analyze' ? 'flex' : 'none';
  document.getElementById('tab-play').classList.toggle('active', tab === 'play');
}

// ── Analysis: game viewer ──────────────────────────────────────────────────

let avBoard        = null;
let avViewer       = new Chess();  // chess.js game used for position replay
let avMoves        = [];           // main-line moves from the PGN
let avIdx          = 0;            // current position in the main line (0 = start)
let avVariation    = [];           // moves made by the user beyond avIdx (exploration)
let avSelSquare    = null;         // click-selected square in analysis board
let avEvalDebounce = null;

function openGameViewer(g) {

  // Parse PGN
  const loader = new Chess();
  if (!loader.load_pgn(g.pgn)) { alert('Could not parse PGN.'); return; }
  avMoves = loader.history({ verbose: true });

  // Reset replay game to start
  avViewer = new Chess();
  avIdx    = 0;

  // Player labels
  const botTop = g.user_color === 'black';
  document.getElementById('av-top-name').textContent    = botTop ? 'You (yuandan)' : g.opponent;
  document.getElementById('av-top-sub').textContent     = botTop ? 'Black' : 'White';
  document.getElementById('av-top-avatar').textContent  = botTop ? '♟' : '♙';
  document.getElementById('av-bottom-name').textContent = botTop ? g.opponent : 'You (yuandan)';
  document.getElementById('av-bottom-sub').textContent  = botTop ? 'White' : 'Black';
  document.getElementById('av-bottom-avatar').textContent = botTop ? '♙' : '♟';

  document.getElementById('av-game-title').textContent =
    `${g.result === 'W' ? '✓' : g.result === 'L' ? '✗' : '½'} vs ${g.opponent} · ${g.opening}`;

  const orient = g.user_color;
  if (avBoard) avBoard.destroy();
  avBoard = Chessboard('av-board', {
    position:        'start',
    orientation:     orient,
    draggable:       true,
    showNotation:    true,
    moveSpeed:       180,
    snapSpeed:       70,
    snapbackSpeed:   80,
    onDragStart:     () => { document.body.classList.add('is-dragging'); return true; },
    onDrop:          avOnDrop,
    onSnapEnd:       avOnSnapEnd,
    pieceTheme:      'https://chessboardjs.com/img/chesspieces/wikipedia/{piece}.png',
  });

  renderAvMoveList();
  fetchLines(avViewer.fen());
}

function closeGameViewer() {
  document.getElementById('av-lines-panel').innerHTML = '';
  avVariation = []; avSelSquare = null;
  if (avBoard) { avBoard.destroy(); avBoard = null; }
}

function avGoTo(idx) {
  idx = Math.max(0, Math.min(avMoves.length, idx));
  avVariation = [];
  avClearAvSel();
  document.getElementById('av-var-back').style.display = 'none';
  avViewer = new Chess();
  for (let i = 0; i < idx; i++) avViewer.move(avMoves[i]);
  avIdx = idx;
  avBoard.position(avViewer.fen(), false);
  renderAvMoveList();
  scheduleLines(avViewer.fen());
}

function avStep(delta) { avGoTo(avIdx + delta); }

function avStepBack() {
  if (avVariation.length > 0) {
    avVariation.pop();
    avViewer.undo();
    avClearAvSel();
    avBoard.position(avViewer.fen(), false);
    scheduleLines(avViewer.fen());
    if (avVariation.length === 0)
      document.getElementById('av-var-back').style.display = 'none';
  } else {
    avGoTo(avIdx - 1);
  }
}

function avClearVariation() {
  avVariation = [];
  avClearAvSel();
  document.getElementById('av-var-back').style.display = 'none';
  avViewer = new Chess();
  for (let i = 0; i < avIdx; i++) avViewer.move(avMoves[i]);
  avBoard.position(avViewer.fen(), false);
  scheduleLines(avViewer.fen());
}

// ── Analysis drag-and-drop ─────────────────────────────────────────────────

function avOnDrop(source, target) {
  document.body.classList.remove('is-dragging');
  if (source === target) return 'snapback';
  avClearAvSel();
  const move = avViewer.move({ from: source, to: target, promotion: 'q' });
  if (move === null) return 'snapback';
  avVariation.push(move);
  scheduleLines(avViewer.fen());
  document.getElementById('av-var-back').style.display = '';
}

function avOnSnapEnd() {
  document.body.classList.remove('is-dragging');
  avBoard.position(avViewer.fen());
}

// ── Analysis click-to-move ─────────────────────────────────────────────────

function avSelectSq(sq) {
  avClearAvSel();
  avSelSquare = sq;
  $(`#av-board .square-${sq}`).addClass('sq-sel');
  avViewer.moves({ square: sq, verbose: true }).forEach(mv => {
    const occupied = !!avViewer.get(mv.to);
    $(`#av-board .square-${mv.to}`).append(
      occupied ? '<div class="legal-ring"></div>' : '<div class="legal-dot"></div>'
    );
  });
}

function avClearAvSel() {
  $('#av-board .sq-sel').removeClass('sq-sel');
  $('#av-board .legal-dot, #av-board .legal-ring').remove();
  avSelSquare = null;
}

$(document).on('click', '#av-board', function(e) {
  if (!avBoard) return;
  const sq = squareFromEl(e.target);
  if (!sq) { avClearAvSel(); return; }

  if (avSelSquare) {
    if (avSelSquare === sq) { avClearAvSel(); return; }
    const move = avViewer.move({ from: avSelSquare, to: sq, promotion: 'q' });
    avClearAvSel();
    if (move) {
      avVariation.push(move);
      avBoard.position(avViewer.fen(), false);
      scheduleLines(avViewer.fen());
      document.getElementById('av-var-back').style.display = '';
    } else if (avViewer.get(sq)) {
      avSelectSq(sq);
    }
  } else {
    if (avViewer.get(sq)) avSelectSq(sq);
  }
});

// Keyboard navigation
document.addEventListener('keydown', e => {
  const viewing = document.getElementById('av-game-view').style.display !== 'none'
                  && document.getElementById('analyze-section').style.display !== 'none';
  if (!viewing) return;
  if (e.key === 'ArrowLeft')  avStepBack();
  if (e.key === 'ArrowRight') avStep(1);
  if (e.key === 'ArrowUp')    avGoTo(0);
  if (e.key === 'ArrowDown')  avGoTo(avMoves.length);
});

function renderAvMoveList() {
  let html = '';
  for (let i = 0; i < avMoves.length; i += 2) {
    const num = Math.floor(i / 2) + 1;
    const wCls = (avIdx === i + 1) ? 'av-move current' : 'av-move';
    const bCls = (avIdx === i + 2) ? 'av-move current' : 'av-move';
    const w = `<span class="${wCls}" onclick="avGoTo(${i+1})">${avMoves[i].san}</span>`;
    const b = avMoves[i+1]
      ? `<span class="${bCls}" onclick="avGoTo(${i+2})">${avMoves[i+1].san}</span>`
      : '';
    html += `<span class="move-num">${num}.</span>${w} ${b} `;
  }
  const el = document.getElementById('av-move-list');
  el.innerHTML = html || '<span style="color:#555">Start of game</span>';
  // Scroll current move into view
  const cur = el.querySelector('.current');
  if (cur) cur.scrollIntoView({ block: 'nearest' });
}

// ── Eval bar ───────────────────────────────────────────────────────────────

function scheduleEval(fen) {
  clearTimeout(avEvalDebounce);
  document.getElementById('av-eval-label').textContent = '…';
  avEvalDebounce = setTimeout(() => fetchEval(fen), 150);
}

function fetchEval(fen) {
  fetch('/api/eval', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fen })
  }).then(r => r.json()).then(data => {
    if (data.error) return;
    updateEvalBar(data.cp, data.is_mate, data.mate);
  }).catch(() => {});
}

function updateEvalBar(cp, isMate, mate) {
  // Sigmoid: white% = 50 + 50*tanh(cp/300)
  const pct  = isMate
    ? (cp > 0 ? 100 : 0)
    : Math.max(2, Math.min(98, 50 + 50 * Math.tanh(cp / 300)));
  document.getElementById('av-eval-fill').style.width = pct + '%';

  let label;
  if (isMate) {
    label = mate > 0 ? `M${mate}` : `M${-mate}`;
  } else {
    const sign = cp >= 0 ? '+' : '';
    label = `${sign}${(cp / 100).toFixed(1)}`;
  }
  document.getElementById('av-eval-label').textContent = label;
}

// ── Engine lines ───────────────────────────────────────────────────────────

let avLinesDebounce = null;

function scheduleLines(fen) {
  clearTimeout(avLinesDebounce);
  avLinesDebounce = setTimeout(() => fetchLines(fen), 60);
}

function fetchLines(fen) {
  fetch('/api/lines', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fen })
  }).then(r => r.json()).then(data => {
    if (data.error || !data.lines) {
      document.getElementById('av-lines-panel').innerHTML = '';
      return;
    }
    // Update eval bar from the best line — avoids a separate /api/eval round-trip
    updateEvalBar(data.eval_cp, data.eval_is_mate, data.eval_mate);
    updateEngineLines(data.lines);
  }).catch(() => { document.getElementById('av-lines-panel').innerHTML = ''; });
}

function updateEngineLines(lines) {
  const panel = document.getElementById('av-lines-panel');
  if (!lines || !lines.length) { panel.innerHTML = ''; return; }
  panel.innerHTML = lines.map(line => {
    const cp = line.cp, isMate = line.is_mate;
    let label, cls;
    if (isMate) {
      label = line.mate > 0 ? `M${line.mate}` : `M${-line.mate}`;
      cls   = line.mate > 0 ? 'ev-pos' : 'ev-neg';
    } else {
      const sign = cp >= 0 ? '+' : '';
      label = `${sign}${(cp / 100).toFixed(2)}`;
      cls   = cp > 15 ? 'ev-pos' : (cp < -15 ? 'ev-neg' : 'ev-eq');
    }
    const pvText = line.pv.join(' ');
    return `<div class="engine-line">
      <span class="ev-badge ${cls}">${label}</span>
      <span class="ev-pv">${pvText}</span>
    </div>`;
  }).join('');
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/new_game", methods=["POST"])
def new_game():
    data = request.json
    tc         = data.get("tc", "blitz")
    bot_color  = data.get("bot_color", "black")
    is_rematch = bool(data.get("is_rematch", False))
    if tc not in TIME_CONTROLS:
        tc = "blitz"
    with _lock:
        state.clear()
        s = _new_state(tc, bot_color)
        s["is_rematch"] = is_rematch
        state.update(s)
    return jsonify(_board_json(state))


@app.route("/resign", methods=["POST"])
def resign():
    with _lock:
        if state.get("over"):
            return jsonify({"error": "Game is already over."})
        state["over"]   = True
        state["result"] = "You resigned — Alan Dai wins!"
        return jsonify(_board_json(state))


@app.route("/abort", methods=["POST"])
def abort():
    with _lock:
        if state.get("over"):
            return jsonify({"error": "Game is already over."})
        board: chess.Board = state["board"]
        if board.fullmove_number > 2:
            return jsonify({"error": "Too late to abort — use Resign instead."}), 400
        state["over"]   = True
        state["result"] = "Game aborted."
        return jsonify(_board_json(state))


@app.route("/move", methods=["POST"])
def human_move():
    uci = request.json.get("uci", "")
    with _lock:
        if "board" not in state:
            return jsonify({"error": "No game in progress."}), 400
        if state.get("over"):
            return jsonify({"error": "Game is over."})
        board: chess.Board = state["board"]
        if board.turn == state["bot_color"]:
            return jsonify({"error": "Not your turn."})
        try:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                # Try with queen promotion
                move = chess.Move.from_uci(uci[:4] + "q") if len(uci) == 4 else move
            if move not in board.legal_moves:
                return jsonify({"error": "Illegal move."}), 400
        except Exception:
            return jsonify({"error": "Bad move format."}), 400

        _tick_clock(state)
        san = board.san(move)
        board.push(move)
        state["moves"].append(san)
        state["last_move"] = [move.from_square, move.to_square]
        _check_game_over(state)
        return jsonify(_board_json(state))


@app.route("/bot_move", methods=["POST"])
def bot_move():
    # Phase 1: compute the move quickly, then release the lock so the UI
    # can keep polling /state (and ticking the clock) during the think delay.
    with _lock:
        if "board" not in state:
            return jsonify({"error": "No game in progress."}), 400
        if state.get("over"):
            return jsonify(_board_json(state))
        board: chess.Board = state["board"]
        if board.turn != state["bot_color"]:
            return jsonify({"error": "Not bot's turn."})

        _tick_clock(state)
        engine: ChessBotEngine = state["engine"]
        move = engine.get_move(board, state["bot_clock"],
                               is_rematch=state["is_rematch"])
        think_delay = state["think_timer"].get_delay(
            board,
            engine.last_gap_cp,
            move,
            state["bot_clock"],
            from_book=engine.last_from_book,
        )

    # Phase 2: simulate thinking — lock released so /state stays responsive
    # and _tick_clock() in get_state() naturally deducts time from bot's clock.
    time.sleep(think_delay)

    # Phase 3: apply the move.
    with _lock:
        if state.get("over"):
            return jsonify(_board_json(state))
        _tick_clock(state)   # accounts for the think_delay elapsed above
        san = state["board"].san(move)
        state["board"].push(move)
        state["moves"].append(san)
        state["last_move"] = [move.from_square, move.to_square]
        _check_game_over(state)
        return jsonify(_board_json(state))


@app.route("/state")
def get_state():
    with _lock:
        if not state:
            return jsonify({"error": "No game in progress."})
        _tick_clock(state)
        _check_game_over(state)
        return jsonify(_board_json(state))


@app.route("/debug")
def debug():
    info = {
        "username": USERNAME,
        "stockfish_path": STOCKFISH_PATH,
        "stockfish_exists": os.path.isfile(STOCKFISH_PATH) if STOCKFISH_PATH else False,
        "stockfish_which": shutil.which("stockfish"),
        "model_path": MODEL_PATH,
        "model_exists": os.path.isfile(MODEL_PATH),
    }
    with _lock:
        if state:
            engine = state.get("engine")
            info["engine_loaded"] = engine is not None
            if engine:
                info["stockfish_enabled"] = engine.stockfish is not None
                info["last_gap_cp"] = engine.last_gap_cp
                info["device"] = str(engine.device)
        else:
            info["engine_loaded"] = False
    return jsonify(info)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"Open http://localhost:{port} in your browser")
    app.run(debug=False, host="0.0.0.0", port=port)
