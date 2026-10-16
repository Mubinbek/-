"""
Работа с PostgreSQL через asyncpg.
Поддерживает и MySQL через aiomysql (переключается через DATABASE_DRIVER).
"""

import os
from datetime import datetime
from typing import Optional

DATABASE_DRIVER = os.getenv("DATABASE_DRIVER", "postgres")  # "postgres" или "mysql"


class Database:
    def __init__(self):
        self._pool = None

    # ── Подключение ────────────────────────────────────────────────────────

    async def connect(self):
        url = os.getenv("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL не задан в .env")

        if DATABASE_DRIVER == "mysql":
            import aiomysql
            # aiomysql принимает отдельные параметры, парсим URL
            # mysql://user:pass@host:3306/dbname
            from urllib.parse import urlparse
            p = urlparse(url)
            self._pool = await aiomysql.create_pool(
                host=p.hostname,
                port=p.port or 3306,
                user=p.username,
                password=p.password,
                db=p.path.lstrip("/"),
                autocommit=True,
                charset="utf8mb4",
            )
        else:
            import asyncpg
            self._pool = await asyncpg.create_pool(url)

    async def disconnect(self):
        if self._pool:
            self._pool.close()
            if DATABASE_DRIVER != "mysql":
                await self._pool.wait_closed()

    # ── DDL ───────────────────────────────────────────────────────────────

    async def create_tables(self):
        if DATABASE_DRIVER == "mysql":
            sql = """
            CREATE TABLE IF NOT EXISTS calls (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                call_sid        VARCHAR(64) UNIQUE,
                caller_phone    VARCHAR(32),
                caller_name     VARCHAR(128),
                purpose         VARCHAR(32),
                details         TEXT,
                appointment_datetime VARCHAR(64),
                summary         TEXT,
                full_transcript MEDIUMTEXT,
                called_at       DATETIME,
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql)
        else:
            sql = """
            CREATE TABLE IF NOT EXISTS calls (
                id                   SERIAL PRIMARY KEY,
                call_sid             TEXT UNIQUE,
                caller_phone         TEXT,
                caller_name          TEXT,
                purpose              TEXT,
                details              TEXT,
                appointment_datetime TEXT,
                summary              TEXT,
                full_transcript      TEXT,
                called_at            TIMESTAMPTZ,
                created_at           TIMESTAMPTZ DEFAULT NOW()
            );
            """
            async with self._pool.acquire() as conn:
                await conn.execute(sql)

    # ── CRUD ──────────────────────────────────────────────────────────────

    async def save_call(self, data: dict):
        called_at = data.get("called_at") or datetime.utcnow()
        if isinstance(called_at, str):
            try:
                called_at = datetime.fromisoformat(called_at)
            except ValueError:
                called_at = datetime.utcnow()

        if DATABASE_DRIVER == "mysql":
            sql = """
            INSERT INTO calls
                (call_sid, caller_phone, caller_name, purpose, details,
                 appointment_datetime, summary, full_transcript, called_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                caller_name          = VALUES(caller_name),
                purpose              = VALUES(purpose),
                details              = VALUES(details),
                appointment_datetime = VALUES(appointment_datetime),
                summary              = VALUES(summary),
                full_transcript      = VALUES(full_transcript)
            """
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, (
                        data.get("call_sid"),
                        data.get("caller_phone"),
                        data.get("caller_name"),
                        data.get("purpose"),
                        data.get("details"),
                        data.get("appointment_datetime"),
                        data.get("summary"),
                        data.get("full_transcript"),
                        called_at,
                    ))
        else:
            sql = """
            INSERT INTO calls
                (call_sid, caller_phone, caller_name, purpose, details,
                 appointment_datetime, summary, full_transcript, called_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (call_sid) DO UPDATE SET
                caller_name          = EXCLUDED.caller_name,
                purpose              = EXCLUDED.purpose,
                details              = EXCLUDED.details,
                appointment_datetime = EXCLUDED.appointment_datetime,
                summary              = EXCLUDED.summary,
                full_transcript      = EXCLUDED.full_transcript
            """
            async with self._pool.acquire() as conn:
                await conn.execute(sql,
                    data.get("call_sid"),
                    data.get("caller_phone"),
                    data.get("caller_name"),
                    data.get("purpose"),
                    data.get("details"),
                    data.get("appointment_datetime"),
                    data.get("summary"),
                    data.get("full_transcript"),
                    called_at,
                )

    async def get_calls(self, limit: int = 50) -> list[dict]:
        if DATABASE_DRIVER == "mysql":
            async with self._pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        "SELECT * FROM calls ORDER BY created_at DESC LIMIT %s", (limit,)
                    )
                    rows = await cur.fetchall()
                    return list(rows)
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM calls ORDER BY created_at DESC LIMIT $1", limit
                )
                return [dict(r) for r in rows]

    async def get_call_by_id(self, call_id: int) -> Optional[dict]:
        if DATABASE_DRIVER == "mysql":
            async with self._pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute("SELECT * FROM calls WHERE id = %s", (call_id,))
                    row = await cur.fetchone()
                    return dict(row) if row else None
        else:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM calls WHERE id = $1", call_id)
                return dict(row) if row else None
