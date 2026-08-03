import json
import os
import shutil
import time
import threading
import atexit
import logging
import secrets
from pathlib import Path
from collections import defaultdict, deque
from urllib.parse import urlsplit
from dotenv import load_dotenv
load_dotenv()
import datetime
import ipaddress
import chess
import chess.engine
import chess.pgn
from flask import Flask, jsonify, request, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from bot.engine import ChessBotEngine, load_model
from bot.think_timer import ThinkTimer
from bot.model_contract import resolve_release_paths

app = Flask(__name__)
logger = logging.getLogger(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
if os.environ.get("TRUST_PROXY_HEADERS", "0") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

USERNAME         = os.environ.get("CHESS_USERNAME", os.environ.get("USERNAME", "yuandan"))
MODEL_PATH       = os.environ.get("MODEL_PATH",       "best_model.onnx")
# Older local .env files pointed serving at the PyTorch checkpoint. Serving only
# accepts ONNX, so recover to the documented deployment artifact automatically.
if Path(MODEL_PATH).suffix == ".pt":
    logger.warning("Ignoring legacy MODEL_PATH=%s; using best_model.onnx", MODEL_PATH)
    MODEL_PATH = "best_model.onnx"
MANIFEST_PATH    = os.environ.get("MODEL_MANIFEST_PATH", "best_model.manifest.json")
RELEASE_PATH     = os.environ.get("MODEL_RELEASE_PATH", "best_model.release.json")
BOOK_PATH        = os.environ.get("BOOK_PATH",        "opening_book.json")
_sf_env = os.environ.get("STOCKFISH_PATH", "")
STOCKFISH_PATH   = (
    _sf_env if _sf_env and os.path.isfile(_sf_env)
    else shutil.which("stockfish") or "/usr/games/stockfish"
)

try:
    MAX_ARTIFICIAL_THINK_DELAY = max(
        0.0, float(os.environ.get("MAX_ARTIFICIAL_THINK_DELAY", "0"))
    )
except ValueError:
    MAX_ARTIFICIAL_THINK_DELAY = 0.0

try:
    MODEL_PATH, MANIFEST_PATH = map(
        str, resolve_release_paths(MODEL_PATH, MANIFEST_PATH, RELEASE_PATH)
    )
    MODEL_SESSION, MODEL_CONTRACT = load_model(
        MODEL_PATH, MANIFEST_PATH, expected_username=USERNAME
    )
    MODEL_ERROR = None
except Exception as exc:
    MODEL_SESSION = None
    MODEL_CONTRACT = None
    MODEL_ERROR = str(exc)
    logger.exception("Deployment model failed startup validation")

try:
    _startup_sf = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    _startup_sf.ping()
    _startup_sf.quit()
    STOCKFISH_READY = True
    STOCKFISH_ERROR = None
except Exception as exc:
    STOCKFISH_READY = False
    STOCKFISH_ERROR = str(exc)
    logger.warning("Stockfish failed startup validation: %s", exc)

TIME_CONTROLS = {
    "bullet": 60,
    "blitz":  180,
    "rapid":  600,
}

# Isolated in-memory games. The opaque token is stored in an HttpOnly cookie and
# never accepted from request JSON, preventing one client from selecting another
# client's game.
GAME_COOKIE    = "chessbot_game"
MAX_ACTIVE_GAMES = int(os.environ.get("MAX_ACTIVE_GAMES", "8"))
MAX_GAMES_PER_IP = int(os.environ.get("MAX_GAMES_PER_IP", "2"))
GAME_IDLE_TTL = int(os.environ.get("GAME_IDLE_TTL", str(30 * 60)))
CAPACITY_EVICTION_IDLE = int(os.environ.get("CAPACITY_EVICTION_IDLE", str(5 * 60)))
FINISHED_GAME_TTL = int(os.environ.get("FINISHED_GAME_TTL", str(15 * 60)))
_games_lock    = threading.Lock()
_analysis_lock = threading.Lock()
_games: dict[str, dict] = {}
_reserved_games = 0
_reserved_by_owner: dict[str, int] = defaultdict(int)
_creating_tokens: set[str] = set()
_rate_lock = threading.Lock()
_rate_events: dict[tuple[str, str], deque] = defaultdict(deque)
_shutdown_event = threading.Event()
_stockfish_probe_lock = threading.Lock()
_stockfish_probe_at = 0.0
_stockfish_probe_ok = STOCKFISH_READY
LOCAL_GAMES_PATH = Path(os.environ.get("LOCAL_GAMES_PATH", "local_games.json"))
MAX_SAVED_GAMES = 6
_saved_games_lock = threading.Lock()
ANALYSIS_RATE_LIMIT = max(10, int(os.environ.get("ANALYSIS_RATE_LIMIT", "30")))


def _new_state(
    tc: str,
    bot_color: str,
    total_seconds: float | None = None,
    increment_seconds: float = 0.0,
) -> dict:
    bot_side = chess.WHITE if bot_color == "white" else chess.BLACK
    engine = ChessBotEngine(
        time_control=tc,
        model_path=MODEL_PATH,
        username=USERNAME,
        opening_book_path=BOOK_PATH,
        stockfish_path=STOCKFISH_PATH,
        inference_session=MODEL_SESSION,
    )
    total = float(total_seconds if total_seconds is not None else TIME_CONTROLS[tc])
    return {
        "lock":        threading.RLock(),
        "engine":      engine,
        "think_timer": ThinkTimer(tc),
        "board":       chess.Board(),
        "bot_color":   bot_side,
        "bot_clock":   total,
        "human_clock": total,
        "last_tick":   time.monotonic(),
        "over":        False,
        "result":      None,
        "is_rematch":  False,
        "moves":       [],          # list of SAN strings
        "last_move":   None,        # (from_sq, to_sq) for highlighting
        "version":     0,
        "bot_busy":    False,
        "increment":   float(increment_seconds),
        "snapshots":   [],
        "decision_source": "—",
        "time_control": tc,
        "saved": False,
        "last_access": time.monotonic(),
    }


def _request_data() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _client_owner() -> str:
    forwarded = request.headers.get("CF-Connecting-IP", "").strip()
    if forwarded:
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return request.remote_addr or "unknown"


def _same_origin() -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return True
    supplied = urlsplit(origin)
    expected = urlsplit(request.host_url)
    return (
        supplied.scheme == expected.scheme
        and supplied.netloc.lower() == expected.netloc.lower()
    )


def _rate_allowed(bucket: str, limit: int, window_seconds: float) -> bool:
    now = time.monotonic()
    key = (bucket, request.remote_addr or "unknown")
    with _rate_lock:
        if len(_rate_events) > 4096:
            oldest = sorted(
                _rate_events,
                key=lambda item: _rate_events[item][-1] if _rate_events[item] else 0,
            )[:1024]
            for stale_key in oldest:
                _rate_events.pop(stale_key, None)
        events = _rate_events[key]
        while events and now - events[0] >= window_seconds:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True


@app.before_request
def _request_guards():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _same_origin():
        return jsonify({"error": "Cross-origin request rejected."}), 403
    limits = {
        "new_game": (10, 60 * 60),
        "api_games": (20, 60),
        "api_local_games": (60, 60),
        "api_eval": (ANALYSIS_RATE_LIMIT, 60),
        "api_lines": (ANALYSIS_RATE_LIMIT, 60),
    }
    rule = limits.get(request.endpoint)
    if rule and not _rate_allowed(request.endpoint, *rule):
        response = jsonify({
            "error": "Analysis is temporarily busy. Please wait a moment and retry."
        })
        response.status_code = 429
        response.headers["Retry-After"] = "2"
        return response


@app.after_request
def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; style-src-attr 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "object-src 'none'; form-action 'self'",
    )
    if request.path.startswith("/static/"):
        fingerprinted = "/chunks/" in request.path or any(
            segment in request.path for segment in ("/vendor/", "/pieces/")
        )
        response.headers["Cache-Control"] = (
            "public, max-age=604800, immutable"
            if fingerprinted else "public, max-age=3600, must-revalidate"
        )
    else:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _current_game():
    token = request.cookies.get(GAME_COOKIE, "")
    if not token:
        return None, None
    with _games_lock:
        game = _games.get(token)
        if game is not None:
            game["last_access"] = time.monotonic()
        return token, game


