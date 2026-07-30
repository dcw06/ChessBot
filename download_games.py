import requests
import os
import time
import tempfile
from pathlib import Path

USERNAME = "yuandan"
OUTPUT_DIR = Path(__file__).resolve().parent / "pgns"
HEADERS = {"User-Agent": "ChessBot/1.0 personal-project"}


def download_all_games():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    archives_url = f"https://api.chess.com/pub/player/{USERNAME}/games/archives"
    response = requests.get(archives_url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    archives = response.json()["archives"]
    print(f"Found {len(archives)} months of games")

    all_pgns = []
    for url in archives:
        month = "/".join(url.split("/")[-2:])
        print(f"Downloading {month}...", end=" ", flush=True)
        resp = requests.get(url + "/pgn", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        all_pgns.append(resp.text)
        print("OK")
        time.sleep(0.5)

    if not all_pgns:
        raise RuntimeError("No PGNs downloaded; existing archive was preserved")
    out_path = OUTPUT_DIR / "all_games.pgn"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=OUTPUT_DIR, delete=False
    ) as destination:
        destination.write("\n\n".join(all_pgns))
        temp_path = Path(destination.name)
    temp_path.replace(out_path)
    print(f"\nSaved {len(all_pgns)} months → {out_path}")


if __name__ == "__main__":
    download_all_games()
