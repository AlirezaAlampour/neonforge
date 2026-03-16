#!/usr/bin/env python3
"""Resolver probe that exercises the real F5-TTS model-load path."""

import sys
from pathlib import Path

service_root = Path("/app") if Path("/app/app.py").exists() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(service_root))

from app import destroy_model, load_model


def main() -> int:
    load_model()
    destroy_model()
    print("F5-TTS model probe completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
