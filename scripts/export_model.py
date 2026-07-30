"""Export a checkpoint to a validated, self-contained ONNX deployment pair."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import onnx
import onnxruntime as ort
import torch

from bot.model_contract import (
    ACTION_ENCODING,
    ACTION_ENCODING_VERSION,
    CANONICALIZATION,
    FEATURE_SCHEMA_VERSION,
    HALFMOVE_CLOCK_DIVISOR,
    HALFMOVE_CLOCK_PLANE,
    INPUT_NAME,
    LEGACY_ACTION_ENCODING,
    LEGACY_NUM_ACTIONS,
    MANIFEST_FORMAT_VERSION,
    NUM_ACTIONS,
    NUM_PLANES,
    POLICY_OUTPUT_NAME,
    VALUE_OUTPUT_NAME,
    default_manifest_path,
    default_release_path,
    sha256_file,
)
from model import ChessNet


OPSET_VERSION = 17
RTOL = 1e-4
ATOL = 1e-5


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_tree_dirty() -> bool | None:
    try:
        return bool(subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _load_checkpoint(path: Path) -> tuple[ChessNet, int, int, str]:
    state = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(state, dict) and "model" in state:
        state = state["model"]
    policy_weight = state.get("policy_head.7.weight")
    if policy_weight is None or policy_weight.ndim != 2:
        raise ValueError("Checkpoint is missing policy_head.7.weight")
    num_actions = int(policy_weight.shape[0])
    if num_actions == LEGACY_NUM_ACTIONS:
        action_version, action_encoding = 1, LEGACY_ACTION_ENCODING
    elif num_actions == NUM_ACTIONS:
        action_version, action_encoding = ACTION_ENCODING_VERSION, ACTION_ENCODING
    else:
        raise ValueError(f"Unsupported checkpoint policy width: {num_actions}")
    model = ChessNet(in_channels=NUM_PLANES, num_actions=num_actions)
    model.load_state_dict(state)
    model.eval()
    return model, action_version, num_actions, action_encoding


def _assert_self_contained(path: Path):
    graph = onnx.load(path, load_external_data=False)
    onnx.checker.check_model(graph)
    external = [
        initializer.name
        for initializer in graph.graph.initializer
        if initializer.data_location == onnx.TensorProto.EXTERNAL
        or initializer.external_data
    ]
    if external:
        raise RuntimeError(
            f"Export contains {len(external)} external initializer(s): {external[:3]}"
        )


def _validate_numerical_parity(
    model: ChessNet, path: Path, num_actions: int
) -> dict:
    rng = np.random.default_rng(42)
    sample = rng.integers(0, 2, size=(4, NUM_PLANES, 8, 8)).astype(np.float32)
    sample[:, HALFMOVE_CLOCK_PLANE] = rng.random((4, 8, 8), dtype=np.float32)
    with torch.no_grad():
        torch_policy, torch_value = model(torch.from_numpy(sample))
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or inputs[0].name != INPUT_NAME:
        raise RuntimeError(f"Unexpected ONNX inputs: {[item.name for item in inputs]}")
    if [item.name for item in outputs] != [POLICY_OUTPUT_NAME, VALUE_OUTPUT_NAME]:
        raise RuntimeError(f"Unexpected ONNX outputs: {[item.name for item in outputs]}")
    if list(inputs[0].shape[1:]) != [NUM_PLANES, 8, 8]:
        raise RuntimeError(f"Unexpected ONNX input shape: {inputs[0].shape}")
    if outputs[0].shape[-1] != num_actions or outputs[1].shape[-1] != 1:
        raise RuntimeError(
            f"Unexpected ONNX output shapes: {[item.shape for item in outputs]}"
        )

    onnx_policy, onnx_value = session.run(None, {INPUT_NAME: sample})
    torch_policy = torch_policy.numpy()
    torch_value = torch_value.numpy()
    np.testing.assert_allclose(onnx_policy, torch_policy, rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(onnx_value, torch_value, rtol=RTOL, atol=ATOL)
    return {
        "samples": len(sample),
        "rtol": RTOL,
        "atol": ATOL,
        "policy_max_abs_error": float(np.max(np.abs(onnx_policy - torch_policy))),
        "value_max_abs_error": float(np.max(np.abs(onnx_value - torch_value))),
    }


def export_model(
    checkpoint_path: str = "best_model.pt",
    output_path: str = "best_model.onnx",
    manifest_path: str | None = None,
    release_path: str | None = None,
    username: str = "yuandan",
    dataset_path: str | None = "pgns/all_games.pgn",
    metrics: dict | None = None,
) -> dict:
    checkpoint = Path(checkpoint_path).resolve()
    output = Path(output_path).resolve()
    manifest_output = (
        Path(manifest_path).resolve()
        if manifest_path
        else default_manifest_path(output)
    )
    release_output = (
        Path(release_path).resolve() if release_path else default_release_path(output)
    )
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint}")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    release_output.parent.mkdir(parents=True, exist_ok=True)
    model, action_version, num_actions, action_encoding = _load_checkpoint(checkpoint)

    temp_handle = tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
    )
    temp_model = Path(temp_handle.name)
    temp_handle.close()
    manifest_handle = tempfile.NamedTemporaryFile(
        prefix=f".{manifest_output.name}.", suffix=".tmp",
        dir=manifest_output.parent, delete=False,
    )
    temp_manifest = Path(manifest_handle.name)
    manifest_handle.close()
    pointer_handle = tempfile.NamedTemporaryFile(
        prefix=f".{release_output.name}.", suffix=".tmp",
        dir=release_output.parent, delete=False,
    )
    temp_pointer = Path(pointer_handle.name)
    pointer_handle.close()
    try:
        sample = torch.zeros((1, NUM_PLANES, 8, 8), dtype=torch.float32)
        torch.onnx.export(
            model,
            sample,
            str(temp_model),
            export_params=True,
            opset_version=OPSET_VERSION,
            do_constant_folding=True,
            input_names=[INPUT_NAME],
            output_names=[POLICY_OUTPUT_NAME, VALUE_OUTPUT_NAME],
            dynamic_axes={
                INPUT_NAME: {0: "batch"},
                POLICY_OUTPUT_NAME: {0: "batch"},
                VALUE_OUTPUT_NAME: {0: "batch"},
            },
            external_data=False,
            dynamo=False,
        )
        _assert_self_contained(temp_model)
        parity = _validate_numerical_parity(model, temp_model, num_actions)
        dataset = Path(dataset_path).resolve() if dataset_path else None
        model_hash = sha256_file(temp_model)
        release_model = output.with_name(f"{output.stem}-{model_hash[:12]}{output.suffix}")
        release_manifest = manifest_output.with_name(
            f"{output.stem}-{model_hash[:12]}.manifest.json"
        )
        manifest = {
            "manifest_format_version": MANIFEST_FORMAT_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "action_encoding_version": action_version,
            "model_file": release_model.name,
            "model_sha256": model_hash,
            "self_contained": True,
            "opset_version": OPSET_VERSION,
            "input_name": INPUT_NAME,
            "policy_output_name": POLICY_OUTPUT_NAME,
            "value_output_name": VALUE_OUTPUT_NAME,
            "num_planes": NUM_PLANES,
            "num_actions": num_actions,
            "halfmove_clock_plane": HALFMOVE_CLOCK_PLANE,
            "halfmove_clock_divisor": HALFMOVE_CLOCK_DIVISOR,
            "canonicalization": CANONICALIZATION,
            "action_encoding": action_encoding,
            "username": username,
            "dataset_file": dataset.name if dataset and dataset.is_file() else None,
            "dataset_sha256": sha256_file(dataset) if dataset and dataset.is_file() else None,
            "source_checkpoint_file": checkpoint.name,
            "source_checkpoint_sha256": sha256_file(checkpoint),
            "source_revision": _git_revision(),
            "source_tree_dirty": _git_tree_dirty(),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "tool_versions": {
                "python": os.sys.version.split()[0],
                "torch": torch.__version__,
                "onnx": onnx.__version__,
                "onnxruntime": ort.__version__,
            },
            "numerical_validation": parity,
            "metrics": metrics or {},
        }
        with open(temp_manifest, "w", encoding="utf-8") as destination:
            json.dump(manifest, destination, indent=2, sort_keys=True)
            destination.write("\n")
        with open(temp_pointer, "w", encoding="utf-8") as destination:
            json.dump({
                "release_format_version": 1,
                "model": release_model.name,
                "manifest": release_manifest.name,
            }, destination, indent=2, sort_keys=True)
            destination.write("\n")
        os.replace(temp_model, release_model)
        os.replace(temp_manifest, release_manifest)
        os.replace(temp_pointer, release_output)
        print(f"Exported {release_model}")
        print(f"Manifest {release_manifest}")
        print(f"Release pointer {release_output}")
        return manifest
    finally:
        temp_model.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)
        temp_pointer.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default="best_model.pt")
    parser.add_argument("--output", default="best_model.onnx")
    parser.add_argument("--manifest")
    parser.add_argument("--release")
    parser.add_argument("--username", default="yuandan")
    parser.add_argument("--dataset", default="pgns/all_games.pgn")
    args = parser.parse_args()
    export_model(
        checkpoint_path=args.checkpoint,
        output_path=args.output,
        manifest_path=args.manifest,
        release_path=args.release,
        username=args.username,
        dataset_path=args.dataset,
    )


if __name__ == "__main__":
    main()
