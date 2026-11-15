"""Create a local owner-control key without printing the credential.

This intentionally has no application/configuration imports: running setup must
not initialize a database, read provider keys or start model/worker processes.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import secrets
import sys
from collections.abc import Sequence


def initialize_owner_key(target: Path) -> str:
    """Exclusively create a mode-0600 credential and return only its digest."""
    target = Path(target)
    key = secrets.token_urlsafe(32)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        stream.write(key + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(key.encode("ascii")).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="create a private owner key once")
    init.add_argument("--out", type=Path, default=Path("./secrets/lore-agent-owner.key"), help="private file outside uploads, static roots and source control")
    args = parser.parse_args(argv)
    try:
        digest = initialize_owner_key(args.out)
    except FileExistsError:
        print("Owner key file already exists; it was not overwritten.", file=sys.stderr)
        return 1
    except OSError:
        print("Could not create the private owner key file.", file=sys.stderr)
        return 1
    print(f"Private owner key created: {args.out}")
    print(f"LORE_AGENT_OWNER_KEY_SHA256={digest}")
    print("Set this digest on the API server. Open the key file locally and enter its contents in Lore Agent settings; never paste it into a chat or MCP configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
