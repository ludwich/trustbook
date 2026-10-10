"""Bake the bundled models into the image at pinned revisions (SPEC §2, docs/DECISIONS.md D-001..D-011).

Usage (build time only; needs huggingface_hub from the backend's ``ml`` extra):

    python docker/download_models.py --dest /opt/trustbook/models

Writes plain model folders plus ``models.lock.json``:

    embeddings/   google/embeddinggemma-2
    reranker/     mixedbread-ai/mxbai-rerank-xsmall-v1
    grounding/    vectara/hallucination_evaluation_model
    grounding/flan-t5-base/   tokenizer + config only (HHEM's remote code needs it; pinned here)
    stt/          whistle.cact + libneedle3.so (Needle engine extracted from its pinned wheel)

Every revision is a full commit hash, so a rebuild always gets byte-identical weights.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download


@dataclass(frozen=True)
class Pinned:
    repo: str
    revision: str
    patterns: tuple[str, ...]
    license: str


EMBEDDINGS = Pinned(
    "google/embeddinggemma-2",
    "914f7f89142e33e77833254d9c9b90c3cef7303b",
    ("*.json", "chat_template.jinja", "tokenizer.model", "model.safetensors"),
    "Apache-2.0",
)
RERANKER = Pinned(
    "mixedbread-ai/mxbai-rerank-xsmall-v1",
    "b5c6e9da73abc3711f593f705371cdbe9e0fe422",
    ("*.json", "spm.model", "model.safetensors", "LICENSE"),  # skips the 370 MB onnx/ folder
    "Apache-2.0",
)
GROUNDING = Pinned(
    "vectara/hallucination_evaluation_model",
    "de9a5e6ba9ba49531a022dba17e35ffeb2a6fad8",
    ("config.json", "configuration_hhem_v2.py", "modeling_hhem_v2.py", "model.safetensors"),
    "Apache-2.0",
)
GROUNDING_TOKENIZER = Pinned(
    "google/flan-t5-base",
    "7bcac572ce56db69c1ea7c8af255c5d7c9672fc2",
    ("config.json", "spiece.model", "tokenizer.json", "tokenizer_config.json",
     "special_tokens_map.json"),
    "Apache-2.0",
)
STT_WEIGHTS = Pinned(
    "Cactus-Compute/whistle",
    "ca5287601bef25af26dcf2e1b2bdc0843a7c19e5",
    ("whistle.cact", "config.json", "LICENSE"),
    "Apache-2.0",
)
STT_ENGINE = Pinned(
    "Cactus-Compute/needle3",
    "8354babbfb92d719ee559f2999d133f11fb9374c",
    ("LICENSE",),
    "Apache-2.0",
)
STT_ENGINE_WHEEL = "python/cactus_needle-3.3.0-py3-none-manylinux2014_x86_64.whl"


def fetch(model: Pinned, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        model.repo,
        revision=model.revision,
        allow_patterns=list(model.patterns),
        local_dir=folder,
    )
    # local_dir keeps a .cache/ of download metadata; it is not needed at runtime.
    shutil.rmtree(folder / ".cache", ignore_errors=True)
    print(f"{model.repo}@{model.revision[:12]} -> {folder}", flush=True)


def fetch_engine(folder: Path) -> None:
    """Extract libneedle3.so from the pinned engine wheel (cactus-needle would fetch it unpinned)."""
    fetch(STT_ENGINE, folder / "needle3")
    wheel = hf_hub_download(STT_ENGINE.repo, STT_ENGINE_WHEEL, revision=STT_ENGINE.revision)
    with zipfile.ZipFile(wheel) as archive:
        (folder / "libneedle3.so").write_bytes(archive.read("needle/libneedle3.so"))
    print(f"needle engine -> {folder / 'libneedle3.so'}", flush=True)


def entry(model: Pinned, folder: Path, dest: Path) -> dict[str, object]:
    return {
        "model_id": model.repo,
        "revision": model.revision,
        "license": model.license,
        "path": str(folder.relative_to(dest)),
        "fallback": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, required=True)
    dest: Path = parser.parse_args().dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    fetch(EMBEDDINGS, dest / "embeddings")
    fetch(RERANKER, dest / "reranker")
    fetch(GROUNDING, dest / "grounding")
    fetch(GROUNDING_TOKENIZER, dest / "grounding" / "flan-t5-base")
    fetch(STT_WEIGHTS, dest / "stt")
    fetch_engine(dest / "stt")

    lock = {
        "models": {
            "embeddings": entry(EMBEDDINGS, dest / "embeddings", dest),
            "reranker": entry(RERANKER, dest / "reranker", dest),
            "grounding": entry(GROUNDING, dest / "grounding", dest),
            "stt": entry(STT_WEIGHTS, dest / "stt", dest),
        },
        "extras": {
            "grounding_tokenizer": {"model_id": GROUNDING_TOKENIZER.repo,
                                    "revision": GROUNDING_TOKENIZER.revision},
            "stt_engine": {"model_id": STT_ENGINE.repo, "revision": STT_ENGINE.revision,
                           "file": STT_ENGINE_WHEEL},
        },
    }
    (dest / "models.lock.json").write_text(json.dumps(lock, indent=2) + "\n", "utf-8")
    print(f"wrote {dest / 'models.lock.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
