#!/usr/bin/env python3
"""Assign train/val/test to a manifest, and refuse to let a source leak.

    python -m training.splits_cli                    # assign and report
    python -m training.splits_cli --check            # verify only, change nothing
    python -m training.splits_cli --val 0.2 --test 0.2

Run `--check` before every training run and in CI. It is instant, and the bug
it catches — clips of one event on both sides of the split — is one you would
otherwise find in a pilot, after quoting the number.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training import manifest as M
from training import splits as S


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default="training/data/manifest.jsonl")
    p.add_argument("--val", type=float, default=0.2)
    p.add_argument("--test", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--no-stratify", action="store_true")
    p.add_argument("--check", action="store_true",
                   help="verify the existing splits and exit non-zero if a "
                        "source video appears in more than one")
    args = p.parse_args(argv)

    path = Path(args.manifest)
    try:
        records = M.read(path)
    except M.ManifestError as e:
        print(e)
        return 1
    if not records:
        print(f"{path} is empty — mine some clips first:")
        print("  python -m training.clipmine --source meva --camera G424")
        return 1

    if args.check:
        try:
            S.check_separation(records)
        except S.SplitError as e:
            print(e)
            return 1
        unassigned = S.unassigned(records)
        if unassigned:
            print(f"{len(unassigned)} clips have no split yet — run without "
                  "--check to assign them")
            return 1
        print("source separation holds: no video appears in two splits\n")
        print(S.describe(records))
        return 0

    S.assign(records, val_fraction=args.val, test_fraction=args.test,
             seed=args.seed, stratify=not args.no_stratify)
    S.check_separation(records)
    M.write(path, records)          # rewritten in place, splits included
    print(f"assigned splits for {len(records)} clips in {path}\n")
    print(S.describe(records))

    print("\nThe test split is now written down. Treat it as untouched: run "
          "against it once,\nat the end, and whatever it says is the number "
          "you may quote.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
