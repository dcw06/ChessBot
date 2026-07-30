"""Versioned contract shared by training, export, and inference."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


MANIFEST_FORMAT_VERSION = 1
FEATURE_SCHEMA_VERSION = 1
ACTION_ENCODING_VERSION = 2
NUM_PLANES = 21
LEGACY_NUM_ACTIONS = 4288
NUM_ACTIONS = 4672
INPUT_NAME = "input"
POLICY_OUTPUT_NAME = "policy"
VALUE_OUTPUT_NAME = "value"
HALFMOVE_CLOCK_PLANE = 20
HALFMOVE_CLOCK_DIVISOR = 100.0
CANONICALIZATION = "vertical-rank-flip-and-color-swap-for-black"
LEGACY_ACTION_ENCODING = "from_square*64+to_square; underpromotions=r,b,n bands"
ACTION_ENCODING = (
    "from_square*64+to_square; underpromotions=piece,direction,to_square bands"
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_manifest_path(model_path: str | Path) -> Path:
    model_path = Path(model_path)
    return model_path.with_name(f"{model_path.stem}.manifest.json")


def default_release_path(model_path: str | Path) -> Path:
    model_path = Path(model_path)
    return model_path.with_name(f"{model_path.stem}.release.json")


def resolve_release_paths(
    model_path: str | Path,
    manifest_path: str | Path | None = None,
    release_path: str | Path | None = None,
) -> tuple[Path, Path]:
    model_path = Path(model_path)
    manifest_path = Path(manifest_path) if manifest_path else default_manifest_path(model_path)
    release_path = Path(release_path) if release_path else default_release_path(model_path)
    if not release_path.is_file():
        return model_path, manifest_path
    with open(release_path, encoding="utf-8") as source:
        release = json.load(source)
    model_name = Path(release.get("model", ""))
    manifest_name = Path(release.get("manifest", ""))
    if (
        not model_name.name or model_name.name != str(model_name)
        or not manifest_name.name or manifest_name.name != str(manifest_name)
    ):
        raise ValueError("Release pointer must contain basename-only artifact paths")
    return release_path.parent / model_name, release_path.parent / manifest_name


def validate_manifest(
    model_path: str | Path,
    manifest_path: str | Path | None = None,
    expected_username: str | None = None,
) -> dict:
    model_path = Path(model_path)
    manifest_path = (
        Path(manifest_path) if manifest_path else default_manifest_path(model_path)
    )
    with open(manifest_path, encoding="utf-8") as source:
        manifest = json.load(source)

    action_version = manifest.get("action_encoding_version")
    action_contracts = {
        1: (LEGACY_NUM_ACTIONS, LEGACY_ACTION_ENCODING),
        ACTION_ENCODING_VERSION: (NUM_ACTIONS, ACTION_ENCODING),
    }
    action_values = action_contracts.get(action_version)
    expected = {
        "manifest_format_version": MANIFEST_FORMAT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "num_planes": NUM_PLANES,
        "input_name": INPUT_NAME,
        "policy_output_name": POLICY_OUTPUT_NAME,
        "value_output_name": VALUE_OUTPUT_NAME,
        "halfmove_clock_plane": HALFMOVE_CLOCK_PLANE,
        "halfmove_clock_divisor": HALFMOVE_CLOCK_DIVISOR,
        "canonicalization": CANONICALIZATION,
    }
    if action_values is None:
        mismatches = [f"unsupported action_encoding_version: {action_version!r}"]
    else:
        expected["action_encoding_version"] = action_version
        expected["num_actions"] = action_values[0]
        expected["action_encoding"] = action_values[1]
        mismatches = []
    mismatches.extend([
        f"{key}: expected {value!r}, got {manifest.get(key)!r}"
        for key, value in expected.items()
        if manifest.get(key) != value
    ])
    if expected_username and manifest.get("username", "").lower() != expected_username.lower():
        mismatches.append(
            f"username: expected {expected_username!r}, got {manifest.get('username')!r}"
        )
    actual_hash = sha256_file(model_path)
    if manifest.get("model_sha256") != actual_hash:
        mismatches.append(
            f"model_sha256: expected {actual_hash!r}, got {manifest.get('model_sha256')!r}"
        )
    if manifest.get("model_file") != model_path.name:
        mismatches.append(
            f"model_file: expected {model_path.name!r}, got {manifest.get('model_file')!r}"
        )
    if not manifest.get("self_contained"):
        mismatches.append("self_contained must be true")
    if action_version == ACTION_ENCODING_VERSION:
        if not isinstance(manifest.get("opset_version"), int):
            mismatches.append("opset_version must be recorded for v2 models")
        validation = manifest.get("numerical_validation", {})
        if not isinstance(validation.get("samples"), int) or validation.get("samples", 0) < 1:
            mismatches.append("numerical_validation.samples must be positive")
        for key in ("torch", "onnx", "onnxruntime", "python"):
            if manifest.get("tool_versions", {}).get(key) in (None, "", "unknown"):
                mismatches.append(f"tool_versions.{key} must be recorded")
        for key in ("dataset_sha256", "source_checkpoint_sha256"):
            value = manifest.get(key)
            if not isinstance(value, str) or len(value) != 64:
                mismatches.append(f"{key} must be a SHA-256 digest")
    if mismatches:
        raise ValueError("Incompatible model manifest: " + "; ".join(mismatches))
    return manifest
