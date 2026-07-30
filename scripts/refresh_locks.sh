#!/bin/sh
set -eu
PYTHON_BIN=${PYTHON_BIN:-python3.11}

"$PYTHON_BIN" -m pip install "pip==25.3" "pip-tools==7.5.2"
"$PYTHON_BIN" -m piptools compile \
  --generate-hashes \
  --allow-unsafe \
  --resolver=backtracking \
  --output-file=requirements-runtime.lock \
  requirements-runtime.txt
"$PYTHON_BIN" -m piptools compile \
  --generate-hashes \
  --allow-unsafe \
  --resolver=backtracking \
  --output-file=requirements-training.lock \
  requirements.txt
