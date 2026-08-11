"""Build a deterministic browser review artifact from user PGN positions."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path

import chess
import chess.pgn
import numpy as np

from bot.board_features import board_to_tensor, flip_move, move_to_index
from bot.engine import load_model
from bot.model_contract import resolve_release_paths


ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sample_positions(
    pgn_path: Path, username: str, sample_size: int, seed: int
) -> tuple[list[dict], int]:
    """Reservoir-sample games, choosing one user-to-move position per game."""
    rng = random.Random(seed)
    reservoir: list[dict] = []
    eligible_games = 0
    username = username.casefold()

    with pgn_path.open(encoding="utf-8", errors="replace") as source:
        game_index = 0
        while game := chess.pgn.read_game(source):
            game_index += 1
            white = game.headers.get("White", "")
            black = game.headers.get("Black", "")
            if white.casefold() == username:
                user_color = chess.WHITE
            elif black.casefold() == username:
                user_color = chess.BLACK
            else:
                continue

            board = game.board()
            if type(board) is not chess.Board:
                continue
            candidates: list[dict] = []
            for ply, move in enumerate(game.mainline_moves(), start=1):
                if board.turn == user_color and not board.is_game_over():
                    candidates.append({
                        "fen": board.fen(),
                        "playedMoveUci": move.uci(),
                        "playedMoveSan": board.san(move),
                        "ply": ply,
                    })
                board.push(move)
            if not candidates:
                continue

            eligible_games += 1
            candidate = candidates[rng.randrange(len(candidates))]
            candidate.update({
                "gameIndex": game_index,
                "white": white,
                "black": black,
                "userColor": "white" if user_color == chess.WHITE else "black",
                "result": game.headers.get("Result", "*"),
                "date": game.headers.get("UTCDate", game.headers.get("Date", "")),
                "event": game.headers.get("Event", ""),
            })
            if len(reservoir) < sample_size:
                reservoir.append(candidate)
            else:
                replacement = rng.randrange(eligible_games)
                if replacement < sample_size:
                    reservoir[replacement] = candidate

    if len(reservoir) < sample_size:
        raise ValueError(
            f"Requested {sample_size} positions but found {len(reservoir)} eligible games"
        )
    rng.shuffle(reservoir)
    return reservoir, eligible_games


def predict_positions(
    positions: list[dict], model_path: Path, manifest_path: Path
) -> tuple[list[dict], dict]:
    session, contract = load_model(
        str(model_path), str(manifest_path), expected_username="yuandan"
    )
    action_version = contract["manifest"]["action_encoding_version"]
    tensors = []
    boards = []
    for position in positions:
        board = chess.Board(position["fen"])
        flip = board.turn == chess.BLACK
        tensor = board_to_tensor(board, flip=flip).astype(np.float32)
        tensor[20] /= 100.0
        tensors.append(tensor)
        boards.append(board)

    policy_batches = []
    value_batches = []
    for offset in range(0, len(tensors), 32):
        policy, value = session.run(
            None, {contract["input"]: np.stack(tensors[offset:offset + 32])}
        )
        policy_batches.append(policy)
        value_batches.append(value)
    policies = np.concatenate(policy_batches)
    values = np.concatenate(value_batches)

    reviewed = []
    for number, (position, board, logits, value) in enumerate(
        zip(positions, boards, policies, values, strict=True), start=1
    ):
        legal_moves = list(board.legal_moves)
        if board.turn == chess.BLACK:
            indices = np.array([
                flip_move(move, version=action_version) for move in legal_moves
            ])
        else:
            indices = np.array([
                move_to_index(move, version=action_version) for move in legal_moves
            ])
        legal_logits = logits[indices]
        best_offset = int(np.argmax(legal_logits))
        best_move = legal_moves[best_offset]
        shifted = legal_logits - legal_logits.max()
        probabilities = np.exp(shifted) / np.exp(shifted).sum()
        reviewed.append({
            "id": number,
            **position,
            "sideToMove": "white" if board.turn == chess.WHITE else "black",
            "fullmove": board.fullmove_number,
            "modelMoveUci": best_move.uci(),
            "modelMoveSan": board.san(best_move),
            "modelConfidence": round(float(probabilities[best_offset]), 6),
            "modelValue": round(float(value[0]), 6),
        })
    return reviewed, contract["manifest"]


def write_artifact(
    output: Path, positions: list[dict], metadata: dict
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata, "positions": positions}
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    (output / "positions.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    (output / "review-data.js").write_text(
        f"window.MODEL_REVIEW_DATA={serialized};\n", encoding="utf-8"
    )

    for name in ("index.html", "review.js", "review.css", "mobile.css"):
        shutil.copy2(ROOT / "review_artifact" / name, output / name)
    shutil.copytree(ROOT / "static" / "pieces", output / "pieces", dirs_exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pgn", type=Path, default=ROOT / "all_games.pgn")
    parser.add_argument("--username", default="yuandan")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--output", type=Path, default=ROOT / "model_review")
    args = parser.parse_args()

    model_path, manifest_path = resolve_release_paths(
        ROOT / "best_model.onnx", ROOT / "best_model.manifest.json",
        ROOT / "best_model.release.json",
    )
    positions, eligible_games = sample_positions(
        args.pgn, args.username, args.count, args.seed
    )
    predictions, manifest = predict_positions(positions, model_path, manifest_path)
    metadata = {
        "artifactVersion": 1,
        "username": args.username,
        "positionCount": len(predictions),
        "eligibleGames": eligible_games,
        "sampleSeed": args.seed,
        "sampling": "one user-to-move position per sampled game",
        "prediction": "highest-logit legal ONNX policy move",
        "datasetFile": args.pgn.name,
        "datasetSha256": sha256_file(args.pgn),
        "modelFile": model_path.name,
        "modelSha256": sha256_file(model_path),
        "actionEncodingVersion": manifest["action_encoding_version"],
    }
    write_artifact(args.output, predictions, metadata)
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
