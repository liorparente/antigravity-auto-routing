import sqlite3
import json
from dataclasses import dataclass
from contextlib import closing
from typing import Optional

DB_PATH = "benchmark_history.db"

@dataclass
class EvaluationResult:
    model: str
    tier: str
    task_id: str
    ttft_ms: float
    tps: float
    cost_usd: float
    success: bool
    score: float
    error_msg: Optional[str] = None

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    model TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    ttft_ms REAL,
                    tps REAL,
                    cost_usd REAL,
                    success BOOLEAN,
                    score REAL,
                    error_msg TEXT
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_model ON benchmark_runs(model)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tier ON benchmark_runs(tier)')
        conn.commit()

def log_run(result: EvaluationResult):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                INSERT INTO benchmark_runs 
                (model, tier, task_id, ttft_ms, tps, cost_usd, success, score, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (result.model, result.tier, result.task_id, result.ttft_ms, result.tps, 
                  result.cost_usd, result.success, result.score, result.error_msg))
        conn.commit()

def get_historical_averages():
    """Returns aggregated stats per model and tier."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                SELECT 
                    model, 
                    tier,
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate,
                    AVG(ttft_ms) as avg_ttft_ms,
                    AVG(tps) as avg_tps,
                    AVG(score) as avg_score,
                    SUM(cost_usd) as total_cost
                FROM benchmark_runs
                GROUP BY model, tier
            ''')
            return [dict(row) for row in cursor.fetchall()]

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
