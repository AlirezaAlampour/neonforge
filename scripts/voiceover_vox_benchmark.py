#!/usr/bin/env python3
"""
Submit reproducible VoxCPM2 voiceover benchmark jobs through the isolated Voiceover API.

Usage:
  python3 scripts/voiceover_vox_benchmark.py --profile-id <voice_profile_id> --case short
  python3 scripts/voiceover_vox_benchmark.py --profile-id <voice_profile_id> --case medium --debug
  python3 scripts/voiceover_vox_benchmark.py --list-cases
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


DEFAULT_GATEWAY_URL = "http://localhost:8080"
VOX_MODEL_ID = "voxcpm2"

BENCHMARK_CASES = {
    "short": "Clear line one. Clear line two. Clear line three.",
    "medium": (
        "Welcome back to the channel. Today we are walking through a practical voiceover test. "
        "The pacing should feel natural, and each sentence should connect cleanly to the next. "
        "This sample is long enough to force multiple chunks without becoming a full production script."
    ),
    "punctuation": (
        "Wait, really? Yes, really. Numbers, dates, and abbreviations can be tricky: "
        "On April 12, 2026, the host said, 'Start at 9:30 a.m., then pause, breathe, and continue.' "
        "After that, read the slash, the comma, and the dash carefully."
    ),
}


def _http_json(url: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    body = None
    headers = {"accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} for {url}: {response_body}") from exc


def _poll_job(gateway_url: str, job_id: str, timeout_seconds: int) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        payload = _http_json(f"{gateway_url.rstrip('/')}/api/v1/voiceover/jobs/{job_id}")
        status = str(payload.get("status") or "")
        if status in {"done", "failed"}:
            return payload
        time.sleep(2)
    raise SystemExit(f"Timed out waiting for voiceover job {job_id}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL, help="Gateway base URL")
    parser.add_argument("--profile-id", help="Saved Voiceover profile id")
    parser.add_argument("--case", choices=sorted(BENCHMARK_CASES), help="Benchmark script case")
    parser.add_argument("--speed", type=float, default=1.0, help="Voiceover speed")
    parser.add_argument("--output-format", choices=("wav", "mp3"), default="wav")
    parser.add_argument("--debug", action="store_true", help="Preserve raw Vox chunk WAVs")
    parser.add_argument("--timeout", type=int, default=600, help="Polling timeout in seconds")
    parser.add_argument("--list-cases", action="store_true", help="Print the built-in benchmark scripts and exit")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.list_cases:
        print(json.dumps(BENCHMARK_CASES, indent=2))
        return 0

    if not args.profile_id or not args.case:
        parser.error("--profile-id and --case are required unless --list-cases is used")

    payload = {
        "voice_profile_id": args.profile_id,
        "script": BENCHMARK_CASES[args.case],
        "model_id": VOX_MODEL_ID,
        "output_format": args.output_format,
        "speed": args.speed,
        "preserve_raw_chunks": args.debug,
    }

    create_payload = _http_json(
        f"{args.gateway_url.rstrip('/')}/api/v1/voiceover/jobs",
        method="POST",
        payload=payload,
    )
    job_id = str(create_payload["job_id"])
    print(f"Submitted Vox benchmark job {job_id} for case '{args.case}'")

    final_payload = _poll_job(args.gateway_url, job_id, args.timeout)
    print(json.dumps(final_payload, indent=2))

    if args.debug:
        print(f"Expected manifest: /srv/ai/outputs/voiceover/{job_id}/vox_debug_manifest.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
