# Muggle Word Cards — SQLite 数据层（标准库 sqlite3，不需要装数据库）
# -*- coding: utf-8 -*-
"""表结构说明：
- users：账号系统的用户表。email 唯一、password_hash 存 bcrypt 哈希（明文密码
  绝不入库）。老库通过 init_db 里的 ALTER TABLE 迁移补列，不用删库。
  默认用户（id=1，名字 local）是单人使用时代留下的本地数据，没有邮箱和密码，
  不能登录；接口只认登录用户的数据。
- groups：一个分组 = 一篇文章（或一次拍照提取）产出的一批词。
- words：单词卡本体。stage 是复习阶梯的位置（见 main.py 的 LADDER），
  due_on 是下次到期日（YYYY-MM-DD），fail_streak 是连续答错次数。
- reviews：每次复习的记录，只用于追踪，不参与排期计算。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL DEFAULT 'local',
    email         TEXT UNIQUE,
    password_hash TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    label      TEXT NOT NULL,
    source     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS words (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL REFERENCES groups(id),
    user_id     INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    word        TEXT NOT NULL,
    phonetic    TEXT NOT NULL DEFAULT '',
    meaning     TEXT NOT NULL,
    phrase      TEXT NOT NULL DEFAULT '',
    hook        TEXT NOT NULL DEFAULT '',
    trap        TEXT NOT NULL DEFAULT '',
    stage       INTEGER NOT NULL DEFAULT 0,
    due_on      TEXT NOT NULL,
    fail_streak INTEGER NOT NULL DEFAULT 0,
    mark        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_words_user_word ON words(user_id, word);
CREATE INDEX IF NOT EXISTS idx_words_due ON words(user_id, due_on);
CREATE INDEX IF NOT EXISTS idx_words_wanted ON words(user_id, fail_streak);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id     INTEGER NOT NULL REFERENCES words(id),
    known       INTEGER NOT NULL,
    pass_number INTEGER,
    reviewed_at TEXT NOT NULL
);
"""


def today_str() -> str:
    """今天的日期（服务器本地日期）。部署时请把服务器时区设为 Asia/Shanghai，
    否则 due_on 会按服务器时区算，和用户以为的“今天”差一天。"""
    return date.today().isoformat()


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    """建库（不存在则创建文件和表）。服务启动时调用一次。

    老库迁移：账号系统上线前建的库 users 表没有 email / password_hash 两列，
    这里用 ALTER TABLE 补上（列已存在时跳过），已有数据不用删。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        # 注意：SQLite 不允许 ALTER TABLE 直接加 UNIQUE 列，所以 email 先加普通列，
        # 再建唯一索引来实现唯一约束（NULL 不计入唯一性，老默认用户不受影响）。
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        if "password_hash" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")


def ensure_user(db_path: str | Path, user_id: int = 1, name: str = "local") -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, name, created_at) VALUES (?, ?, ?)",
            (user_id, name, today_str()),
        )


def create_user(db_path: str | Path, email: str, name: str, password_hash: str) -> int:
    """新建账号。邮箱已存在时抛 sqlite3.IntegrityError（调用方转 409）。"""
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (email, name, password_hash, today_str()),
        )
        return cur.lastrowid


def get_user_by_email(db_path: str | Path, email: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(db_path: str | Path, user_id: int) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def create_group(db_path: str | Path, user_id: int, label: str, source: str | None = None) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO groups (user_id, label, source, created_at) VALUES (?, ?, ?, ?)",
            (user_id, label, source, today_str()),
        )
        return cur.lastrowid


def word_exists(db_path: str | Path, user_id: int, word: str) -> bool:
    """去重判断：同一个用户库里已有这个词（不区分大小写、前后空格）就视为重复。"""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM words WHERE user_id = ? AND lower(trim(word)) = lower(trim(?)) LIMIT 1",
            (user_id, word),
        ).fetchone()
        return row is not None


def insert_word(
    db_path: str | Path,
    *,
    group_id: int,
    user_id: int,
    word: str,
    phonetic: str = "",
    meaning: str,
    phrase: str = "",
    hook: str = "",
    trap: str = "",
    due_on: str | None = None,
) -> int:
    """插入一张新卡。新词当天到期（due_on = 今天），stage=0，fail_streak=0。"""
    with connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO words
               (group_id, user_id, word, phonetic, meaning, phrase, hook, trap,
                stage, due_on, fail_streak, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?)""",
            (
                group_id, user_id, word.strip(), phonetic, meaning,
                phrase, hook, trap, due_on or today_str(), today_str(),
            ),
        )
        return cur.lastrowid


def list_groups(db_path: str | Path, user_id: int) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT g.id, g.label, g.source, g.created_at,
                      COUNT(w.id) AS word_count
               FROM groups g LEFT JOIN words w ON w.group_id = g.id
               WHERE g.user_id = ?
               GROUP BY g.id ORDER BY g.created_at DESC, g.id DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_words_in_group(db_path: str | Path, group_id: int, user_id: int) -> list[dict]:
    """某分组下的全部单词，只返回该用户的（分组归属在调用方先校验）。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM words WHERE group_id = ? AND user_id = ? ORDER BY id",
            (group_id, user_id),
        ).fetchall()
        return [dict(r) for r in rows]


def group_exists(db_path: str | Path, group_id: int, user_id: int) -> bool:
    """分组存在且属于该用户。别人的分组一律按不存在处理（接口返回 404）。"""
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT 1 FROM groups WHERE id = ? AND user_id = ? LIMIT 1",
            (group_id, user_id),
        ).fetchone() is not None


def get_word(db_path: str | Path, word_id: int) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        return dict(row) if row else None


def update_word_review(
    db_path: str | Path, word_id: int, *, stage: int, due_on: str, fail_streak: int
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE words SET stage = ?, due_on = ?, fail_streak = ? WHERE id = ?",
            (stage, due_on, fail_streak, word_id),
        )


def add_review(
    db_path: str | Path, word_id: int, *, known: bool, pass_number: int | None
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO reviews (word_id, known, pass_number, reviewed_at) VALUES (?, ?, ?, ?)",
            (word_id, 1 if known else 0, pass_number, today_str()),
        )


def due_words(db_path: str | Path, user_id: int, limit: int = 200) -> list[dict]:
    """今天到期（含过期未复习）的词，按到期日排序。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM words WHERE user_id = ? AND due_on <= ? ORDER BY due_on, id LIMIT ?",
            (user_id, today_str(), limit),
        ).fetchall()
        return [dict(r) for r in rows]


def wanted_words(db_path: str | Path, user_id: int, limit: int = 200) -> list[dict]:
    """通缉名单：连续答错 >= 2 次的词，连错最多的排前面。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM words WHERE user_id = ? AND fail_streak >= 2 "
            "ORDER BY fail_streak DESC, id LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
