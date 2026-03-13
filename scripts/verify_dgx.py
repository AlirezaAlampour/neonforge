#!/usr/bin/env python3
"""
verify_dgx.py -- DGX Spark AI Stack health verifier.

Checks:
  1. /healthz on all services (process alive)
  2. /readyz on all services (model loaded / ready)
  3. /smoke on all services (lightweight deterministic test) with latency
  4. Host memory: MemAvailable, SwapFree, used%, swap%
  5. Per-container RSS via docker stats
  6. Per-process GPU memory via nvidia-smi pmon (if available)
  7. Latency percentiles (p50/p95/p99) for smoke tests

Usage:
  python3 scripts/verify_dgx.py [--gateway-url http://localhost:8080] [--smoke]

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
  2 = critical failure (gateway unreachable)
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8080")

SERVICES = [
    {"name": "gateway", "url": GATEWAY_URL, "tier": "always-on"},
    {"name": "whisper", "url": None, "tier": "always-on"},
    {"name": "f5tts", "url": None, "tier": "warm"},
    {"name": "liveportrait", "url": None, "tier": "warm"},
    {"name": "lipsync", "url": None, "tier": "warm"},
    {"name": "wan21", "url": None, "tier": "lazy-start"},
]

COLORS = {
    "green": "\033[92m",
    "red": "\033[91m",
    "yellow": "\033[93m",
    "cyan": "\033[96m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def c(color: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"


def http_get(url: str, timeout: float = 5.0) -> tuple[int, dict | None, float]:
    """GET a URL, return (status_code, parsed_json_or_None, latency_seconds)."""
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            latency = time.monotonic() - t0
            try:
                return resp.status, json.loads(body), latency
            except json.JSONDecodeError:
                return resp.status, {"raw": body}, latency
    except urllib.error.HTTPError as e:
        return e.code, None, time.monotonic() - t0
    except Exception:
        return 0, None, time.monotonic() - t0


def percentiles(values: list[float]) -> dict:
    """Compute p50, p95, p99 from a list of values."""
    if not values:
        return {"p50": 0, "p95": 0, "p99": 0}
    s = sorted(values)
    n = len(s)
    return {
        "p50": round(s[n // 2], 4),
        "p95": round(s[min(int(n * 0.95), n - 1)], 4),
        "p99": round(s[min(int(n * 0.99), n - 1)], 4),
    }


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_healthz(name: str, base_url: str) -> tuple[bool, float]:
    status, data, latency = http_get(f"{base_url}/healthz")
    ok = status == 200
    sym = c("green", "PASS") if ok else c("red", "FAIL")
    print(f"  {sym}  /healthz  {name}  (HTTP {status}, {latency*1000:.0f}ms)")
    return ok, latency


def check_readyz(name: str, base_url: str) -> tuple[bool, float]:
    status, data, latency = http_get(f"{base_url}/readyz")
    ok = status == 200
    detail = ""
    if data and isinstance(data, dict):
        ml = data.get("model_loaded", "?")
        uma = data.get("uma_used_gb")
        detail = f"  model_loaded={ml}"
        if uma is not None:
            detail += f"  uma_used={uma}GB"
    sym = c("green", "PASS") if ok else c("yellow", "IDLE") if status == 200 else c("red", "FAIL")
    print(f"  {sym}  /readyz   {name}  (HTTP {status}, {latency*1000:.0f}ms){detail}")
    return ok, latency


def check_smoke_via_exec(name: str) -> tuple[bool, float]:
    """Run smoke test via docker exec and measure latency."""
    container = f"ai-{name}"
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            ["docker", "exec", container,
             "python", "-c",
             "import urllib.request; "
             "r=urllib.request.urlopen('http://localhost:8000/smoke', timeout=120); "
             "print(r.read().decode())"],
            capture_output=True, text=True, timeout=180,
        )
        latency = time.monotonic() - t0
        if result.returncode == 0:
            print(f"  {c('green', 'PASS')}  /smoke  {name}  ({latency*1000:.0f}ms)")
            return True, latency
        else:
            print(f"  {c('red', 'FAIL')}  /smoke  {name}: {result.stderr[:200]}")
            return False, latency
    except subprocess.TimeoutExpired:
        latency = time.monotonic() - t0
        print(f"  {c('yellow', 'TIMEOUT')}  /smoke  {name}  ({latency*1000:.0f}ms)")
        return False, latency
    except Exception as e:
        latency = time.monotonic() - t0
        print(f"  {c('yellow', 'ERROR')}  /smoke  {name}: {e}")
        return False, latency


# ---------------------------------------------------------------------------
# Host metrics
# ---------------------------------------------------------------------------
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


def report_host_memory() -> dict:
    print(f"\n{c('bold', '--- Host Memory (UMA) ---')}")
    mi = read_meminfo()
    total = mi.get("MemTotal", 0) / 1048576
    avail = mi.get("MemAvailable", 0) / 1048576
    used = total - avail
    pct = (used / total * 100) if total else 0

    swap_total = mi.get("SwapTotal", 0) / 1048576
    swap_free = mi.get("SwapFree", 0) / 1048576
    swap_used = swap_total - swap_free
    swap_pct = (swap_used / swap_total * 100) if swap_total else 0

    buffers = mi.get("Buffers", 0) / 1048576
    cached = mi.get("Cached", 0) / 1048576
    shmem = mi.get("Shmem", 0) / 1048576

    color = "green" if pct < 60 else "yellow" if pct < 80 else "red"
    print(f"  MemTotal:      {total:.1f} GB")
    print(f"  MemAvailable:  {c(color, f'{avail:.1f} GB')}  ({100-pct:.0f}% free)")
    print(f"  MemUsed:       {c(color, f'{used:.1f} GB ({pct:.0f}%)')}")
    print(f"  Buffers/Cache: {buffers:.1f} / {cached:.1f} GB")
    print(f"  Shmem:         {shmem:.1f} GB")

    swap_color = "green" if swap_pct < 10 else "yellow" if swap_pct < 50 else "red"
    print(f"  SwapTotal:     {swap_total:.1f} GB")
    print(f"  SwapFree:      {c(swap_color, f'{swap_free:.1f} GB')}")
    print(f"  SwapUsed:      {c(swap_color, f'{swap_used:.1f} GB ({swap_pct:.0f}%)')}")

    if swap_pct > 10:
        print(f"  {c('yellow', 'WARNING: Swap in use -- indicates UMA memory pressure.')}")

    return {
        "total_gb": round(total, 1),
        "available_gb": round(avail, 1),
        "used_gb": round(used, 1),
        "used_pct": round(pct, 1),
        "swap_total_gb": round(swap_total, 1),
        "swap_free_gb": round(swap_free, 1),
        "swap_used_gb": round(swap_used, 1),
        "swap_used_pct": round(swap_pct, 1),
    }


def report_container_rss() -> list[dict]:
    print(f"\n{c('bold', '--- Per-Container RSS ---')}")
    containers = []
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.PIDs}}\t{{.NetIO}}\t{{.BlockIO}}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"  {'CONTAINER':<20} {'MEM USAGE':<22} {'MEM%':<8} {'PIDs':<6} {'NET I/O':<22} {'BLOCK I/O'}")
            for line in result.stdout.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) >= 4 and "ai-" in parts[0]:
                    name = parts[0]
                    mem = parts[1] if len(parts) > 1 else "?"
                    pct = parts[2] if len(parts) > 2 else "?"
                    pids = parts[3] if len(parts) > 3 else "?"
                    net = parts[4] if len(parts) > 4 else "?"
                    bio = parts[5] if len(parts) > 5 else "?"
                    print(f"  {name:<20} {mem:<22} {pct:<8} {pids:<6} {net:<22} {bio}")
                    containers.append({
                        "name": name, "mem_usage": mem,
                        "mem_pct": pct, "pids": pids,
                    })
        else:
            print(f"  {c('yellow', 'docker stats unavailable or no ai- containers')}")
    except Exception as e:
        print(f"  {c('yellow', f'docker stats failed: {e}')}")
    return containers


def report_gpu_processes() -> list[dict]:
    print(f"\n{c('bold', '--- GPU Processes (per-process memory) ---')}")
    gpu_procs = []

    # Method 1: nvidia-smi pmon (per-process GPU utilization + framebuffer memory)
    try:
        result = subprocess.run(
            ["nvidia-smi", "pmon", "-c", "1", "-s", "um"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            header_shown = False
            for line in lines:
                if line.startswith("#"):
                    if not header_shown:
                        clean = line.lstrip("# ").strip()
                        if clean:
                            print(f"  {c('dim', clean)}")
                            header_shown = True
                    continue
                parts = line.split()
                if len(parts) >= 4 and parts[1] != "-":
                    pid = parts[1]
                    gpu_mem = parts[3] if len(parts) > 3 else "?"
                    proc_type = parts[2] if len(parts) > 2 else "?"
                    cmd = parts[-1] if len(parts) > 7 else "?"
                    print(f"  PID {pid:<8} type={proc_type:<4} fb_mem={gpu_mem:<8}MB cmd={cmd}")
                    gpu_procs.append({
                        "pid": pid, "type": proc_type,
                        "fb_mem_mb": gpu_mem, "cmd": cmd,
                    })
            if not gpu_procs:
                print(f"  {c('cyan', 'No active GPU processes (or UMA does not report per-process memory)')}")
        else:
            # Fallback: query-compute-apps
            result = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,name,used_memory",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                print(f"  {'PID':<10} {'Process':<40} {'GPU Memory'}")
                for line in result.stdout.strip().split("\n"):
                    print(f"  {line.strip()}")
            else:
                print(f"  {c('cyan', 'No GPU compute processes or UMA does not report per-process memory')}")
    except FileNotFoundError:
        print(f"  {c('yellow', 'nvidia-smi not found (run on host, not in container)')}")
    except Exception as e:
        print(f"  {c('yellow', f'GPU process query failed: {e}')}")

    # GPU temperature / utilization / clocks
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,power.draw,clocks.gr,clocks.mem",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            if len(parts) >= 3:
                temp, util, power = parts[0], parts[1], parts[2]
                clk = parts[3] if len(parts) > 3 else "?"
                mem_clk = parts[4] if len(parts) > 4 else "?"
                print(f"\n  GPU: {temp}C | Util: {util}% | Power: {power}W | Clk: {clk}/{mem_clk} MHz")
    except Exception:
        pass

    return gpu_procs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="DGX Spark AI Stack Verifier")
    parser.add_argument("--gateway-url", default=GATEWAY_URL)
    parser.add_argument("--smoke", action="store_true", help="Run smoke tests (may trigger model loading)")
    parser.add_argument("--json", action="store_true", help="Append structured JSON output")
    args = parser.parse_args()
    gateway_url = args.gateway_url.rstrip("/")

    print(c("bold", "=" * 64))
    print(c("bold", "  DGX Spark AI Stack -- Verification Report"))
    print(c("bold", f"  {time.strftime('%Y-%m-%d %H:%M:%S %Z')}"))
    print(c("bold", "=" * 64))

    results = {}
    all_passed = True
    smoke_latencies = {}

    # -- 1. Gateway --
    print(f"\n{c('bold', '--- Gateway ---')}")
    gw_alive, _ = check_healthz("gateway", gateway_url)
    if not gw_alive:
        print(c("red", "\nCRITICAL: Gateway unreachable. Cannot continue.\n"))
        sys.exit(2)
    check_readyz("gateway", gateway_url)

    # -- 2. Service health via gateway --
    print(f"\n{c('bold', '--- Service Health ---')}")
    status_code, svc_status, _ = http_get(f"{gateway_url}/services/status")
    if status_code == 200 and svc_status:
        for name, info in svc_status.items():
            alive = info.get("alive", False)
            ready = info.get("ready", False)
            sym_a = c("green", "ALIVE") if alive else c("red", "DOWN")
            sym_r = c("green", "READY") if ready else c("yellow", "IDLE")
            tier_info = ""
            for s in SERVICES:
                if s["name"] == name:
                    tier_info = f" [{s['tier']}]"
            last = info.get("last_activity")
            age = ""
            if last:
                ago = time.time() - last
                if ago < 60:
                    age = f"  (active {ago:.0f}s ago)"
                elif ago < 3600:
                    age = f"  (active {ago/60:.0f}m ago)"
                else:
                    age = f"  (active {ago/3600:.1f}h ago)"
            print(f"  {sym_a}  {sym_r}  {name}{tier_info}{age}")
            results[name] = {"alive": alive, "ready": ready}
            if not alive and name not in ("wan21",):  # wan21 is lazy
                all_passed = False
    else:
        print(c("red", "  Failed to fetch service status"))
        all_passed = False

    # -- 3. Smoke tests --
    if args.smoke:
        print(f"\n{c('bold', '--- Smoke Tests ---')}")
        for name, info in (svc_status or {}).items():
            if info.get("alive"):
                ok, latency = check_smoke_via_exec(name)
                results.setdefault(name, {})["smoke"] = ok
                smoke_latencies.setdefault(name, []).append(latency)
                if not ok:
                    all_passed = False

        if smoke_latencies:
            print(f"\n  {c('bold', 'Smoke Test Latency Percentiles:')}")
            print(f"  {'Service':<18} {'p50':>8} {'p95':>8} {'p99':>8}")
            for name, lats in sorted(smoke_latencies.items()):
                p = percentiles(lats)
                print(f"  {name:<18} {p['p50']*1000:>7.0f}ms {p['p95']*1000:>7.0f}ms {p['p99']*1000:>7.0f}ms")

    # -- 4. Memory gate status --
    print(f"\n{c('bold', '--- Memory Gate ---')}")
    _, mem_data, _ = http_get(f"{gateway_url}/memory")
    if mem_data:
        thresholds = mem_data.get("thresholds", {})
        used_pct = mem_data.get("used_pct", 0)
        avail = mem_data.get("available_gb", 0)
        hard = thresholds.get("hard_pct", 80)
        gate_color = "green" if used_pct < hard else "red"
        print(f"  UMA used:      {c(gate_color, f'{used_pct}%')} (hard limit: {hard}%)")
        print(f"  Available:     {avail:.1f} GB")
        print(f"  Reserves:      heavy={thresholds.get('reserve_heavy_gb', '?')}GB  "
              f"medium={thresholds.get('reserve_medium_gb', '?')}GB  "
              f"light={thresholds.get('reserve_light_gb', '?')}GB")
        gate_ok = used_pct < hard
        print(f"  Gate status:   {c('green', 'OPEN') if gate_ok else c('red', 'BLOCKED')}")

    # -- 5. Host metrics --
    mem_report = report_host_memory()
    container_rss = report_container_rss()
    gpu_procs = report_gpu_processes()

    # -- 6. Summary --
    print(f"\n{c('bold', '=' * 64)}")
    if all_passed:
        print(c("green", "  ALL CHECKS PASSED"))
    else:
        print(c("red", "  SOME CHECKS FAILED"))
    print(c("bold", "=" * 64))

    if args.json:
        output = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "passed": all_passed,
            "services": results,
            "host_memory": mem_report,
            "containers": container_rss,
            "gpu_processes": gpu_procs,
            "smoke_latency_percentiles": {
                name: percentiles(lats) for name, lats in smoke_latencies.items()
            } if smoke_latencies else {},
            "memory_gate": mem_data,
        }
        print(f"\n{json.dumps(output, indent=2)}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
