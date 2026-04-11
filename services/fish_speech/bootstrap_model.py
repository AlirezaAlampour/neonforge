from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download

MODEL_REPO_ID = "fishaudio/s2-pro"
ALLOW_PATTERNS = [
    "chat_template.jinja",
    "codec.pth",
    "config.json",
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
    "model.safetensors.index.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
]
REQUIRED_FILES = [
    "codec.pth",
    "config.json",
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
    "model.safetensors.index.json",
    "tokenizer.json",
    "tokenizer_config.json",
]


def main() -> None:
    model_path = Path(os.getenv("FISH_SPEECH_MODEL_PATH", "/models/fish_speech/s2-pro"))
    model_path.mkdir(parents=True, exist_ok=True)

    if all((model_path / filename).exists() for filename in REQUIRED_FILES):
        print(f"Fish Speech checkpoint already present at {model_path}")
        return

    print(f"Downloading Fish Speech checkpoint to {model_path} from {MODEL_REPO_ID}")
    snapshot_download(
        repo_id=MODEL_REPO_ID,
        local_dir=str(model_path),
        allow_patterns=ALLOW_PATTERNS,
        resume_download=True,
    )
    print("Fish Speech checkpoint download complete")


if __name__ == "__main__":
    main()
