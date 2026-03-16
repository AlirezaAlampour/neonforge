#!/usr/bin/env python3
"""Resolve missing Python packages without disturbing a vendor-tuned GPU stack."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

MODULE_NOT_FOUND_RE = re.compile(
    r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]"
)
IMPORT_NO_MODULE_RE = re.compile(
    r"ImportError:\s+No module named ['\"]([^'\"]+)['\"]"
)
VALID_IMPORT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
PROTECTED_IMPORT_PREFIXES = {
    "cuda",
    "cudnn",
    "nvidia",
    "torch",
    "torchaudio",
    "torchvision",
    "triton",
    "xformers",
}
PROTECTED_DISTRIBUTIONS = {
    "cuda",
    "cudnn",
    "nvidia",
    "torch",
    "torchaudio",
    "torchvision",
    "triton",
    "xformers",
}
MODULE_TO_PACKAGE = {
    "Crypto": "pycryptodome",
    "OpenGL": "PyOpenGL",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python-headless",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
    "git": "GitPython",
    "google.auth": "google-auth",
    "google.protobuf": "protobuf",
    "googleapiclient": "google-api-python-client",
    "pkg_resources": "setuptools",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
}
VERSION_PROBE = textwrap.dedent(
    """
    import importlib
    import json

    result = {}
    for name in ("torch", "torchaudio", "torchvision"):
        try:
            module = importlib.import_module(name)
        except Exception:
            result[name] = None
        else:
            result[name] = getattr(module, "__version__", None)

    print(json.dumps(result))
    """
).strip()


class ResolverError(RuntimeError):
    """Raised when dependency resolution cannot proceed safely."""


@dataclass
class BootResult:
    returncode: int
    stdout: str
    stderr: str
    booted_cleanly: bool
    used_boot_timeout: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a Python application, install missing modules one by one with "
            "torch, torchaudio, and torchvision pinned as constraints, and freeze "
            "the final environment."
        )
    )
    parser.add_argument("target", help="Path to the Python script to launch.")
    parser.add_argument(
        "target_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to the target script.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used for probing, installing, and launching.",
    )
    parser.add_argument(
        "--constraints",
        default="constraints.txt",
        help="Output path for the generated torch/torchaudio/torchvision constraints file.",
    )
    parser.add_argument(
        "--requirements",
        default="safe_requirements.txt",
        help="Output path for the frozen environment snapshot.",
    )
    parser.add_argument(
        "--boot-timeout",
        type=float,
        default=10.0,
        help=(
            "Seconds the target must stay alive to count as a successful boot. "
            "Use 0 to wait for the target to exit normally."
        ),
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=20,
        help="Maximum dependency-install attempts before giving up.",
    )
    parser.add_argument(
        "--cwd",
        default=".",
        help="Working directory used when launching the target script.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print captured stdout/stderr for handled retry attempts.",
    )
    return parser.parse_args()


def normalize_target_args(target_args: Sequence[str]) -> list[str]:
    if target_args and target_args[0] == "--":
        return list(target_args[1:])
    return list(target_args)


def run_command(command: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
    )


def probe_framework_versions(python_executable: str) -> dict[str, str]:
    result = run_command([python_executable, "-c", VERSION_PROBE])
    if result.returncode != 0:
        raise ResolverError(
            "Unable to inspect the current PyTorch stack.\n"
            f"Command: {shlex.join([python_executable, '-c', VERSION_PROBE])}\n"
            f"stderr:\n{result.stderr.strip()}"
        )

    try:
        versions = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise ResolverError(
            f"Received malformed version probe output: {result.stdout!r}"
        ) from exc

    missing = [name for name, version in versions.items() if not version]
    if missing:
        names = ", ".join(missing)
        raise ResolverError(
            "The resolver refuses to continue unless the vendor-supported GPU stack "
            f"is already present. Missing: {names}. Preinstall torch, torchaudio, "
            "and torchvision first, then rerun this tool."
        )

    return {name: str(version) for name, version in versions.items()}


def write_constraints(constraints_path: Path, versions: dict[str, str]) -> None:
    lines = [
        "# Auto-generated by dgx-auto-resolver",
        "# These pins protect the vendor-tuned PyTorch/CUDA stack during pip installs.",
        f"torch=={versions['torch']}",
        f"torchaudio=={versions['torchaudio']}",
        f"torchvision=={versions['torchvision']}",
        "",
    ]
    constraints_path.write_text("\n".join(lines), encoding="utf-8")


def extract_missing_module(output: str) -> str | None:
    for pattern in (MODULE_NOT_FOUND_RE, IMPORT_NO_MODULE_RE):
        match = pattern.search(output)
        if match:
            return match.group(1)
    return None


def is_stdlib_module(name: str) -> bool:
    root = name.split(".", 1)[0]
    stdlib_names = getattr(sys, "stdlib_module_names", set())
    return root in stdlib_names


def resolve_distribution_name(module_name: str) -> str:
    if not VALID_IMPORT_RE.match(module_name):
        raise ResolverError(f"Refusing to install suspicious module name: {module_name!r}")
    if module_name.startswith("_") or is_stdlib_module(module_name):
        raise ResolverError(
            f"Refusing to pip install {module_name!r} because it looks like a stdlib/private module."
        )

    root = module_name.split(".", 1)[0]
    protected = root.lower() in PROTECTED_IMPORT_PREFIXES
    if protected:
        raise ResolverError(
            "Refusing to install a package tied to the accelerator stack "
            f"({module_name!r}). Resolve that dependency manually."
        )

    distribution = (
        MODULE_TO_PACKAGE.get(module_name)
        or MODULE_TO_PACKAGE.get(root)
        or root
    )
    if distribution.lower() in PROTECTED_DISTRIBUTIONS:
        raise ResolverError(
            f"Refusing to install protected distribution {distribution!r}."
        )
    return distribution


def print_retry_output(stdout: str, stderr: str) -> None:
    if stdout.strip():
        print("----- target stdout -----", file=sys.stderr)
        print(stdout.rstrip(), file=sys.stderr)
    if stderr.strip():
        print("----- target stderr -----", file=sys.stderr)
        print(stderr.rstrip(), file=sys.stderr)


def _coerce_output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_target(
    python_executable: str,
    target: Path,
    target_args: Sequence[str],
    boot_timeout: float,
    cwd: Path,
) -> BootResult:
    command = [python_executable, str(target), *target_args]
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if boot_timeout <= 0:
        stdout, stderr = process.communicate()
        return BootResult(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            booted_cleanly=process.returncode == 0,
            used_boot_timeout=False,
        )

    try:
        stdout, stderr = process.communicate(timeout=boot_timeout)
    except subprocess.TimeoutExpired as exc:
        partial_stdout = _coerce_output_text(exc.stdout)
        partial_stderr = _coerce_output_text(exc.stderr)
        partial_output = "\n".join(part for part in (partial_stdout, partial_stderr) if part)
        missing_module = extract_missing_module(partial_output)
        if missing_module:
            process.terminate()
            tail_stdout, tail_stderr = _finalize_process(process)
            return BootResult(
                returncode=process.returncode or 1,
                stdout=partial_stdout + tail_stdout,
                stderr=partial_stderr + tail_stderr,
                booted_cleanly=False,
                used_boot_timeout=False,
            )

        process.terminate()
        tail_stdout, tail_stderr = _finalize_process(process)
        return BootResult(
            returncode=0,
            stdout=partial_stdout + tail_stdout,
            stderr=partial_stderr + tail_stderr,
            booted_cleanly=True,
            used_boot_timeout=True,
        )

    return BootResult(
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        booted_cleanly=process.returncode == 0,
        used_boot_timeout=False,
    )


def _finalize_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    return _coerce_output_text(stdout), _coerce_output_text(stderr)


def install_distribution(
    python_executable: str,
    distribution: str,
    constraints_path: Path,
) -> None:
    command = [
        python_executable,
        "-m",
        "pip",
        "install",
        "--constraint",
        str(constraints_path),
        "--upgrade-strategy",
        "only-if-needed",
        "--disable-pip-version-check",
        distribution,
    ]
    print(f"[resolver] Installing {distribution!r} with protected constraints")
    result = run_command(command)
    combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    protected_conflict = (
        result.returncode != 0
        and any(name in combined_output for name in PROTECTED_DISTRIBUTIONS)
    )
    if protected_conflict:
        fallback_command = [
            python_executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--disable-pip-version-check",
            distribution,
        ]
        print(
            "[resolver] pip hit a protected-stack dependency conflict; "
            f"retrying {distribution!r} with --no-deps"
        )
        result = run_command(fallback_command)
    if result.returncode != 0:
        raise ResolverError(
            "pip install failed.\n"
            f"Command: {shlex.join(command)}\n"
            f"stdout:\n{result.stdout.strip()}\n\n"
            f"stderr:\n{result.stderr.strip()}"
        )


def freeze_environment(python_executable: str, requirements_path: Path) -> None:
    command = [
        python_executable,
        "-m",
        "pip",
        "freeze",
        "--exclude-editable",
    ]
    result = run_command(command)
    if result.returncode != 0:
        raise ResolverError(
            "Unable to freeze the environment.\n"
            f"Command: {shlex.join(command)}\n"
            f"stderr:\n{result.stderr.strip()}"
        )
    requirements_path.write_text(result.stdout, encoding="utf-8")


def ensure_paths_exist(paths: Iterable[Path]) -> None:
    for path in paths:
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)


def main() -> int:
    args = parse_args()
    target_args = normalize_target_args(args.target_args)
    target = Path(args.target).expanduser().resolve()
    if not target.is_file():
        raise SystemExit(f"Target script does not exist: {target}")

    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.exists():
        raise SystemExit(f"Working directory does not exist: {cwd}")

    constraints_path = Path(args.constraints).expanduser().resolve()
    requirements_path = Path(args.requirements).expanduser().resolve()
    ensure_paths_exist([constraints_path, requirements_path])

    versions = probe_framework_versions(args.python)
    write_constraints(constraints_path, versions)
    print(
        "[resolver] Locked protected framework versions to "
        f"{constraints_path}"
    )

    attempted_distributions: set[str] = set()
    for attempt in range(1, args.max_attempts + 1):
        print(
            "[resolver] Boot attempt "
            f"{attempt}/{args.max_attempts}: {shlex.join([args.python, str(target), *target_args])}"
        )
        result = run_target(
            args.python,
            target,
            target_args,
            args.boot_timeout,
            cwd,
        )
        combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        missing_module = extract_missing_module(combined_output)

        if result.booted_cleanly and not missing_module:
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            if result.used_boot_timeout:
                print(
                    "[resolver] Target survived the startup window; "
                    "treating that as a clean boot and stopping the probe process."
                )
            freeze_environment(args.python, requirements_path)
            print(
                "[resolver] Wrote frozen environment snapshot to "
                f"{requirements_path}"
            )
            return 0

        if not missing_module:
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            raise SystemExit(result.returncode or 1)

        if args.verbose:
            print_retry_output(result.stdout, result.stderr)

        distribution = resolve_distribution_name(missing_module)
        if distribution in attempted_distributions:
            raise ResolverError(
                "The same dependency was already installed but the import error "
                f"persisted: module={missing_module!r}, distribution={distribution!r}"
            )

        print(
            "[resolver] Detected missing module "
            f"{missing_module!r}; mapped to distribution {distribution!r}"
        )
        install_distribution(args.python, distribution, constraints_path)
        attempted_distributions.add(distribution)

    raise ResolverError(
        f"Reached the maximum number of attempts ({args.max_attempts}) without a clean boot."
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ResolverError as exc:
        print(f"[resolver] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