def _game_or_error():
    token, game = _current_game()
    if game is None:
        return token, None, (jsonify({"error": "No game in progress."}), 404)
    return token, game, None


def _prune_games():
    """Remove abandoned games and return them for cleanup outside the store lock."""
    now = time.monotonic()
    removed = []
    with _games_lock:
        for token, game in list(_games.items()):
            ttl = FINISHED_GAME_TTL if game["over"] else GAME_IDLE_TTL
            if now - game["last_access"] > ttl:
                removed.append(_games.pop(token))
        if len(_games) >= MAX_ACTIVE_GAMES:
            finished = [
                (token, game) for token, game in _games.items() if game["over"]
            ]
            if finished:
                token, _ = min(finished, key=lambda item: item[1]["last_access"])
                removed.append(_games.pop(token))
        if len(_games) >= MAX_ACTIVE_GAMES:
            inactive = [
                (token, game) for token, game in _games.items()
                if now - game["last_access"] >= CAPACITY_EVICTION_IDLE
            ]
            if inactive:
                token, _ = min(inactive, key=lambda item: item[1]["last_access"])
                removed.append(_games.pop(token))
    for game in removed:
        with game["lock"]:
            game["version"] += 1
            game["engine"].close()


def _reaper():
    while not _shutdown_event.wait(60):
        try:
            _prune_games()
        except Exception:
            logger.exception("Game reaper failed")


