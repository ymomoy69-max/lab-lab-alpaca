"""Audit log + SQLite persistence for observability."""
from __future__ import annotations

import json
import sqlite3
import datetime as dt
from pathlib import Path
from typing import Any


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class AuditStore:
    def __init__(self, db_path: str = "data/nexus.db", audit_dir: str = "data/audit"):
        self.db_path = Path(db_path)
        self.audit_dir = Path(audit_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.mcp_log = self.audit_dir / "mcp-audit.jsonl"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    type TEXT NOT NULL,
                    symbol TEXT,
                    message TEXT NOT NULL,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    strategy TEXT,
                    confidence REAL,
                    status TEXT NOT NULL,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    equity REAL,
                    daily_pnl REAL,
                    daily_pnl_pct REAL,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS trade_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    underlying TEXT NOT NULL,
                    occ_symbol TEXT,
                    strategy TEXT,
                    status TEXT NOT NULL,
                    pnl_ratio REAL,
                    reason TEXT,
                    payload TEXT
                );
                """
            )

    def event(self, type_: str, message: str, symbol: str | None = None, payload: dict | None = None) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO events (ts, type, symbol, message, payload) VALUES (?,?,?,?,?)",
                (_utc(), type_, symbol, message, json.dumps(payload or {})),
            )

    def decision(
        self,
        symbol: str,
        action: str,
        strategy: str | None,
        confidence: float,
        status: str,
        reason: str,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO decisions (ts, symbol, action, strategy, confidence, status, reason)
                   VALUES (?,?,?,?,?,?,?)""",
                (_utc(), symbol, action, strategy, confidence, status, reason),
            )

    def mcp(self, record: dict) -> None:
        record = dict(record)
        record.setdefault("ts", _utc())
        with self.mcp_log.open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def recent_events(self, limit: int = 100) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_decisions(self, limit: int = 50) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def save_portfolio(self, metrics: dict, positions: Any) -> None:
        import json

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO portfolio_snapshots (ts, equity, daily_pnl, daily_pnl_pct, payload)
                   VALUES (?,?,?,?,?)""",
                (
                    _utc(),
                    metrics.get("equity"),
                    metrics.get("daily_pnl"),
                    metrics.get("daily_pnl_pct"),
                    json.dumps({"metrics": metrics, "positions": positions}, default=str)[:50000],
                ),
            )

    def latest_portfolio(self) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def record_orders(self, symbol: str, results: Any) -> None:
        import json

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO orders (ts, symbol, payload) VALUES (?,?,?)",
                (_utc(), symbol, json.dumps(results, default=str)[:20000]),
            )

    def strategy_stats(self, limit: int = 100) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT symbol, strategy,
                       SUM(CASE WHEN status='SUBMITTED' THEN 1 ELSE 0 END) AS submitted,
                       SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) AS rejected,
                       SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) AS errors,
                       AVG(confidence) AS avg_confidence
                FROM decisions
                WHERE strategy IS NOT NULL
                GROUP BY symbol, strategy
                ORDER BY submitted DESC, rejected DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def equity_series(self, limit: int = 30) -> list[float]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT equity FROM portfolio_snapshots WHERE equity IS NOT NULL ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        vals = [float(r[0]) for r in reversed(rows) if r[0] is not None]
        return vals

    def latest_feedback_snapshot(self) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT payload FROM events WHERE type='feedback' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row or not row["payload"]:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    def record_outcome(
        self,
        underlying: str,
        strategy: str,
        *,
        symbol: str | None = None,
        status: str = "OPEN",
        pnl_ratio: float | None = None,
        reason: str = "",
        payload: dict | None = None,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO trade_outcomes
                   (ts, underlying, occ_symbol, strategy, status, pnl_ratio, reason, payload)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    _utc(),
                    underlying,
                    symbol,
                    strategy,
                    status,
                    pnl_ratio,
                    reason,
                    json.dumps(payload or {}, default=str)[:10000],
                ),
            )

    def outcome_stats(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT strategy,
                       COUNT(*) AS total,
                       SUM(CASE WHEN pnl_ratio > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN pnl_ratio <= 0 THEN 1 ELSE 0 END) AS losses,
                       AVG(pnl_ratio) AS avg_pnl_ratio
                FROM trade_outcomes
                WHERE status='CLOSED' AND strategy != 'exit'
                GROUP BY strategy
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def open_positions_tracked(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM trade_outcomes WHERE status='OPEN' ORDER BY id DESC LIMIT 50"""
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_open_ts(self, occ: str | None = None, underlying: str | None = None) -> str | None:
        """ISO timestamp of the most recent OPEN outcome for this contract or underlying."""
        with sqlite3.connect(self.db_path) as conn:
            if occ:
                row = conn.execute(
                    """SELECT ts FROM trade_outcomes
                       WHERE status='OPEN' AND occ_symbol=?
                       ORDER BY id DESC LIMIT 1""",
                    (occ,),
                ).fetchone()
                if row:
                    return str(row[0])
            if underlying:
                row = conn.execute(
                    """SELECT ts FROM trade_outcomes
                       WHERE status='OPEN' AND underlying=?
                       ORDER BY id DESC LIMIT 1""",
                    (underlying,),
                ).fetchone()
                if row:
                    return str(row[0])
        return None
