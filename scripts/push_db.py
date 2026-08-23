import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.db.base import Base
import app.models
from app.config.settings import settings


async def push_to_supabase():
    db_url = settings.DATABASE_URL
    print("=" * 60)
    print("PUSHING DATABASE SCHEMA TO SUPABASE POSTGRESQL")
    print("=" * 60)
    
    connect_args = {}
    if "postgres" in db_url:
        connect_args["ssl"] = "require"
    elif "sqlite" in db_url:
        connect_args["check_same_thread"] = False

    engine = create_async_engine(db_url, connect_args=connect_args, echo=False)
    
    # 1. Push all table models and indexes
    print("\n[1/2] Synchronizing table definitions & indexes...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("      [OK] Tables and indexes created successfully.")
        
        # 2. Query all tables in public schema
        print("\n[2/2] Verifying tables in Supabase public schema:")
        result = await conn.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            ORDER BY table_name;
        """))
        tables = [row[0] for row in result.fetchall()]
        for idx, t in enumerate(tables, 1):
            print(f"      ({idx:02d}) {t}")
            
    await engine.dispose()
    print("\n" + "=" * 60)
    print("SUPABASE DATABASE PUSH COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(push_to_supabase())
