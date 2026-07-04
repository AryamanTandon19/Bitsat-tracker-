#!/usr/bin/env python3
"""Walk the audit-log hash chain and report any tampering.

Usage:  python verify_audit.py [--db watchdog.db]
Exit code 0 = chain intact, 1 = tampering detected / errors.
"""
from __future__ import annotations

import argparse
import sys

from app.db import Database


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="watchdog.db")
    args = ap.parse_args()

    db = Database(args.db)
    try:
        rows = db.audit_rows()
        ok, problems = db.verify_audit_chain()
    finally:
        db.close()

    print(f"audit rows: {len(rows)}")
    if ok:
        print("OK — hash chain intact, no tampering detected")
        return 0
    print("TAMPERING DETECTED:")
    for p in problems:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
