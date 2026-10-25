"""SQLite 저장소. 이벤트는 추가만 하고(run_id, seq로 중복 제거), 화면용 요약은 조회 시점에 이벤트에서 계산합니다."""
import json
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH", "/data/db/rsi.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    spec TEXT,
    spec_version INTEGER,
    repo TEXT,
    source_path TEXT,
    started_at TEXT,
    ended_at TEXT,
    last_event_at TEXT,
    status TEXT,
    baseline_objective REAL,
    best_objective REAL,
    noise_threshold_ms REAL,
    planned_iterations INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT,
    type TEXT,
    iteration INTEGER,
    data TEXT,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS events_by_iteration ON events(run_id, iteration);
CREATE INDEX IF NOT EXISTS events_by_type ON events(type, ts);
-- 대시보드 → 오케스트레이터 명령 큐. 상태: queued → delivered → applied (또는 rejected/failed)
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    type TEXT NOT NULL,
    payload TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    result TEXT,
    created_at TEXT,
    delivered_at TEXT,
    applied_at TEXT
);
CREATE INDEX IF NOT EXISTS commands_by_status ON commands(status, id);
-- 호스트 오케스트레이터가 주기적으로 갱신하는 상태(heartbeat, 사용 가능한 Spec, 실행 중 런)
CREATE TABLE IF NOT EXISTS orchestrator (
    name TEXT PRIMARY KEY,
    heartbeat_at TEXT,
    state TEXT
);
-- Spec 버전 저장소(대시보드가 원본). 버전은 바꾸지 않고 새로 쌓습니다.
CREATE TABLE IF NOT EXISTS spec_versions (
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    yaml TEXT NOT NULL,
    title TEXT,
    note TEXT,
    source TEXT,
    created_at TEXT,
    PRIMARY KEY (name, version)
);
-- 대화형 설계(Design Studio) 세션과 메시지. 답변은 오케스트레이터가 Claude Code를 실행해 채웁니다.
CREATE TABLE IF NOT EXISTS design_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    repo TEXT,
    claude_session_id TEXT,
    status TEXT DEFAULT 'idle',
    last_error TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS design_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT,
    meta TEXT,
    ts TEXT
);
CREATE INDEX IF NOT EXISTS design_messages_by_session ON design_messages(session_id, id);
-- 구독 사용량 시계열 (루프의 usage 이벤트 + 설계 대화 중 오케스트레이터 기록)
CREATE TABLE IF NOT EXISTS usage_samples (
    ts TEXT NOT NULL,
    five_hour REAL,
    seven_day REAL,
    five_reset INTEGER,
    seven_reset INTEGER,
    source TEXT,
    run_id TEXT
);
CREATE INDEX IF NOT EXISTS usage_samples_ts ON usage_samples(ts);
-- 런 일정 예약: 요일(0=월)과 시간 창 안에서만 자동 시작하고, 창이 끝나면 '이번 반복 후 종료'를 보냅니다.
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    spec TEXT NOT NULL,
    version INTEGER,
    iterations INTEGER NOT NULL,
    dry_run INTEGER DEFAULT 0,
    days TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    last_started_at TEXT,
    last_run_id TEXT,
    created_at TEXT
);
-- 런 관리 정보 (runs 표는 서버 시작 때 이벤트로 다시 만들기 때문에, 사용자가 정한 값은 따로 보관)
CREATE TABLE IF NOT EXISTS run_meta (
    run_id TEXT PRIMARY KEY,
    archived INTEGER DEFAULT 0,
    note TEXT,
    updated_at TEXT
);
-- 감사 로그: 상태를 바꾸는 요청(누가·언제·어디서·무엇을·결과)
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, category TEXT, action TEXT, path TEXT, method TEXT, status INTEGER, ip TEXT, device TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_by_ts ON audit_log(ts);
-- 실행 노드 (홈서버 local + SSH로 붙는 Windows·Linux·Pi). 상태 열은 오케스트레이터가 갱신합니다.
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    name TEXT,
    transport TEXT NOT NULL DEFAULT 'ssh',
    host TEXT, port INTEGER DEFAULT 22, user TEXT,
    shell TEXT DEFAULT 'bash',
    os TEXT DEFAULT 'linux',
    tags TEXT DEFAULT '[]',
    workdir TEXT,
    enabled INTEGER DEFAULT 1,
    online INTEGER, last_check TEXT, info TEXT, error TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# 기존 DB에 나중에 추가된 열 — 없으면 추가합니다(이미 있으면 무시).
MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN title TEXT",
    "ALTER TABLE runs ADD COLUMN metric TEXT",
    "ALTER TABLE runs ADD COLUMN judge TEXT",
    "ALTER TABLE runs ADD COLUMN dry_run INTEGER",
    "ALTER TABLE design_sessions ADD COLUMN repo_git TEXT",   # 원본 저장소 git 상태(오케스트레이터가 기록): none | empty | ok
    "ALTER TABLE design_sessions ADD COLUMN repo_heavy TEXT",  # git 전 원본의 큰 폴더·파일 목록(JSON) — .gitignore 제안용
]


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass


def get_setting(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key, value):
    with connect() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value)))


def delete_setting(key):
    with connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
