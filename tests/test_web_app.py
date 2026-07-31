import threading
import time
import sys
import types
import tempfile
import unittest
from unittest import mock

import chess

# The web tests replace game engines and exercise HTTP/state behavior. Permit them
# to run in a lightweight environment; the real ONNX runtime is covered separately
# by test_model_smoke.py.
try:
    import onnxruntime  # noqa: F401
except ImportError:
    class _FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def get_inputs(self):
            return [types.SimpleNamespace(name="input", shape=["batch", 21, 8, 8])]

        def get_outputs(self):
            return [
                types.SimpleNamespace(name="policy", shape=["batch", 4288]),
                types.SimpleNamespace(name="value", shape=["batch", 1]),
            ]

    sys.modules["onnxruntime"] = types.SimpleNamespace(
        SessionOptions=lambda: types.SimpleNamespace(),
        InferenceSession=_FakeSession,
    )

try:
    import dotenv  # noqa: F401
except ImportError:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda: None)

import web_app


class FakeEngine:
    last_gap_cp = None
    last_from_book = False
    stockfish = None

    def get_move(self, board, clock_remaining, is_rematch=False):
        return next(iter(board.legal_moves))

    def close(self):
        return None


class FakeTimer:
    def get_delay(self, *args, **kwargs):
        return 0.01


def fake_state(tc, bot_color, total_seconds=None, increment_seconds=0):
    total = float(total_seconds or web_app.TIME_CONTROLS[tc])
    return {
        "lock": threading.RLock(),
        "engine": FakeEngine(),
        "think_timer": FakeTimer(),
        "board": chess.Board(),
        "bot_color": chess.WHITE if bot_color == "white" else chess.BLACK,
        "bot_clock": total,
        "human_clock": total,
        "last_tick": time.monotonic(),
        "over": False,
        "result": None,
        "is_rematch": False,
        "moves": [],
        "last_move": None,
        "version": 0,
        "bot_busy": False,
        "last_access": time.monotonic(),
        "increment": float(increment_seconds),
        "snapshots": [],
        "decision_source": "—",
        "time_control": tc,
        "saved": False,
    }


