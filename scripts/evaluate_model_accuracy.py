"""Compare greedy ONNX policy moves with played user moves in a PGN."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path

import chess
import chess.pgn

from bot.model_contract import resolve_release_paths
from scripts.build_model_review import predict_positions, sha256_file

ROOT = Path(__file__).resolve().parents[1]


def is_one_minute(time_control: str) -> bool:
    base = time_control.split("+", 1)[0]
    return base == "60"


def collect_positions(path: Path, username: str, after_move: int) -> tuple[list[dict], dict]:
    positions = []
    excluded_games = retained_games = total_games = 0
    username_folded = username.casefold()
    with path.open(encoding="utf-8", errors="replace") as source:
        game_index = 0
        while game := chess.pgn.read_game(source):
            game_index += 1
            total_games += 1
            time_control = game.headers.get("TimeControl", "")
            if is_one_minute(time_control):
                excluded_games += 1
                continue
            white = game.headers.get("White", "")
            black = game.headers.get("Black", "")
            if white.casefold() == username_folded:
                user_color = chess.WHITE
            elif black.casefold() == username_folded:
                user_color = chess.BLACK
            else:
                continue
            board = game.board()
            if type(board) is not chess.Board:
                continue
            retained_games += 1
            for ply, move in enumerate(game.mainline_moves(), start=1):
                if (
                    board.turn == user_color
                    and board.fullmove_number > after_move
                    and not board.is_game_over()
                ):
                    positions.append({
                        "fen": board.fen(),
                        "playedMoveUci": move.uci(),
                        "playedMoveSan": board.san(move),
                        "ply": ply,
                        "fullmove": board.fullmove_number,
                        "gameIndex": game_index,
                        "white": white,
                        "black": black,
                        "userColor": "white" if user_color else "black",
                        "result": game.headers.get("Result", "*"),
                        "date": game.headers.get("UTCDate", game.headers.get("Date", "")),
                        "event": game.headers.get("Event", ""),
                        "timeControl": time_control or "(missing)",
                    })
                board.push(move)
    return positions, {
        "totalGames": total_games,
        "excludedOneMinuteGames": excluded_games,
        "retainedGames": retained_games,
    }


def accuracy_summary(rows: list[dict]) -> dict:
    correct = sum(row["matchesActual"] for row in rows)
    breakdowns = {}
    dimensions = {
        "timeControl": lambda row: row["timeControl"],
        "userColor": lambda row: row["userColor"],
        "movePhase": lambda row: (
            "1" if row["fullmove"] == 1
            else "2-5" if row["fullmove"] <= 5
            else "6-15" if row["fullmove"] <= 15
            else "16+"
        ),
    }
    for name, key_function in dimensions.items():
        groups = defaultdict(list)
        for row in rows:
            groups[key_function(row)].append(row)
        breakdowns[name] = {
            key: {
                "positions": len(group),
                "correct": sum(item["matchesActual"] for item in group),
                "accuracy": round(
                    sum(item["matchesActual"] for item in group) / len(group), 6
                ),
            }
            for key, group in sorted(groups.items())
        }
    return {
        "positions": len(rows),
        "correct": correct,
        "incorrect": len(rows) - correct,
        "accuracy": round(correct / len(rows), 6),
        "breakdowns": breakdowns,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pgn", type=Path, required=True)
    parser.add_argument("--username", default="yuandan2")
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--after-move", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--output", type=Path, default=ROOT / "model_accuracy_2026-08-11")
    args = parser.parse_args()

    eligible, game_counts = collect_positions(args.pgn, args.username, args.after_move)
    rng = random.Random(args.seed)
    sample = (
        rng.sample(eligible, args.sample_size)
        if len(eligible) >= args.sample_size
        else eligible
    )
    model_path, manifest_path = resolve_release_paths(
        ROOT / "best_model.onnx", ROOT / "best_model.manifest.json",
        ROOT / "best_model.release.json",
    )
    predictions, _ = predict_positions(sample, model_path, manifest_path)
    rows = [
        {**row, "matchesActual": row["modelMoveUci"] == row["playedMoveUci"]}
        for row in predictions
    ]
    summary = accuracy_summary(rows)
    report = {
        "configuration": {
            "pgn": args.pgn.name,
            "pgnSha256": sha256_file(args.pgn),
            "username": args.username,
            "excludedTimeControl": "base time exactly 60 seconds",
            "afterMove": args.after_move,
            "requestedSampleSize": args.sample_size,
            "eligiblePositions": len(eligible),
            "sampledPositions": len(sample),
            "sampleWithReplacement": False,
            "seed": args.seed,
            "model": model_path.name,
            "modelSha256": sha256_file(model_path),
            **game_counts,
        },
        "summary": summary,
        "positions": rows,
    }
    json_path = args.output.with_suffix(".json")
    csv_path = args.output.with_suffix(".csv")
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=[
            "id", "gameIndex", "date", "white", "black", "result",
            "timeControl", "userColor", "fullmove", "fen", "playedMoveSan",
            "playedMoveUci", "modelMoveSan", "modelMoveUci", "matchesActual",
            "modelConfidence", "modelValue",
        ])
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in writer.fieldnames} for row in rows)
    print(json.dumps({"configuration": report["configuration"], "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
