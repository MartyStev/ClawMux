"""
Encrypt existing plaintext OpenClaw credentials in the database.

One-shot maintenance script for the Fernet-at-rest rollout: after setting
CREDENTIAL_ENCRYPTION_KEY in the environment, run it once against the router
database. Already-encrypted rows are skipped, so it is safe to re-run.

Usage:
    CREDENTIAL_ENCRYPTION_KEY=... python scripts/encrypt_existing_credentials.py [--dry-run]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from src.core.config import settings  # noqa: E402
from src.core.crypto import encrypt_secret, is_encrypted  # noqa: E402
from src.core.database import dispose_engine, engine  # noqa: E402

SECRET_COLUMNS = ("private_key_b64", "device_token", "gateway_token")
# Aliased raw labels guarantee we read exactly what is stored
# (fernet prefix or legacy plaintext), independent of ORM type processors.
_RAW_SELECT = ", ".join(f"{col} AS raw_{i}" for i, col in enumerate(SECRET_COLUMNS))


async def main(dry_run: bool) -> int:
    if not settings.credential_encryption_key.strip():
        print("ERROR: CREDENTIAL_ENCRYPTION_KEY is not set in the environment")
        return 1

    async with engine.begin() as conn:
        rows = (await conn.execute(text(f"SELECT instance_uuid, {_RAW_SELECT} FROM instance"))).mappings().all()

        updated = 0
        for row in rows:
            params = {}
            for i, column in enumerate(SECRET_COLUMNS):
                value = row[f"raw_{i}"] or ""
                if value and not is_encrypted(value):
                    params[column] = encrypt_secret(value)
            if not params:
                continue
            set_clause = ", ".join(f"{col} = :{col}" for col in params)
            print(f"{'would encrypt' if dry_run else 'encrypting'}: {row['instance_uuid']} ({', '.join(params)})")
            if not dry_run:
                await conn.execute(
                    text(f"UPDATE instance SET {set_clause} WHERE instance_uuid = :uuid"),
                    {**params, "uuid": row["instance_uuid"]},
                )
            updated += 1

        print(f"Done. Rows scanned: {len(rows)}, encrypted: {updated}, dry_run: {dry_run}")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    async def _run() -> int:
        try:
            return await main(args.dry_run)
        finally:
            await dispose_engine()

    sys.exit(asyncio.run(_run()))
