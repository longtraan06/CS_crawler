import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pagecrawl.models import Comment, Post, content_hash


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, db_path: Path, jsonl_path: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = Path(jsonl_path) if jsonl_path else self.db_path.parent / "posts.jsonl"
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS collections (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT UNIQUE NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  page_url TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  status TEXT NOT NULL,
                  stop_reason TEXT,
                  new_count INTEGER NOT NULL DEFAULT 0,
                  skip_count INTEGER NOT NULL DEFAULT 0,
                  updated_count INTEGER NOT NULL DEFAULT 0,
                  rounds INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS posts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  post_id TEXT UNIQUE NOT NULL,
                  page_url TEXT NOT NULL,
                  author TEXT NOT NULL,
                  created_at TEXT,
                  time_label TEXT,
                  like_count INTEGER NOT NULL,
                  comment_count INTEGER NOT NULL,
                  text TEXT NOT NULL,
                  image_urls_json TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  permalink TEXT,
                  id_unstable INTEGER NOT NULL DEFAULT 0,
                  raw_json TEXT,
                  first_seen_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_posts_page ON posts(page_url);
            """
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO collections (id, name, created_at) VALUES (1, ?, ?)",
                ("mặc định", _now()),
            )
            post_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(posts)")}
            if "collection_id" not in post_columns:
                self._conn.execute("ALTER TABLE posts ADD COLUMN collection_id INTEGER NOT NULL DEFAULT 1")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_collection ON posts(collection_id)")
            self._migrate_posts_composite_key()
            self._drop_feedback_column()
            self._ensure_comments_schema()
            self._conn.commit()

    def _drop_feedback_column(self) -> None:
        """Bỏ cột legacy feedback_id (thời xưa dùng để nối bình luận với bài)."""
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(posts)")}
        if "feedback_id" not in columns:
            return
        self._conn.executescript(
            """
            CREATE TABLE posts_clean (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              post_id TEXT NOT NULL,
              page_url TEXT NOT NULL,
              author TEXT NOT NULL,
              created_at TEXT,
              time_label TEXT,
              like_count INTEGER NOT NULL,
              comment_count INTEGER NOT NULL,
              text TEXT NOT NULL,
              image_urls_json TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              permalink TEXT,
              id_unstable INTEGER NOT NULL DEFAULT 0,
              raw_json TEXT,
              first_seen_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              collection_id INTEGER NOT NULL DEFAULT 1,
              UNIQUE(post_id, collection_id)
            );
            INSERT OR IGNORE INTO posts_clean SELECT id, post_id, page_url, author, created_at,
              time_label, like_count, comment_count, text, image_urls_json, content_hash,
              permalink, id_unstable, raw_json, first_seen_at, updated_at, collection_id
              FROM posts;
            DROP TABLE posts;
            ALTER TABLE posts_clean RENAME TO posts;
            CREATE INDEX IF NOT EXISTS idx_posts_page ON posts(page_url);
            CREATE INDEX IF NOT EXISTS idx_posts_collection ON posts(collection_id);
            """
        )

    def _ensure_comments_schema(self) -> None:
        """Bảng bình luận + cột đánh dấu bài nào đã lấy bình luận (để chạy lại không lặp)."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS comments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              collection_id INTEGER NOT NULL DEFAULT 1,
              post_id TEXT NOT NULL,
              comment_id TEXT NOT NULL,
              parent_id TEXT,
              depth INTEGER NOT NULL DEFAULT 0,
              author TEXT NOT NULL DEFAULT '',
              text TEXT NOT NULL DEFAULT '',
              created_at TEXT,
              like_count INTEGER NOT NULL DEFAULT 0,
              saved_at TEXT NOT NULL,
              UNIQUE(collection_id, comment_id)
            );
            CREATE INDEX IF NOT EXISTS idx_comments_post
              ON comments(collection_id, post_id, depth);
            CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(posts)")}
        # pending: chưa lấy | done: đã lấy xong | error: lỗi, có thể thử lại
        for name, decl in (
            ("comments_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("comments_saved", "INTEGER NOT NULL DEFAULT 0"),
            ("comments_done_at", "TEXT"),
            ("comments_error", "TEXT"),
        ):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE posts ADD COLUMN {name} {decl}")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_comments_status ON posts(collection_id, comments_status)"
        )

    def _migrate_posts_composite_key(self) -> None:
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='posts'"
        ).fetchone()
        if row is None:
            return
        create_sql = row[0] or ""
        if "UNIQUE(post_id, collection_id)" in create_sql:
            return
        if "post_id TEXT UNIQUE" not in create_sql and "post_id TEXT NOT NULL UNIQUE" not in create_sql:
            return
        self._conn.executescript(
            """
            CREATE TABLE posts_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              post_id TEXT NOT NULL,
              page_url TEXT NOT NULL,
              author TEXT NOT NULL,
              created_at TEXT,
              time_label TEXT,
              like_count INTEGER NOT NULL,
              comment_count INTEGER NOT NULL,
              text TEXT NOT NULL,
              image_urls_json TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              permalink TEXT,
              id_unstable INTEGER NOT NULL DEFAULT 0,
              raw_json TEXT,
              first_seen_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              collection_id INTEGER NOT NULL DEFAULT 1,
              UNIQUE(post_id, collection_id)
            );
            INSERT OR IGNORE INTO posts_new SELECT id, post_id, page_url, author, created_at,
              time_label, like_count, comment_count, text, image_urls_json, content_hash,
              permalink, id_unstable, raw_json, first_seen_at, updated_at, collection_id
              FROM posts;
            DROP TABLE posts;
            ALTER TABLE posts_new RENAME TO posts;
            CREATE INDEX IF NOT EXISTS idx_posts_page ON posts(page_url);
            CREATE INDEX IF NOT EXISTS idx_posts_collection ON posts(collection_id);
            """
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_run(self, page_url: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO runs (page_url, started_at, status) VALUES (?, ?, ?)",
                (page_url, _now(), "starting"),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_run(self, run_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "stop_reason",
            "new_count",
            "skip_count",
            "updated_count",
            "rounds",
            "finished_at",
        }
        keys = [key for key in fields if key in allowed]
        if not keys:
            return
        assignments = ", ".join(f"{key} = ?" for key in keys)
        values = [fields[key] for key in keys]
        with self._lock:
            self._conn.execute(
                f"UPDATE runs SET {assignments} WHERE id = ?",
                (*values, run_id),
            )
            self._conn.commit()

    def finish_run(
        self,
        run_id: int,
        status: str,
        stop_reason: str,
        new_count: int,
        skip_count: int,
        updated_count: int,
        rounds: int,
    ) -> None:
        self.update_run(
            run_id,
            status=status,
            stop_reason=stop_reason,
            new_count=new_count,
            skip_count=skip_count,
            updated_count=updated_count,
            rounds=rounds,
            finished_at=_now(),
        )

    def create_collection(self, name: str) -> dict[str, Any]:
        name = name.strip()
        with self._lock:
            row = self._conn.execute("SELECT id, name, created_at FROM collections WHERE name = ?", (name,)).fetchone()
            if row is None:
                cursor = self._conn.execute(
                    "INSERT INTO collections (name, created_at) VALUES (?, ?)",
                    (name, _now()),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT id, name, created_at FROM collections WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
        return {"id": int(row["id"]), "name": row["name"], "created_at": row["created_at"]}

    def get_collection(self, collection_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, created_at FROM collections WHERE id = ?", (collection_id,)
            ).fetchone()
        if row is None:
            return None
        return {"id": int(row["id"]), "name": row["name"], "created_at": row["created_at"]}

    def get_collection_by_name(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, created_at FROM collections WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return None
        return {"id": int(row["id"]), "name": row["name"], "created_at": row["created_at"]}

    def list_collections(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT c.id, c.name, c.created_at, COUNT(p.id) AS post_count
                FROM collections c
                LEFT JOIN posts p ON p.collection_id = c.id
                GROUP BY c.id
                ORDER BY c.id
                """
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "name": row["name"],
                "created_at": row["created_at"],
                "post_count": int(row["post_count"]),
            }
            for row in rows
        ]

    def delete_collection(self, collection_id: int) -> tuple[int, int]:
        with self._lock:
            self._conn.execute("DELETE FROM comments WHERE collection_id = ?", (collection_id,))
            posts_del = int(self._conn.execute("DELETE FROM posts WHERE collection_id = ?", (collection_id,)).rowcount)
            cols_del = int(self._conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,)).rowcount)
            self._conn.commit()
        return cols_del, posts_del

    def get_post(self, post_id: str, collection_id: int | None = None) -> dict[str, Any] | None:
        with self._lock:
            if collection_id is not None:
                row = self._conn.execute(
                    "SELECT * FROM posts WHERE post_id = ? AND collection_id = ?",
                    (post_id, collection_id),
                ).fetchone()
            else:
                row = self._conn.execute("SELECT * FROM posts WHERE post_id = ?", (post_id,)).fetchone()
        if row is None:
            return None
        return _row_to_post(row)

    def upsert(self, page_url: str, post: Post, *, collection_id: int = 1) -> dict[str, Any]:
        digest = content_hash(post)
        now = _now()
        images = json.dumps(post.image_urls, ensure_ascii=False)
        raw = json.dumps(post.raw, ensure_ascii=False) if post.raw is not None else None
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, first_seen_at FROM posts WHERE post_id = ? AND collection_id = ?",
                (post.post_id, collection_id),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    """
                    INSERT INTO posts (
                      post_id, page_url, author, created_at, time_label, like_count,
                      comment_count, text, image_urls_json, content_hash, permalink,
                      id_unstable, raw_json, first_seen_at, updated_at, collection_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        post.post_id,
                        page_url,
                        post.author,
                        post.created_at,
                        post.time_label,
                        post.like_count,
                        post.comment_count,
                        post.text,
                        images,
                        digest,
                        post.permalink,
                        1 if post.id_unstable else 0,
                        raw,
                        now,
                        now,
                        collection_id,
                    ),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE posts SET
                      page_url = ?, author = ?, created_at = ?, time_label = ?,
                      like_count = ?, comment_count = ?, text = ?, image_urls_json = ?,
                      content_hash = ?, permalink = ?, id_unstable = ?, raw_json = ?,
                      updated_at = ?
                    WHERE post_id = ? AND collection_id = ?
                    """,
                    (
                        page_url,
                        post.author,
                        post.created_at,
                        post.time_label,
                        post.like_count,
                        post.comment_count,
                        post.text,
                        images,
                        digest,
                        post.permalink,
                        1 if post.id_unstable else 0,
                        raw,
                        now,
                        post.post_id,
                        collection_id,
                    ),
                )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM posts WHERE post_id = ? AND collection_id = ?",
                (post.post_id, collection_id),
            ).fetchone()
        saved = _row_to_post(row)
        self._append_jsonl(saved)
        return saved

    def list_posts(
        self,
        page_url: str = "",
        query: str = "",
        offset: int = 0,
        limit: int = 50,
        collection_id: int | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if page_url:
            clauses.append("page_url = ?")
            params.append(page_url)
        if collection_id is not None:
            clauses.append("collection_id = ?")
            params.append(collection_id)
        if query:
            clauses.append("(author LIKE ? OR text LIKE ?)")
            like = f"%{query.replace('%', '').replace('_', '')}%"
            params.extend([like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM posts {where}", params).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT * FROM posts {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return {"total": int(total), "posts": [_row_to_post(row) for row in rows]}

    def count_for_url(self, page_url: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM posts WHERE page_url = ?", (page_url,)).fetchone()
        return int(row[0])

    # ------------------------------------------------------------- bình luận

    def list_posts_needing_comments(
        self,
        collection_id: int,
        min_comments: int = 2,
        limit: int | None = None,
        retry_errors: bool = True,
    ) -> list[dict[str, Any]]:
        """Bài chưa lấy bình luận, có đủ ngưỡng bình luận và có link để mở.

        Bài đánh dấu 'done' bị bỏ qua nên bấm Dừng giữa chừng rồi chạy tiếp sẽ
        không lặp lại bài đã xong.

        ``limit`` bỏ trống (hoặc <= 0) nghĩa là lấy hết, không giới hạn.
        """
        sql = (
            "SELECT * FROM posts WHERE collection_id = ? AND comment_count >= ? "
            "AND permalink IS NOT NULL AND permalink != '' "
            "AND comments_status != 'done' "
        )
        params: list[Any] = [collection_id, min_comments]
        if not retry_errors:
            sql += "AND comments_status != 'error' "
        sql += "ORDER BY id DESC"
        if limit and limit > 0:
            # -1 là "không giới hạn" với SQLite.
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_post(row) for row in rows]

    def save_comments(
        self,
        collection_id: int,
        post_id: str,
        comments: list[Comment],
    ) -> int:
        """Ghi bình luận (chạy lại sẽ cập nhật chứ không nhân bản). Trả về tổng số."""
        now = _now()
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO comments
                  (collection_id, post_id, comment_id, parent_id, depth, author,
                   text, created_at, like_count, saved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_id, comment_id) DO UPDATE SET
                  parent_id = excluded.parent_id,
                  depth = excluded.depth,
                  author = excluded.author,
                  text = excluded.text,
                  created_at = excluded.created_at,
                  like_count = excluded.like_count,
                  saved_at = excluded.saved_at
                """,
                [
                    (
                        collection_id,
                        post_id,
                        item.comment_id,
                        item.parent_id,
                        item.depth,
                        item.author,
                        item.text,
                        item.created_at or None,
                        item.like_count,
                        now,
                    )
                    for item in comments
                ],
            )
            total = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM comments WHERE collection_id = ? AND post_id = ?",
                    (collection_id, post_id),
                ).fetchone()[0]
            )
            self._conn.execute(
                "UPDATE posts SET comments_status = 'done', comments_saved = ?, "
                "comments_done_at = ?, comments_error = NULL "
                "WHERE post_id = ? AND collection_id = ?",
                (total, now, post_id, collection_id),
            )
            self._conn.commit()
        return total

    def mark_comments_error(self, collection_id: int, post_id: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE posts SET comments_status = 'error', comments_error = ? "
                "WHERE post_id = ? AND collection_id = ?",
                (error[:500], post_id, collection_id),
            )
            self._conn.commit()

    def reset_comments(self, collection_id: int, post_id: str | None = None) -> int:
        """Xoá bình luận đã lưu và đặt lại trạng thái để crawl lại."""
        now = _now()
        with self._lock:
            if post_id:
                self._conn.execute(
                    "DELETE FROM comments WHERE collection_id = ? AND post_id = ?",
                    (collection_id, post_id),
                )
                n = self._conn.execute(
                    "UPDATE posts SET comments_status = 'pending', comments_saved = 0, "
                    "comments_done_at = NULL, comments_error = NULL "
                    "WHERE post_id = ? AND collection_id = ?",
                    (post_id, collection_id),
                ).rowcount
            else:
                self._conn.execute("DELETE FROM comments WHERE collection_id = ?", (collection_id,))
                n = self._conn.execute(
                    "UPDATE posts SET comments_status = 'pending', comments_saved = 0, "
                    "comments_done_at = NULL, comments_error = NULL WHERE collection_id = ?",
                    (collection_id,),
                ).rowcount
            self._conn.execute(
                "UPDATE runs SET finished_at = ? WHERE finished_at IS NULL", (now,)
            )
            self._conn.commit()
        return int(n)

    def list_comments(
        self,
        collection_id: int,
        post_id: str,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self._lock:
            total = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM comments WHERE collection_id = ? AND post_id = ?",
                    (collection_id, post_id),
                ).fetchone()[0]
            )
            rows = self._conn.execute(
                "SELECT * FROM comments WHERE collection_id = ? AND post_id = ? "
                "ORDER BY depth ASC, id ASC LIMIT ? OFFSET ?",
                (collection_id, post_id, limit, offset),
            ).fetchall()
        return {"total": total, "comments": [_row_to_comment(row) for row in rows]}

    def comment_progress(self, collection_id: int, min_comments: int = 2) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                  COUNT(*) AS tong_bai,
                  SUM(CASE WHEN comment_count >= ? AND permalink IS NOT NULL AND permalink != ''
                           THEN 1 ELSE 0 END) AS du_dieu_lien_ket,
                  SUM(CASE WHEN comments_status = 'done' THEN 1 ELSE 0 END) AS xong,
                  SUM(CASE WHEN comments_status = 'error' THEN 1 ELSE 0 END) AS loi,
                  SUM(CASE WHEN comments_status = 'pending' AND comment_count >= ?
                                AND permalink IS NOT NULL AND permalink != ''
                           THEN 1 ELSE 0 END) AS cho
                FROM posts WHERE collection_id = ?
                """,
                (min_comments, min_comments, collection_id),
            ).fetchone()
            saved = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM comments WHERE collection_id = ?", (collection_id,)
                ).fetchone()[0]
            )
        return {
            "posts": int(row["tong_bai"] or 0),
            "eligible": int(row["du_dieu_lien_ket"] or 0),
            "done": int(row["xong"] or 0),
            "error": int(row["loi"] or 0),
            "pending": int(row["cho"] or 0),
            "saved": saved,
        }

    def _append_jsonl(self, saved: dict[str, Any]) -> None:
        line = {
            "page_url": saved["page_url"],
            "post_id": saved["post_id"],
            "author": saved["author"],
            "created_at": saved["created_at"],
            "time_label": saved["time_label"],
            "like_count": saved["like_count"],
            "comment_count": saved["comment_count"],
            "text": saved["text"],
            "image_urls": saved["image_urls"],
            "permalink": saved["permalink"],
        }
        with self._lock:
            with self.jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def _row_to_post(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "post_id": row["post_id"],
        "page_url": row["page_url"],
        "author": row["author"],
        "created_at": row["created_at"],
        "time_label": row["time_label"],
        "like_count": row["like_count"],
        "comment_count": row["comment_count"],
        "text": row["text"],
        "image_urls": json.loads(row["image_urls_json"]),
        "content_hash": row["content_hash"],
        "permalink": row["permalink"],
        "id_unstable": bool(row["id_unstable"]),
        "collection_id": int(row["collection_id"]),
        "comments_status": row["comments_status"],
        "comments_saved": int(row["comments_saved"]),
        "comments_done_at": row["comments_done_at"],
        "comments_error": row["comments_error"],
        "first_seen_at": row["first_seen_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_comment(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "comment_id": row["comment_id"],
        "post_id": row["post_id"],
        "parent_id": row["parent_id"],
        "depth": int(row["depth"]),
        "author": row["author"],
        "text": row["text"],
        "created_at": row["created_at"],
        "like_count": int(row["like_count"]),
        "saved_at": row["saved_at"],
    }
