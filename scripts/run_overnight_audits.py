"""Run overnight data audit scripts sequentially with logging.

Usage:
  python scripts/run_overnight_audits.py

Writes:
  reports/overnight_audit_run.log
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
REPORTS = PROJECT_ROOT / "reports"
LOG_PATH = REPORTS / "overnight_audit_run.log"

PLAYERS_DEALT_JSON = REPORTS / "players_dealt_counts.json"
PLAYERS_DEALT_LOG = REPORTS / "players_dealt_run.log"

STEPS = [
    ("count_players_dealt", ["scripts/count_players_dealt.py"]),
    ("count_unique_players", ["scripts/count_unique_players.py"]),
    ("audit_stake_strings", ["scripts/audit_stake_strings.py"]),
    ("check_duplicate_hands", ["scripts/check_duplicate_hands.py"]),
    ("count_hands_by_month", ["scripts/count_hands_by_month.py", "--plot"]),
]


OUTPUT_MARKERS = {
    "count_players_dealt": REPORTS / "players_dealt_counts.json",
    "count_unique_players": REPORTS / "player_counts.json",
    "audit_stake_strings": REPORTS / "stake_strings.json",
    "check_duplicate_hands": REPORTS / "duplicate_hands.json",
    "count_hands_by_month": REPORTS / "hand_counts_monthly.json",
}


def log(msg: str, log_file) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8", errors="replace"
        ), flush=True)
    log_file.write(line + "\n")
    log_file.flush()


def players_dealt_running() -> bool:
    if PLAYERS_DEALT_JSON.exists():
        return False
    if not PLAYERS_DEALT_LOG.exists():
        return False
    try:
        mtime = PLAYERS_DEALT_LOG.stat().st_mtime
        if time.time() - mtime > 600:
            return False
    except OSError:
        return False
    return True


def wait_for_players_dealt(log_file, poll_seconds: int = 120) -> None:
    if PLAYERS_DEALT_JSON.exists():
        log("players_dealt_counts.json exists — skipping wait", log_file)
        return
    if not players_dealt_running():
        log("count_players_dealt not detected as running — will run in pipeline", log_file)
        return

    log("Waiting for count_players_dealt.py to finish...", log_file)
    while players_dealt_running() and not PLAYERS_DEALT_JSON.exists():
        if PLAYERS_DEALT_LOG.exists():
            try:
                tail = PLAYERS_DEALT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in reversed(tail[-20:]):
                    if "/" in line and "files" in line.lower():
                        log(f"  progress: {line.strip()}", log_file)
                        break
            except OSError:
                pass
        time.sleep(poll_seconds)
    log("count_players_dealt finished or output present", log_file)


def run_step(name: str, args: list[str], log_file) -> float:
    cmd = [PYTHON] + args
    log(f"START {name}: {' '.join(cmd)}", log_file)
    start = time.time()
    proc = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.time() - start
    for line in proc.stdout.splitlines():
        log(f"  {line}", log_file)
    status = "OK" if proc.returncode == 0 else f"FAILED (exit {proc.returncode})"
    log(f"END {name}: {status} — {elapsed/60:.1f} min", log_file)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    return elapsed


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    total_start = time.time()
    step_times: dict[str, float] = {}

    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log("=" * 60, log_file)
        log("Overnight audit run started", log_file)
        wait_for_players_dealt(log_file)

        for name, args in STEPS:
            marker = OUTPUT_MARKERS.get(name)
            if marker and marker.exists():
                log(f"SKIP {name}: output already exists ({marker.name})", log_file)
                continue
            step_times[name] = run_step(name, args, log_file)

        total_elapsed = time.time() - total_start
        log("=" * 60, log_file)
        log("Overnight audit run complete", log_file)
        for name, elapsed in step_times.items():
            log(f"  {name}: {elapsed/60:.1f} min", log_file)
        log(f"Total: {total_elapsed/60:.1f} min", log_file)


if __name__ == "__main__":
    main()
