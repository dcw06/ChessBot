import json
from pathlib import Path
import tempfile
import unittest

from bot.model_contract import resolve_release_paths, validate_manifest


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "best_model.onnx"
MANIFEST = ROOT / "best_model.manifest.json"


class ModelContractTests(unittest.TestCase):
    def test_current_release_pointer_is_compatible(self):
        model, manifest_path = resolve_release_paths(MODEL, MANIFEST)
        manifest = validate_manifest(model, manifest_path, expected_username="yuandan")
        self.assertEqual(manifest["model_file"], model.name)
        self.assertGreaterEqual(manifest["numerical_validation"]["samples"], 1)

    def test_current_deployment_pair_is_compatible(self):
        manifest = validate_manifest(MODEL, MANIFEST, expected_username="yuandan")
        self.assertEqual(manifest["num_planes"], 21)
        self.assertEqual(manifest["num_actions"], 4288)

    def test_schema_mismatch_is_rejected(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        manifest["feature_schema_version"] += 1
        with tempfile.TemporaryDirectory() as directory:
            bad_manifest = Path(directory) / "manifest.json"
            bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "feature_schema_version"):
                validate_manifest(MODEL, bad_manifest, expected_username="yuandan")

    def test_model_hash_mismatch_is_rejected(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        manifest["model_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            bad_manifest = Path(directory) / "manifest.json"
            bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "model_sha256"):
                validate_manifest(MODEL, bad_manifest, expected_username="yuandan")

    def test_release_pointer_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            pointer = Path(directory) / "model.release.json"
            pointer.write_text(
                json.dumps({"model": "../model.onnx", "manifest": "manifest.json"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "basename-only"):
                resolve_release_paths(MODEL, MANIFEST, pointer)


if __name__ == "__main__":
    unittest.main()
