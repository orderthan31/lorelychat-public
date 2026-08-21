from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ops.sqlite_ops import check_database, online_backup, verify_restore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lorechat SQLite operations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="Verify a runtime database")
    check.add_argument("--db", required=True)
    check.add_argument("--full", action="store_true")

    backup = subparsers.add_parser("backup", help="Create an online SQLite backup")
    backup.add_argument("--db", required=True)
    backup.add_argument("--output-dir", required=True)
    backup.add_argument("--keep", type=int, default=14)

    restore = subparsers.add_parser("restore-verify", help="Copy and verify a backup as a writable restore")
    restore.add_argument("--backup", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "check":
        result = check_database(Path(args.db), full=args.full)
    elif args.command == "backup":
        result = online_backup(Path(args.db), Path(args.output_dir), keep=args.keep)
    else:
        result = verify_restore(Path(args.backup))
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
