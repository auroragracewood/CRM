"""Run every test_*.py acceptance script in the tests/ directory.

Each test file is self-contained: own temp DB, own setup, own assertions.
So we run them as separate subprocesses (matches how they're meant to be
run individually) and report PASS/FAIL per file.

Usage:
  python -m tests.run_all
  python -m tests.run_all --verbose   # stream each test's stdout live

Exit code: 0 if all pass, 1 if any fail.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent

# Ordered manually so a baseline failure (migrations) reports first.
# Anything not listed here that matches test_*.py is appended afterwards.
PRIORITY = [
    "test_migrations.py",      # if the runner is broken, nothing else matters
    "test_milestone1.py",      # M1+M2 baseline behaviour + transports
    "test_v1plus.py",          # v1+ services + plug-in framework
    "test_services_coverage.py",
    "test_error_paths.py",
    "test_transports.py",
    "test_webhook_delivery.py",
    "test_contact_lifecycle.py",
]


def _discover_tests() -> list[Path]:
    """Return test files in priority order, with any new test_*.py appended."""
    all_tests = {p.name: p for p in TESTS_DIR.glob("test_*.py")}
    ordered = []
    for name in PRIORITY:
        if name in all_tests:
            ordered.append(all_tests.pop(name))
    # Anything new (not in PRIORITY) lands at the end, alphabetically.
    for name in sorted(all_tests):
        ordered.append(all_tests[name])
    return ordered


def _run_one(test_file: Path, *, verbose: bool) -> tuple[bool, float, str]:
    """Run one test file as `python -m tests.<name>`. Returns (passed, secs, tail)."""
    module = f"tests.{test_file.stem}"
    t0 = time.time()
    if verbose:
        # Stream live; tail is the empty string since user is watching.
        rc = subprocess.run([sys.executable, "-m", module],
                            cwd=str(ROOT)).returncode
        elapsed = time.time() - t0
        return rc == 0, elapsed, ""
    # Capture so we can show the tail on failure.
    proc = subprocess.run(
        [sys.executable, "-m", module],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    elapsed = time.time() - t0
    if proc.returncode == 0:
        return True, elapsed, ""
    # Tail of combined output, helpful when something fails.
    combined = (proc.stdout or "") + (proc.stderr or "")
    tail_lines = combined.strip().splitlines()[-15:]
    return False, elapsed, "\n".join(tail_lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true",
                    help="stream each test's output live")
    args = ap.parse_args()

    tests = _discover_tests()
    if not tests:
        print("No test_*.py files found under tests/", file=sys.stderr)
        return 2

    print(f"Running {len(tests)} acceptance test file(s)...\n")
    results = []
    total_start = time.time()
    for t in tests:
        if not args.verbose:
            # Print the name first so the user sees progress even if a test
            # takes a while.
            print(f"  {t.name:<35}", end="", flush=True)
        else:
            print(f"\n========== {t.name} ==========")
        ok, secs, tail = _run_one(t, verbose=args.verbose)
        results.append((t.name, ok, secs, tail))
        if not args.verbose:
            badge = "PASS" if ok else "FAIL"
            print(f"  {badge}  ({secs:.1f}s)")
            if not ok and tail:
                # Indent the tail under the test name so it's clearly grouped.
                for line in tail.splitlines():
                    print(f"      | {line}")
    total = time.time() - total_start

    passed = sum(1 for _, ok, _, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{'-' * 60}")
    print(f"{passed}/{len(results)} passed, {failed} failed  ({total:.1f}s total)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