_reaper_thread = threading.Thread(
    target=_reaper, name="chessbot-game-reaper", daemon=True
)
_reaper_thread.start()


def _stockfish_is_ready() -> bool:
    global _stockfish_probe_at, _stockfish_probe_ok
    now = time.monotonic()
    with _stockfish_probe_lock:
        if now - _stockfish_probe_at < 10:
            return _stockfish_probe_ok
        engine = None
        try:
            engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
            engine.ping()
            _stockfish_probe_ok = True
        except Exception as exc:
            logger.warning("Stockfish readiness probe failed: %s", exc)
            _stockfish_probe_ok = False
        finally:
            if engine is not None:
                try:
                    engine.quit()
                except Exception:
                    logger.exception("Stockfish readiness probe cleanup failed")
        _stockfish_probe_at = now
        return _stockfish_probe_ok


def _version_matches(s: dict, data: dict) -> bool:
    expected = data.get("expected_version")
    return expected is None or (
        isinstance(expected, int) and not isinstance(expected, bool)
        and expected == s["version"]
    )


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
        "version":     s["version"],
        "bot_busy":    s["bot_busy"],
        "increment":   s.get("increment", 0),
        "decision_source": s.get("decision_source", "—"),
        "opening_name": _opening_name(s["moves"]),
    }


def _opening_name(moves: list[str]) -> str:
    prefix = " ".join(moves[:6])
    openings = (
        ("e4 e5 Nf3 Nc6 Bb5", "Ruy López"),
        ("e4 e5 Nf3 Nc6 Bc4", "Italian Game"),
        ("e4 c5", "Sicilian Defense"),
        ("e4 e6", "French Defense"),
        ("e4 c6", "Caro–Kann Defense"),
        ("e4 d5", "Scandinavian Defense"),
        ("d4 d5 c4", "Queen's Gambit"),
        ("d4 Nf6 c4 g6", "King's Indian Defense"),
        ("d4 Nf6 c4 e6 Nc3 Bb4", "Nimzo-Indian Defense"),
        ("Nf3", "Réti Opening"),
        ("c4", "English Opening"),
    )
    for line, name in openings:
        if prefix.startswith(line):
            return name
    return "Opening phase" if len(moves) < 16 else "Middlegame"


