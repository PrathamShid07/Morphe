#!/usr/bin/env python
"""Inspect, apply or undo the pre-account data migration (see docs/USER_ACCOUNTS.md, "Migration").

    python backend/scripts/migrate_accounts.py plan                 # read-only: how many records would be shared / restricted
    python backend/scripts/migrate_accounts.py apply                # label unlabelled shared-plane records (idempotent; writes a JSON backup first)
    python backend/scripts/migrate_accounts.py rollback BACKUP.json # remove exactly the labels that run added
    python backend/scripts/migrate_accounts.py status               # past runs

Stop the API first when MORPHE uses the local Qdrant folder (only one process may open it). The API also runs `apply` itself at startup
(AUTO_MIGRATE=true), so this script is mainly for inspection and recovery.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.container import build_context  # noqa: E402
from app.migration import migrate, plan, rollback  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "apply", "rollback", "status"])
    ap.add_argument("backup", nargs="?", help="backup file written by `apply` (for rollback)")
    args = ap.parse_args()
    settings = get_settings().model_copy(update={"auto_migrate": False})
    ctx = build_context(settings, reconcile=False)
    try:
        if args.command == "plan":
            out = plan(ctx)
        elif args.command == "apply":
            out = migrate(ctx)
        elif args.command == "status":
            out = list(ctx.mongo.col("migrations").find({}, {"_id": 0}).sort("started_at", -1))
        else:
            if not args.backup:
                ap.error("rollback needs the backup file path")
            out = rollback(ctx, args.backup)
        print(json.dumps(out, indent=2, default=str))
        return 0
    finally:
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
