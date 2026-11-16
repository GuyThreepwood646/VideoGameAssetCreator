"""Reads the junit file pytest wrote, prints the passed, failed and skipped counts, and fails the build when:

  - any test failed or errored,
  - more tests skipped than --max-skipped (a skip is for something a runner legitimately lacks, such as Blender;
    a count above the limit means something that should be here is missing), or
  - fewer tests passed than --min-passed (a collection problem can hide whole files).

Usage: python check_pytest_counts.py report.xml --max-skipped N --min-passed N
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET


def counts(path: str) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) for s in suites)
    errors = sum(int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return {"total": total, "failed": failed, "errors": errors, "skipped": skipped,
            "passed": total - failed - errors - skipped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--max-skipped", type=int, required=True)
    ap.add_argument("--min-passed", type=int, required=True)
    a = ap.parse_args()
    c = counts(a.report)
    print(f"passed {c['passed']}, failed {c['failed']}, errors {c['errors']}, skipped {c['skipped']} (of {c['total']})")
    problems = []
    if c["failed"] or c["errors"]:
        problems.append(f"{c['failed']} failed and {c['errors']} errored")
    if c["skipped"] > a.max_skipped:
        problems.append(f"{c['skipped']} skipped, more than the {a.max_skipped} that are expected here; "
                        "something that should be installed or checked out is missing")
    if c["passed"] < a.min_passed:
        problems.append(f"only {c['passed']} passed, fewer than {a.min_passed}; tests may not have been collected")
    for p in problems:
        print("FAIL:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