def _saved_games() -> list[dict]:
    try:
        data = json.loads(LOCAL_GAMES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        if len(data) > MAX_SAVED_GAMES:
            data = data[-MAX_SAVED_GAMES:]
            temporary = LOCAL_GAMES_PATH.with_suffix(LOCAL_GAMES_PATH.suffix + ".tmp")
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temporary.replace(LOCAL_GAMES_PATH)
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _game_pgn(s: dict) -> str:
    game = chess.pgn.Game()
    game.headers["Event"] = "Alan Dai local game"
    game.headers["Date"] = datetime.datetime.now().strftime("%Y.%m.%d")
    human_white = s["bot_color"] == chess.BLACK
    game.headers["White"] = "You" if human_white else "Alan Dai"
    game.headers["Black"] = "Alan Dai" if human_white else "You"
    result = s.get("result") or "*"
    game.headers["Result"] = (
        "1-0" if "you win" in result.lower() and human_white else
        "0-1" if "you win" in result.lower() else
        "0-1" if "alan dai wins" in result.lower() and human_white else
        "1-0" if "alan dai wins" in result.lower() else
        "1/2-1/2" if "draw" in result.lower() else "*"
    )
    board = game.board()
    node = game
    for san in s["moves"]:
        move = board.parse_san(san)
        node = node.add_variation(move)
        board.push(move)
    return str(game)


def _persist_finished_game(s: dict) -> None:
    if not s.get("over") or s.get("saved") or not s.get("moves"):
        return
    human_white = s["bot_color"] == chess.BLACK
    result_text = s.get("result") or "Game finished"
    lowered = result_text.lower()
    result = "D" if "draw" in lowered else (
        "W" if "you win" in lowered or "alan dai flagged" in lowered else "L"
    )
    record = {
        "id": secrets.token_urlsafe(10),
        "opponent": "Alan Dai",
        "result": result,
        "result_text": result_text,
        "time_class": s.get("time_control", "custom"),
        "date": datetime.datetime.now().strftime("%b %d"),
        "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pgn": _game_pgn(s),
        "opening": _opening_name(s["moves"]),
        "user_color": "white" if human_white else "black",
        "source": "local",
    }
    with _saved_games_lock:
        records = _saved_games()
        records.append(record)
        records = records[-MAX_SAVED_GAMES:]
        LOCAL_GAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = LOCAL_GAMES_PATH.with_suffix(LOCAL_GAMES_PATH.suffix + ".tmp")
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        temporary.replace(LOCAL_GAMES_PATH)
    s["saved"] = True


def _try_persist_finished_game(s: dict) -> None:
    """Keep gameplay available when optional local history cannot be written."""
    try:
        _persist_finished_game(s)
    except Exception:
        logger.exception("Unable to persist finished game")


def _save_snapshot(s: dict):
    snapshots = s.setdefault("snapshots", [])
    snapshots.append({
        "fen": s["board"].fen(),
        "moves": list(s["moves"]),
        "bot_clock": s["bot_clock"],
        "human_clock": s["human_clock"],
        "last_move": s["last_move"],
    })
    del snapshots[:-200]


def _tick_clock(s: dict):
    """Subtract wall time from whichever clock is running."""
    now = time.monotonic()
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
    _try_persist_finished_game(s)


# ── Analysis Stockfish (separate from the game engine) ────────────────────
_analysis_sf = None

def _get_analysis_sf():
    global _analysis_sf
    if _analysis_sf is None:
        try:
            _analysis_sf = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
            _analysis_sf.configure({"Threads": 1, "Hash": 64})
        except Exception:
            logger.exception("Analysis Stockfish failed to start")
            _analysis_sf = None
    return _analysis_sf


def _discard_analysis_sf():
    global _analysis_sf
    engine, _analysis_sf = _analysis_sf, None
    if engine is not None:
        try:
            engine.quit()
        except Exception:
            logger.exception("Failed to discard analysis Stockfish")


@app.route("/api/games")
def api_games():
    """Backward-compatible alias for local user-vs-bot history."""
    with _saved_games_lock:
        games = list(reversed(_saved_games()))
    return jsonify({"games": games})

@app.route("/api/local-games")
def api_local_games():
    """Return the six most recently completed browser-vs-bot games."""
    with _saved_games_lock:
        games = list(reversed(_saved_games()))
    return jsonify({"games": games})


@app.route("/api/eval", methods=["POST"])
def api_eval():
    """Run Stockfish depth-15 on a FEN, return centipawn score from White's POV."""
    fen = _request_data().get("fen", "")
    if not _analysis_lock.acquire(blocking=False):
        return jsonify({"error": "Analysis is busy. Try again shortly."}), 429
    try:
        sf = _get_analysis_sf()
        if sf is None:
            return jsonify({"error": "Stockfish unavailable"}), 503
        board = chess.Board(fen)
        if board.is_game_over():
            return jsonify({"cp": 0, "is_mate": False, "mate": None})
        info = sf.analyse(board, chess.engine.Limit(depth=15))
        score = info["score"].white()
        if score.is_mate():
            m = score.mate()
            return jsonify({"cp": 10000 if m > 0 else -10000, "is_mate": True, "mate": m})
        return jsonify({"cp": score.score(), "is_mate": False, "mate": None})
    except Exception:
        logger.exception("Position evaluation failed")
        _discard_analysis_sf()
        return jsonify({"error": "Position evaluation failed."}), 500
    finally:
        _analysis_lock.release()



@app.route("/api/lines", methods=["POST"])
def api_lines():
    """Run Stockfish multipv=3 depth-15 on a FEN; return top-3 lines with SAN continuations."""
    data = _request_data()
    fen = data.get("fen", "")
    multipv = min(5, max(1, data.get("lines", 3))) if isinstance(data.get("lines", 3), int) else 3
    think_time = data.get("time", 0.3)
    think_time = min(2.0, max(0.05, float(think_time))) if isinstance(think_time, (int, float)) else 0.3
    if not _analysis_lock.acquire(blocking=False):
        return jsonify({"error": "Analysis is busy. Try again shortly."}), 429
    try:
        sf = _get_analysis_sf()
        if sf is None:
            return jsonify({"error": "Stockfish unavailable"}), 503
        board = chess.Board(fen)
        if board.is_game_over():
            return jsonify({"lines": []})
        infos = sf.analyse(board, chess.engine.Limit(time=think_time), multipv=multipv)
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
                        "eval_cp": ev["cp"], "eval_is_mate": ev["is_mate"], "eval_mate": ev["mate"],
                        "depth": max((info.get("depth", 0) for info in infos), default=0)})
    except Exception:
        logger.exception("Line analysis failed")
        _discard_analysis_sf()
        return jsonify({"error": "Line analysis failed."}), 500
    finally:
        _analysis_lock.release()