class WebAppTests(unittest.TestCase):
    def setUp(self):
        web_app.app.config.update(TESTING=True)
        self.temp_directory = tempfile.TemporaryDirectory()
        self.saved_path_patcher = mock.patch.object(
            web_app, "LOCAL_GAMES_PATH", web_app.Path(self.temp_directory.name) / "games.json"
        )
        self.saved_path_patcher.start()
        self.new_state_patcher = mock.patch.object(
            web_app, "_new_state", side_effect=fake_state
        )
        self.new_state = self.new_state_patcher.start()
        with web_app._games_lock:
            web_app._games.clear()
        with web_app._rate_lock:
            web_app._rate_events.clear()

    def tearDown(self):
        self.new_state_patcher.stop()
        self.saved_path_patcher.stop()
        self.temp_directory.cleanup()
        with web_app._games_lock:
            web_app._games.clear()

    def _new_game(self, client, bot_color="black"):
        response = client.post(
            "/new_game",
            json={"tc": "blitz", "bot_color": bot_color, "is_rematch": False},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def _copy_game_cookie(self, source, target):
        cookie = source.get_cookie(web_app.GAME_COOKIE)
        self.assertIsNotNone(cookie)
        target.set_cookie(web_app.GAME_COOKIE, cookie.value)

    def test_clients_cannot_access_or_replace_each_others_games(self):
        first = web_app.app.test_client()
        second = web_app.app.test_client()
        first_game = self._new_game(first)
        self.assertEqual(second.get("/state").status_code, 404)
        second_game = self._new_game(second)
        self.assertEqual(first.get("/state").get_json()["fen"], first_game["fen"])
        self.assertEqual(second.get("/state").get_json()["fen"], second_game["fen"])
        with web_app._games_lock:
            self.assertEqual(len(web_app._games), 2)

    def test_mutations_require_game_cookie_and_validate_json(self):
        client = web_app.app.test_client()
        self.assertEqual(client.post("/resign").status_code, 404)
        self.assertEqual(client.post("/abort").status_code, 404)
        self.assertEqual(client.post("/move", data="bad").status_code, 404)
        self.assertEqual(
            client.post("/new_game", json={"tc": "invalid"}).status_code, 400
        )

    def test_end_game_discards_current_game_without_saving_it(self):
        client = web_app.app.test_client()
        self._new_game(client)
        before = len(client.get("/api/local-games").get_json()["games"])
        ended = client.post("/end_game")
        self.assertEqual(ended.status_code, 200)
        self.assertTrue(ended.get_json()["ended"])
        self.assertEqual(client.get("/state").status_code, 404)
        self.assertEqual(
            len(client.get("/api/local-games").get_json()["games"]), before
        )
        self.assertFalse(client.post("/end_game").get_json()["ended"])

    def test_cross_origin_mutation_is_rejected(self):
        client = web_app.app.test_client()
        response = client.post(
            "/new_game",
            json={"tc": "blitz", "bot_color": "black"},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_rate_limit_stops_analysis_abuse(self):
        client = web_app.app.test_client()
        with mock.patch.object(web_app, "_get_analysis_sf", return_value=None):
            for _ in range(30):
                client.post("/api/eval", json={"fen": "bad"})
            self.assertEqual(
                client.post("/api/eval", json={"fen": "bad"}).status_code, 429
            )

    def test_custom_clock_and_increment_are_applied(self):
        client = web_app.app.test_client()
        response = client.post(
            "/new_game",
            json={
                "tc": "blitz",
                "bot_color": "black",
                "initial_seconds": 300,
                "increment_seconds": 3,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["increment"], 3)
        self.assertGreater(response.get_json()["human_clock"], 299)

    def test_only_six_latest_local_games_are_persisted(self):
        for index in range(7):
            state = fake_state("blitz", "black")
            state["board"].push_san("e4")
            state["moves"] = ["e4"]
            state["over"] = True
            state["result"] = f"You win by checkmate! #{index}"
            web_app._persist_finished_game(state)
        response = web_app.app.test_client().get("/api/local-games")
        games = response.get_json()["games"]
        self.assertEqual(len(games), 6)
        self.assertIn("#6", games[0]["result_text"])
        self.assertNotIn("#0", [game["result_text"] for game in games])

    def test_frontend_security_and_cache_headers(self):
        client = web_app.app.test_client()
        page = client.get("/")
        self.assertIn("script-src 'self'", page.headers["Content-Security-Policy"])
        self.assertNotIn("script-src 'self' 'unsafe-inline'", page.headers["Content-Security-Policy"])
        asset = client.get("/static/dist/app.min.js")
        self.assertIn("public", asset.headers["Cache-Control"])
        self.assertNotIn("no-cache", asset.headers["Cache-Control"])
        asset.close()

    def test_undo_restores_position_before_complete_turn(self):
        client = web_app.app.test_client()
        self._new_game(client, bot_color="black")
        moved = client.post(
            "/move", json={"uci": "e2e4", "expected_version": 0}
        )
        self.assertEqual(moved.status_code, 200)
        bot = client.post(
            "/bot_move", json={"expected_version": moved.get_json()["version"]}
        )
        self.assertEqual(bot.status_code, 200)
        undone = client.post("/undo")
        self.assertEqual(undone.status_code, 200)
        self.assertEqual(undone.get_json()["moves"], [])
        self.assertEqual(undone.get_json()["fen"], chess.Board().fen())

    def test_capacity_is_checked_before_engine_creation(self):
        client = web_app.app.test_client()
        with web_app._games_lock:
            for index in range(web_app.MAX_ACTIVE_GAMES):
                web_app._games[f"occupied-{index}"] = fake_state("blitz", "black")
        before = self.new_state.call_count
        response = client.post(
            "/new_game", json={"tc": "blitz", "bot_color": "black"}
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.new_state.call_count, before)

    def test_duplicate_bot_move_is_rejected(self):
        first = web_app.app.test_client()
        duplicate = web_app.app.test_client()
        self._new_game(first, bot_color="white")
        self._copy_game_cookie(first, duplicate)
        entered = threading.Event()
        release = threading.Event()
        result = {}

        def delayed_sleep(_):
            entered.set()
            release.wait(2)

        def run_first():
            result["response"] = first.post(
                "/bot_move", json={"expected_version": 0}
            )

        with mock.patch.object(web_app.time, "sleep", side_effect=delayed_sleep):
            worker = threading.Thread(target=run_first)
            worker.start()
            self.assertTrue(entered.wait(1))
            response = duplicate.post("/bot_move", json={"expected_version": 0})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()["error"], "Bot move already in progress.")
            release.set()
            worker.join(2)

        self.assertEqual(result["response"].status_code, 200)
        self.assertEqual(len(result["response"].get_json()["moves"]), 1)

    def test_new_game_invalidates_pending_bot_move(self):
        first = web_app.app.test_client()
        replacement = web_app.app.test_client()
        self._new_game(first, bot_color="white")
        self._copy_game_cookie(first, replacement)
        entered = threading.Event()
        release = threading.Event()
        result = {}

        def delayed_sleep(_):
            entered.set()
            release.wait(2)

        def run_old_move():
            result["response"] = first.post(
                "/bot_move", json={"expected_version": 0}
            )

        with mock.patch.object(web_app.time, "sleep", side_effect=delayed_sleep):
            worker = threading.Thread(target=run_old_move)
            worker.start()
            self.assertTrue(entered.wait(1))
            self._new_game(replacement, bot_color="black")
            release.set()
            worker.join(2)

        self.assertEqual(result["response"].status_code, 409)
        self.assertIn("discarded", result["response"].get_json()["error"])

    def test_game_over_and_clock_outcomes(self):
        checkmate = fake_state("blitz", "black")
        checkmate["board"] = chess.Board(
            "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
        )
        web_app._check_game_over(checkmate)
        self.assertTrue(checkmate["over"])
        self.assertIn("checkmate", checkmate["result"])

        drawn = fake_state("blitz", "black")
        drawn["board"] = chess.Board("8/8/8/8/8/8/7k/7K w - - 0 1")
        web_app._check_game_over(drawn)
        self.assertEqual(drawn["result"], "Draw — insufficient material.")

        flagged = fake_state("blitz", "white")
        flagged["human_clock"] = 0
        web_app._check_game_over(flagged)
        self.assertEqual(flagged["result"], "You flagged — Alan Dai wins on time!")

    def test_human_flag_is_checked_before_move_is_recorded(self):
        client = web_app.app.test_client()
        self._new_game(client, bot_color="black")
        cookie = client.get_cookie(web_app.GAME_COOKIE)
        with web_app._games_lock:
            state = web_app._games[cookie.value]
        with state["lock"]:
            state["human_clock"] = 0.0
        response = client.post(
            "/move", json={"uci": "e2e4", "expected_version": 0}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["moves"], [])

    def test_bot_flag_is_checked_before_move_is_recorded(self):
        client = web_app.app.test_client()
        self._new_game(client, bot_color="white")
        cookie = client.get_cookie(web_app.GAME_COOKIE)
        with web_app._games_lock:
            state = web_app._games[cookie.value]
        with state["lock"]:
            state["bot_clock"] = 0.0
        response = client.post("/bot_move", json={"expected_version": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["moves"], [])


if __name__ == "__main__":
    unittest.main()
