"""Apply scripts/sql/apply_all.sql to the live Supabase database.

Prints the pre-change audit, applies the migration, then prints verification --
so the whole run is a record of what the database looked like before and after.

The password is never printed, never passed on a command line (where it would
land in shell history), and never written to disk by this script. Supply it as
SUPABASE_DB_PASSWORD in the environment or on a SUPABASE_DB_PASSWORD= line in
.env, which is gitignored:

    SUPABASE_DB_PASSWORD=... python scripts/apply_supabase_migration.py

Add --audit-only to inspect without changing anything.

Note this connects to the DIRECT host (db.<ref>.supabase.co), which is IPv6-only.
That is fine from a machine with IPv6. It is NOT suitable for Render, whose
outbound is IPv4-only -- production must use the pooler host instead.
"""
import asyncio
import os
import re
import sys
from pathlib import Path

HOST = "db.snpvgzitwbdjiioaqozc.supabase.co"
PORT = 5432
DATABASE = "postgres"
USER = "postgres"

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = ROOT / "scripts" / "sql" / "apply_all.sql"

AUDIT_TABLES = """
SELECT c.relname                                   AS table_name,
       c.relowner::regrole::text                   AS owner,
       c.relrowsecurity                            AS rls,
       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies,
       has_table_privilege('anon', c.oid, 'SELECT') AS anon_sel,
       has_table_privilege('anon', c.oid, 'INSERT') AS anon_ins,
       has_table_privilege('anon', c.oid, 'UPDATE') AS anon_upd,
       has_table_privilege('anon', c.oid, 'DELETE') AS anon_del
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname
"""

AUDIT_ROLES = """
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname IN ('postgres', 'anon', 'authenticated', 'service_role')
ORDER BY rolname
"""

AUDIT_DUPES = """
SELECT count(*) AS dupe_groups, coalesce(sum(copies) - count(*), 0) AS extra_rows
FROM (SELECT count(*) AS copies
      FROM public.playlist_songs
      GROUP BY playlist_id, song_id
      HAVING count(*) > 1) g
"""

AUDIT_CONSTRAINT = """
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = to_regclass('public.playlist_songs') AND contype = 'u'
"""


def password() -> str:
    pw = os.environ.get("SUPABASE_DB_PASSWORD")
    if pw:
        return pw
    env = ROOT / ".env"
    if env.exists():
        m = re.search(r"^SUPABASE_DB_PASSWORD=(.*)$", env.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    sys.exit(
        "No password found.\n"
        "Set SUPABASE_DB_PASSWORD in the environment, or add a line\n"
        "    SUPABASE_DB_PASSWORD=<your password>\n"
        f"to {env} (gitignored). Reset it at:\n"
        "    Supabase Dashboard > Project Settings > Database > Reset database password"
    )


def table(rows, headers) -> str:
    if not rows:
        return "    (no rows)"
    data = [[("t" if v is True else "f" if v is False else "" if v is None else str(v))
             for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in data)) for i, h in enumerate(headers)]
    out = ["    " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
           "    " + "  ".join("-" * w for w in widths)]
    out += ["    " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(r)) for r in data]
    return "\n".join(out)


async def audit(conn, label: str) -> list:
    print(f"\n===== {label} =====")

    roles = await conn.fetch(AUDIT_ROLES)
    print("\n  Roles:")
    print(table([tuple(r) for r in roles], ["rolname", "super", "bypassrls"]))

    tables = await conn.fetch(AUDIT_TABLES)
    print(f"\n  Tables in public ({len(tables)}):")
    print(table([tuple(r) for r in tables],
                ["table_name", "owner", "rls", "pol", "anon_sel", "anon_ins",
                 "anon_upd", "anon_del"]))

    exposed = [r["table_name"] for r in tables if not r["rls"] and r["anon_sel"]]
    print(f"\n  EXPOSED (rls off AND anon can select): {len(exposed)}"
          + (f" -> {', '.join(exposed)}" if exposed else ""))

    if any(r["table_name"] == "playlist_songs" for r in tables):
        d = await conn.fetchrow(AUDIT_DUPES)
        print(f"  playlist_songs duplicate (playlist_id, song_id) groups: "
              f"{d['dupe_groups']} ({d['extra_rows']} extra row(s) would be deleted)")
        cons = await conn.fetch(AUDIT_CONSTRAINT)
        print("  unique constraints: "
              + (", ".join(f"{c['conname']} {c['definition']}" for c in cons) or "none"))
    return tables


async def main() -> None:
    audit_only = "--audit-only" in sys.argv
    script = SQL_FILE.read_text(encoding="utf-8")

    try:
        import asyncpg
    except ImportError:
        sys.exit("asyncpg is not installed: python -m pip install asyncpg")

    print(f"Connecting to {USER}@{HOST}:{PORT}/{DATABASE} (SSL required) ...")
    try:
        conn = await asyncpg.connect(
            host=HOST, port=PORT, database=DATABASE, user=USER,
            password=password(), ssl="require", timeout=30,
        )
    except Exception as e:
        sys.exit(f"Connection failed: {type(e).__name__}: {e}")

    try:
        server = await conn.fetchval("SELECT version()")
        print(f"Connected. {server.split(' on ')[0]}")
        print(f"current_user = {await conn.fetchval('SELECT current_user')}")

        before = await audit(conn, "BEFORE -- current state, nothing changed yet")

        if audit_only:
            print("\n--audit-only given; stopping without applying anything.")
            return

        notices: list[str] = []
        conn.add_log_listener(lambda _c, m: notices.append(str(m)))

        print(f"\n===== APPLYING {SQL_FILE.name} =====")
        try:
            await conn.execute(script)
        except Exception as e:
            print(f"\n  FAILED: {type(e).__name__}: {e}")
            print("  The script is transaction-wrapped, so nothing was committed.")
            for n in notices:
                print(f"    {n}")
            sys.exit(1)

        for n in notices:
            print(f"  {n}")

        await audit(conn, "AFTER -- verification")

        tables = await conn.fetch(AUDIT_TABLES)
        bad = [r["table_name"] for r in tables
               if not r["rls"] or r["anon_sel"] or r["anon_ins"]
               or r["anon_upd"] or r["anon_del"]]
        cons = await conn.fetch(AUDIT_CONSTRAINT)
        has_uq = any(c["conname"] == "uq_playlist_song" for c in cons)

        print("\n===== RESULT =====")
        print(f"  tables before: {len(before)}, after: {len(tables)}")
        print(f"  all tables RLS-on and anon fully revoked: {'YES' if not bad else 'NO -> ' + ', '.join(bad)}")
        print(f"  uq_playlist_song present: {'YES' if has_uq else 'NO'}")
        print("\n" + ("PASS" if not bad and has_uq else "CHECK THE OUTPUT ABOVE"))
    finally:
        await conn.close()


asyncio.run(main())
