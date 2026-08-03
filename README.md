# ChessBot

A Flask chess application that combines a behavioral-clone ONNX model, an
opening book, tactical rules, time-management behavior, and an optional
Stockfish safety filter.

## Clean setup

Python 3.11 is the supported baseline. Do not reuse or copy the legacy
`ChessBot_v1_drew_3200/venv`; virtual environments contain absolute interpreter
paths and are not portable. Install Stockfish with the operating-system package
manager, then create a clean environment:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install "pip==25.3"
.venv/bin/python -m pip install --require-hashes -r requirements-runtime.lock
.venv/bin/python -m scripts.smoke_test
.venv/bin/python web_app.py
```

Open <http://localhost:5001>. Set `STOCKFISH_PATH` if Stockfish is not on
`PATH`. `MODEL_PATH`, `MODEL_MANIFEST_PATH`, `MODEL_RELEASE_PATH`, `BOOK_PATH`,
`CHESS_USERNAME`, `PORT`, `MAX_ACTIVE_GAMES`, `MAX_GAMES_PER_IP`,
`GAME_IDLE_TTL`, `CAPACITY_EVICTION_IDLE`, and
`MAX_ARTIFICIAL_THINK_DELAY` are also configurable. Bot replies include a
human-like pause capped at two seconds by default; set the last value to `0`
for immediate replies.

The server gives each browser an opaque HttpOnly game cookie. Games are
isolated in memory, so use one Gunicorn worker unless the state store is moved
to a shared service. The default maximum is eight concurrent games and two
active games per client IP. Idle games expire after 30 minutes; when the server
is full, a game inactive for at least five minutes may be reclaimed.

## Verify

Run the complete test suite and production smoke test:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m scripts.smoke_test
```

Frontend source lives in `templates/`, `static/css/`, and the modules under
`static/js/`. Production serves the generated files in `static/dist/`.

```sh
npm install
npm run build
npm run format:check
npm run lint
npm run test:unit
npx playwright install chromium
npm run test:e2e
```

The browser suite covers desktop and mobile layouts, keyboard access, game
startup, promotion choice, overflow, and visual regression. Rebuild the
production assets after changing frontend source.

Completed browser games against Alan Dai are stored in `local_games.json` for
review in the Analyze tab. Only the six newest games are retained; Chess.com
history is not fetched or displayed.

`/health/live` checks the HTTP process. `/health/ready` verifies that the ONNX
model loaded at startup and reports whether the Stockfish startup probe passed.

The active deployment is selected atomically by `best_model.release.json`,
which points to an immutable hash-named ONNX file and manifest. The canonical
`best_model.onnx` and `best_model.manifest.json` pair remains a fallback when no
release pointer exists. Startup verifies the model SHA-256, username,
feature schema, action encoding, normalization, tensor names, and tensor
shapes. The current model is self-contained; `*.onnx.data` files are neither
required nor copied into the image.

## Train

Training requires the larger exact dependency lock:

```sh
.venv/bin/python -m pip install --require-hashes -r requirements-training.lock
.venv/bin/python train.py
```

At the end of a successful run, `train.py` automatically exports
`best_model.pt` to a self-contained, immutable opset-17 ONNX release and
atomically updates the release pointer. Export is promoted only after:

- ONNX structural validation succeeds;
- no external tensor data is present;
- input/output names and shapes match the versioned contract; and
- PyTorch and ONNX Runtime outputs pass numerical parity checks.

To export an existing checkpoint without retraining:

```sh
.venv/bin/python -m scripts.export_model \
  --checkpoint best_model.pt \
  --output best_model.onnx \
  --username yuandan \
  --dataset pgns/all_games.pgn
```

Commit the hash-named ONNX file, its manifest, and the release pointer together.

## Dependency updates

`requirements-runtime.txt` and `requirements.txt` are the human-edited inputs.
Runtime, Docker, and training consume their corresponding `.lock` files. To
refresh fully resolved locks with package hashes in a clean Python 3.11
environment:

```sh
./scripts/refresh_locks.sh
.venv/bin/python -m pip install --require-hashes -r requirements-runtime.lock
.venv/bin/python -m pip install --require-hashes -r requirements-training.lock
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m scripts.smoke_test
```

Review and commit both lock-file changes. Do not edit generated hash sections
by hand.

## Deploy

Build and smoke-test the image:

```sh
docker build -t chessbot .
docker run --rm chessbot python -m scripts.smoke_test
docker run --rm -p 10000:10000 chessbot
```

The production image installs inference dependencies only. Keep Gunicorn at
one worker while game state is in process memory; threads are supported by the
per-game locking and position-version checks.

## Recovery

If the local environment stops working:

1. Move or remove only the project-local `.venv` directory.
2. Recreate it with Python 3.11 using the clean-setup commands above.
3. Run the unit suite and smoke test.
4. If model validation fails, restore `best_model.release.json` and both files
   it names from the same known-good commit.
5. If a checkpoint must be recovered, restore `best_model.pt`, run the manual
   export command, then rerun the smoke test.

Training data and checkpoints are deliberately absent from the production
image. Back them up separately; copied virtual environments are not backups.
