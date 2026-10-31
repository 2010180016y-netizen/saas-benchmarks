"""
SlotBridge Korea — Database & Migration Manager
Handles SQLite / Cloudflare D1 local emulation, atomic transactions, and quota circuit breakers.
"""

import sqlite3
import os
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = BASE_DIR / "migrations"

class DatabaseManager:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.daily_write_count = 0
        self.max_daily_writes = 100_000 # Cloudflare D1 Free Tier limit
        self.circuit_breaker_ratio = 0.80 # 80% threshold

    def execute_script(self, sql_script: str):
        self.conn.executescript(sql_script)

    def run_migration_up(self):
        migration_file = MIGRATIONS_DIR / "0001_initial_schema.sql"
        with open(migration_file, "r", encoding="utf-8") as f:
            sql = f.read()
        self.execute_script(sql)

    def run_migration_down(self):
        rollback_file = MIGRATIONS_DIR / "0001_rollback.sql"
        with open(rollback_file, "r", encoding="utf-8") as f:
            sql = f.read()
        self.execute_script(sql)

    def seed_data(self):
        seeds_file = MIGRATIONS_DIR / "seeds.sql"
        with open(seeds_file, "r", encoding="utf-8") as f:
            sql = f.read()
        self.execute_script(sql)

    def check_circuit_breaker(self):
        """Throws RuntimeError if quota exceeds 80% to fail closed and prevent charges."""
        if self.daily_write_count >= (self.max_daily_writes * self.circuit_breaker_ratio):
            raise RuntimeError(
                f"Fail-Closed Circuit Breaker Triggered: Daily write count ({self.daily_write_count}) "
                f"exceeded 80% of Cloudflare D1 free quota ({self.max_daily_writes}). Halting new writes."
            )

    def execute(self, query: str, params: tuple = ()):
        query_upper = query.strip().upper()
        if any(query_upper.startswith(w) for w in ["INSERT", "UPDATE", "DELETE"]):
            self.check_circuit_breaker()
            self.daily_write_count += 1
        return self.conn.execute(query, params)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()