@app.route("/")
def index():
    return render_template("index.html")


@app.route("/new_game", methods=["POST"])
def new_game():
    global _reserved_games
    if MODEL_ERROR is not None:
        return jsonify({"error": "Chess model is unavailable."}), 503
    data = _request_data()
    tc         = data.get("tc", "blitz")
    bot_color  = data.get("bot_color", "black")
    is_rematch = bool(data.get("is_rematch", False))
    initial_seconds = data.get("initial_seconds")
    increment_seconds = data.get("increment_seconds", 0)
    if initial_seconds is not None:
        if not isinstance(initial_seconds, (int, float)) or not 60 <= initial_seconds <= 10800:
            return jsonify({"error": "Custom clock must be between 1 and 180 minutes."}), 400
        if not isinstance(increment_seconds, (int, float)) or not 0 <= increment_seconds <= 60:
            return jsonify({"error": "Increment must be between 0 and 60 seconds."}), 400
    if tc not in TIME_CONTROLS:
        return jsonify({"error": "Unsupported time control."}), 400
    if bot_color not in ("white", "black"):
        return jsonify({"error": "bot_color must be 'white' or 'black'."}), 400

    _prune_games()
    old_token, old_game = _current_game()
    owner = _client_owner()
    with _games_lock:
        if old_token in _creating_tokens:
            return jsonify({"error": "Game creation already in progress."}), 409
        if old_game is None and len(_games) + _reserved_games >= MAX_ACTIVE_GAMES:
            return jsonify({"error": "Server is at game capacity. Try again shortly."}), 503
        owner_games = sum(
            game.get("owner") == owner for game in _games.values()
        ) + _reserved_by_owner.get(owner, 0)
        if old_game is None and owner_games >= MAX_GAMES_PER_IP:
            return jsonify({
                "error": "Too many active games from this connection."
            }), 429
        if old_token:
            _creating_tokens.add(old_token)
        if old_game is None:
            _reserved_games += 1
            _reserved_by_owner[owner] += 1
    token = secrets.token_urlsafe(32)
    try:
        s = (
            _new_state(tc, bot_color, initial_seconds, increment_seconds)
            if initial_seconds is not None
            else _new_state(tc, bot_color)
        )
    except Exception:
        logger.exception("Unable to initialize a new game")
        with _games_lock:
            if old_token:
                _creating_tokens.discard(old_token)
            if old_game is None:
                _reserved_games -= 1
                _reserved_by_owner[owner] -= 1
                if not _reserved_by_owner[owner]:
                    _reserved_by_owner.pop(owner, None)
        return jsonify({"error": "Chess engine is unavailable."}), 503
    s["is_rematch"] = is_rematch
    s["owner"] = owner
    with _games_lock:
        if old_token:
            _creating_tokens.discard(old_token)
        if old_game is None:
            _reserved_games -= 1
            _reserved_by_owner[owner] -= 1
            if not _reserved_by_owner[owner]:
                _reserved_by_owner.pop(owner, None)
        _games[token] = s
        if old_token:
            _games.pop(old_token, None)
    if old_game is not None:
        with old_game["lock"]:
            old_game["over"] = True
            old_game["result"] = old_game.get("result") or "Game replaced."
            old_game["version"] += 1
            _try_persist_finished_game(old_game)
            try:
                old_game["engine"].close()
            except Exception:
                logger.exception("Unable to close replaced game engine")

    response = jsonify(_board_json(s))
    response.set_cookie(
        GAME_COOKIE, token, httponly=True,
        secure=request.is_secure or os.environ.get("COOKIE_SECURE", "0") == "1",
        samesite="Lax", max_age=24 * 60 * 60,
    )
    return response


