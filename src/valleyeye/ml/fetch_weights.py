from __future__ import annotations

import argparse
import os
import urllib.request
from pathlib import Path

from valleyeye.ml.snunet import WEIGHTS_SHA256, WEIGHTS_SIZE_BYTES, WEIGHTS_URL, sha256_file


def fetch_weights(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256_file(destination) == WEIGHTS_SHA256:
        return destination

    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(WEIGHTS_URL, timeout=60) as response, partial.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
        if partial.stat().st_size != WEIGHTS_SIZE_BYTES:
            raise ValueError("Downloaded checkpoint has an unexpected file size")
        if sha256_file(partial) != WEIGHTS_SHA256:
            raise ValueError("Downloaded checkpoint failed SHA-256 verification")
        os.replace(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and verify the Kuro Siwo SNUNet weights")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("weights") / "kuro-siwo-snunet.pt",
        help="Destination path (defaults to the git-ignored weights directory)",
    )
    arguments = parser.parse_args()
    try:
        result = fetch_weights(arguments.output)
    except Exception as exc:
        parser.exit(1, f"Weight fetch failed: {exc}\n")
    print(f"Verified checkpoint: {result} ({WEIGHTS_SHA256})")


if __name__ == "__main__":
    main()
