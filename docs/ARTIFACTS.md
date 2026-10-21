# Model artifact distribution

The hash-named ONNX file and manifest form the active immutable release. The release pointer selects them atomically. The canonical ONNX pair is the tested fallback, and the PyTorch checkpoint supports training/export recovery.

Removing these files or replacing them with LFS pointers without changing CI, Docker, download verification, and recovery would break clean checkouts. They are retained by this maintenance change.

A future migration should publish versioned assets with SHA-256 manifests, make verified download an explicit setup step, test both clean and offline deployments, then remove redundant tracked weights in a normal commit. Rewriting Git history is not required for a forward migration. Historical size remains until an intentional history migration is approved.