@app.route("/resign", methods=["POST"])
def resign():
    _, s, error = _game_or_error()
    if error:
        return error
    with s["lock"]:
        if s.get("over"):
            return jsonify({"error": "Game is already over."}), 409
        s["over"] = True
        s["result"] = "You resigned — Alan Dai wins!"
        s["version"] += 1
        _try_persist_finished_game(s)
        return jsonify(_board_json(s))


@app.route("/abort", methods=["POST"])
def abort():
    _, s, error = _game_or_error()
    if error:
        return error
    with s["lock"]:
        if s.get("over"):
            return jsonify({"error": "Game is already over."}), 409
        board: chess.Board = s["board"]
        if board.fullmove_number > 2:
            return jsonify({"error": "Too late to abort — use Resign instead."}), 400
        s["over"] = True
        s["result"] = "Game aborted."
        s["version"] += 1
        _try_persist_finished_game(s)
        return jsonify(_board_json(s))


@app.route("/end_game", methods=["POST"])
def end_game():
    """Idempotently discard the browser's current game without recording a result."""
    token = request.cookies.get(GAME_COOKIE, "")
    with _games_lock:
        game = _games.pop(token, None) if token else None
    if game is not None:
        with game["lock"]:
            game["over"] = True
            game["version"] += 1
            game["engine"].close()
    response = jsonify({"ended": game is not None})
    response.delete_cookie(
        GAME_COOKIE,
        secure=request.is_secure or os.environ.get("COOKIE_SECURE", "0") == "1",
        samesite="Lax",
    )
    return response


@app.route("/move", methods=["POST"])
def human_move():
    data = _request_data()
    uci = data.get("uci", "")
    token, s, error = _game_or_error()
    if error:
        return error
    with s["lock"]:
        if not _version_matches(s, data):
            return jsonify({"error": "Position changed.", **_board_json(s)}), 409
        if s.get("over"):
            return jsonify({"error": "Game is over."}), 409
        board: chess.Board = s["board"]
        if board.turn == s["bot_color"]:
            return jsonify({"error": "Not your turn."}), 409
        try:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                # Try with queen promotion
                move = chess.Move.from_uci(uci[:4] + "q") if len(uci) == 4 else move
            if move not in board.legal_moves:
                return jsonify({"error": "Illegal move."}), 400
        except Exception:
            return jsonify({"error": "Bad move format."}), 400

        _tick_clock(s)
        _check_game_over(s)
        if s["over"]:
            return jsonify(_board_json(s)), 409
        _save_snapshot(s)
        san = board.san(move)
        board.push(move)
        s["human_clock"] += s.get("increment", 0)
        s["moves"].append(san)
        s["last_move"] = [move.from_square, move.to_square]
        s["version"] += 1
        _check_game_over(s)
        if (
            data.get("start_bot") is True
            and not s["over"]
            and board.turn == s["bot_color"]
        ):
            s["bot_busy"] = True
            _start_bot_worker(token, s, s["version"])
        return jsonify(_board_json(s))


def _finish_bot_move(token: str, s: dict, start_version: int) -> None:
    engine: ChessBotEngine = s["engine"]
    try:
        with s["lock"]:
            board = s["board"].copy(stack=True)
            clock = s["bot_clock"]
            is_rematch = s["is_rematch"]
            move = engine.get_move(board, clock, is_rematch=is_rematch)
            if MAX_ARTIFICIAL_THINK_DELAY:
                think_delay = s["think_timer"].get_delay(
                    board,
                    engine.last_gap_cp,
                    move,
                    clock,
                    from_book=engine.last_from_book,
                )
                think_delay = min(think_delay, MAX_ARTIFICIAL_THINK_DELAY)
            else:
                think_delay = 0.0
        if think_delay:
            time.sleep(think_delay)
    except Exception:
        logger.exception("Bot move calculation failed")
        with s["lock"]:
            s["bot_busy"] = False
        return

    with _games_lock:
        current = _games.get(token)
    with s["lock"]:
        s["bot_busy"] = False
        if current is not s or s["version"] != start_version:
            return
        if s.get("over"):
            return
        if move not in s["board"].legal_moves:
            logger.error("Calculated bot move became illegal: %s", move)
            return
        _tick_clock(s)
        _check_game_over(s)
        if s["over"]:
            return
        san = s["board"].san(move)
        _save_snapshot(s)
        s["board"].push(move)
        s["bot_clock"] += s.get("increment", 0)
        s["moves"].append(san)
        s["last_move"] = [move.from_square, move.to_square]
        s["version"] += 1
        s["decision_source"] = getattr(engine, "last_decision_source", "chess engine")
        _check_game_over(s)


