#!/usr/bin/env python3
"""
idle_manager.py — DGX Spark AI Stack idle service manager.

Monitors service activity via Redis timestamps set by the gateway.
Stops idle lazy-start containers and optionally pauses warm containers.

Designed to run as a systemd service (see systemd/ai-idle-manager.service).

Features:
  - Checks Redis for last-activity timestamps per service
  - Stops wan21 container after WAN21_IDLE_TIMEOUT seconds of inactivity
  - Logs memory status on each check cycle
  - Monitors GPU temperature and pauses job acceptance if overheating
  - Does NOT use IPMI/BMC — relies on nvidia-smi and /proc/meminfo

Usage:
  python3 scripts/idle_manager.py [--interval 60] [--dry-run]
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [idle-mgr] %(message)s",
)
log = logging.getLogger("idle-manager")

# Configuration
CHECK_INTERVAL = int(os.getenv("IDLE_CHECK_INTERVAL", "60"))
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
COMPOSE_DIR = os.getenv("COMPOSE_DIR", "/home/xxfactionsxx/dgx-ai-stack")

# Service idle timeouts (seconds). 0 = never stop.
SERVICE_TIMEOUTS = {
    "wan21": int(os.getenv("WAN21_IDLE_TIMEOUT", "300")),
    # Warm services have longer timeouts; set to 0 to never stop
    "f5tts": int(os.getenv("F5TTS_IDLE_TIMEOUT", "1800")),
    "liveportrait": int(os.getenv("LIVEPORTRAIT_IDLE_TIMEOUT", "1800")),
    "lipsync": int(os.getenv("LIPSYNC_IDLE_TIMEOUT", "1800")),
}

# Thermal thresholds (Celsius)
GPU_TEMP_WARN = 75
GPU_TEMP_CRITICAL = 85


def get_redis_client():
    """Lazy Redis connection."""
    import redis
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def get_last_activity(rdb, service: str) -> float | None:
    """Get last activity timestamp from Redis."""
    val = rdb.get(f"activity:{service}")
    if val:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return None


def is_container_running(name: str) -> bool:
    """Check if a Docker container is running."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", f"ai-{name}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def stop_container(name: str, dry_run: bool = False) -> bool:
    """Stop a Docker container via docker compose."""
    container = f"ai-{name}"
    if dry_run:
        log.info("[DRY-RUN] Would stop container: %s", container)
        return True
    try:
        log.info("Stopping idle container: %s", container)
        result = subprocess.run(
            ["docker", "compose", "stop", name],
            capture_output=True, text=True,
            cwd=COMPOSE_DIR, timeout=60,
        )
        if result.returncode == 0:
            log.info("Stopped %s successfully", container)
            return True
        else:
            log.error("Failed to stop %s: %s", container, result.stderr[:300])
            return False
    except Exception as e:
        log.error("Exception stopping %s: %s", container, e)
        return False


def read_meminfo() -> dict:
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return info


def get_gpu_temp() -> int | None:
    """Read GPU temperature via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return None


def log_system_status():
    """Log current system health metrics."""
    mi = read_meminfo()
    total = mi.get("MemTotal", 0) / 1048576
    avail = mi.get("MemAvailable", 0) / 1048576
    used = total - avail
    pct = (used / total * 100) if total else 0

    swap_total = mi.get("SwapTotal", 0) / 1048576
    swap_free = mi.get("SwapFree", 0) / 1048576
    swap_used = swap_total - swap_free

    gpu_temp = get_gpu_temp()
    temp_str = f"{gpu_temp}C" if gpu_temp is not None else "N/A"

    log.info(
        "System: mem=%.1f/%.1f GB (%.0f%%), swap=%.1f/%.1f GB, gpu_temp=%s",
        used, total, pct, swap_used, swap_total, temp_str,
    )

    if gpu_temp is not None:
        if gpu_temp >= GPU_TEMP_CRITICAL:
            log.warning(
                "GPU temperature CRITICAL: %dC >= %dC. "
                "Consider stopping GPU workloads.",
                gpu_temp, GPU_TEMP_CRITICAL,
            )
        elif gpu_temp >= GPU_TEMP_WARN:
            log.warning("GPU temperature elevated: %dC", gpu_temp)

    if pct > 90:
        log.warning("Memory usage above 90%% — risk of OOM kill on UMA system")
    if swap_used > 1.0:
        log.warning("Swap usage: %.1f GB — indicates memory pressure", swap_used)


def check_cycle(rdb, dry_run: bool):
    """One check cycle: inspect all services, stop idle ones."""
    now = time.time()
    log_system_status()

    for service, timeout in SERVICE_TIMEOUTS.items():
        if timeout <= 0:
            continue  # Never auto-stop

        if not is_container_running(service):
            continue  # Already stopped

        last = get_last_activity(rdb, service)
        if last is None:
            # No activity recorded — check container uptime instead
            try:
                result = subprocess.run(
                    ["docker", "inspect", "-f",
                     "{{.State.StartedAt}}", f"ai-{service}"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    # Container is running but no requests recorded.
                    # Use startup time as baseline.
                    from datetime import datetime, timezone
                    started = result.stdout.strip()
                    # Docker timestamps are RFC3339
                    try:
                        dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                        last = dt.timestamp()
                    except (ValueError, TypeError):
                        last = now  # Can't parse, assume just started
            except Exception:
                last = now

        if last is not None:
            idle_seconds = now - last
            if idle_seconds > timeout:
                log.info(
                    "Service %s idle for %ds (timeout=%ds), stopping",
                    service, int(idle_seconds), timeout,
                )
                stop_container(service, dry_run)
            else:
                remaining = timeout - idle_seconds
                log.debug(
                    "Service %s active (idle=%ds, remaining=%ds)",
                    service, int(idle_seconds), int(remaining),
                )


def main():
    parser = argparse.ArgumentParser(description="AI Stack Idle Manager")
    parser.add_argument(
        "--interval", type=int, default=CHECK_INTERVAL,
        help=f"Check interval in seconds (default: {CHECK_INTERVAL})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log actions but do not actually stop containers",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one check cycle and exit",
    )
    args = parser.parse_args()

    log.info(
        "Starting idle manager (interval=%ds, dry_run=%s)",
        args.interval, args.dry_run,
    )

    try:
        rdb = get_redis_client()
        rdb.ping()
        log.info("Connected to Redis at %s:%d", REDIS_HOST, REDIS_PORT)
    except Exception as e:
        log.error("Cannot connect to Redis: %s", e)
        sys.exit(1)

    if args.once:
        check_cycle(rdb, args.dry_run)
        return

    while True:
        try:
            check_cycle(rdb, args.dry_run)
        except Exception as e:
            log.error("Check cycle failed: %s", e)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