def _start_bot_worker(token: str, s: dict, start_version: int) -> None:
    worker = threading.Thread(
        target=_finish_bot_move,
        args=(token, s, start_version),
        name=f"chessbot-move-{token[:8]}",
        daemon=True,
    )
    worker.start()


@app.route("/bot_move", methods=["POST"])
def bot_move():
    data = _request_data()
    token, s, error = _game_or_error()
    if error:
        return error
    with s["lock"]:
        if not _version_matches(s, data):
            return jsonify({"error": "Position changed.", **_board_json(s)}), 409
        if s.get("over"):
            return jsonify(_board_json(s))
        if s["bot_busy"]:
            return jsonify({"error": "Bot move already in progress.", **_board_json(s)}), 409
        board: chess.Board = s["board"]
        if board.turn != s["bot_color"]:
            return jsonify({"error": "Not bot's turn."}), 409
        _tick_clock(s)
        _check_game_over(s)
        if s["over"]:
            return jsonify(_board_json(s))
        start_version = s["version"]
        s["bot_busy"] = True
        _start_bot_worker(token, s, start_version)
        return jsonify(_board_json(s)), 202


@app.route("/undo", methods=["POST"])
def undo_move():
    _, s, error = _game_or_error()
    if error:
        return error
    with s["lock"]:
        if s.get("bot_busy"):
            return jsonify({"error": "Wait for the bot move to finish."}), 409
        if len(s["snapshots"]) < 2:
            return jsonify({"error": "There is no complete turn to undo."}), 409
        snapshot = s["snapshots"][-2]
        s["snapshots"] = s["snapshots"][:-2]
        s["board"] = chess.Board(snapshot["fen"])
        s["moves"] = snapshot["moves"]
        s["bot_clock"] = snapshot["bot_clock"]
        s["human_clock"] = snapshot["human_clock"]
        s["last_move"] = snapshot["last_move"]
        s["over"] = False
        s["result"] = None
        s["decision_source"] = "undo"
        s["last_tick"] = time.monotonic()
        s["version"] += 1
        return jsonify(_board_json(s))


@app.route("/state")
def get_state():
    _, s, error = _game_or_error()
    if error:
        return error
    with s["lock"]:
        _tick_clock(s)
        _check_game_over(s)
        return jsonify(_board_json(s))


@app.route("/debug")
def debug():
    """Non-sensitive compatibility endpoint; prefer /health/live and /health/ready."""
    info = {"model_ready": MODEL_ERROR is None}
    return jsonify(info)


@app.route("/health/live")
def health_live():
    return jsonify({"status": "ok"})


@app.route("/health/ready")
def health_ready():
    if MODEL_ERROR is not None:
        return jsonify({"status": "not_ready", "model": "failed"}), 503
    # Stockfish is optional for legal move generation, but report the deployment as
    # degraded when it is absent because the safety filter is then disabled.
    stockfish_ready = _stockfish_is_ready()
    status = 200 if stockfish_ready else 503
    return jsonify({
        "status": "ready" if stockfish_ready else "not_ready",
        "model": "ready",
        "stockfish": "ready" if stockfish_ready else "unavailable",
    }), status


def _shutdown_engines():
    global _analysis_sf
    _shutdown_event.set()
    with _games_lock:
        games = list(_games.values())
        _games.clear()
    for game in games:
        with game["lock"]:
            game["engine"].close()
    with _analysis_lock:
        engine, _analysis_sf = _analysis_sf, None
        if engine is not None:
            try:
                engine.quit()
            except Exception:
                logger.exception("Failed to close analysis Stockfish")


atexit.register(_shutdown_engines)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"Open http://localhost:{port} in your browser")
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
