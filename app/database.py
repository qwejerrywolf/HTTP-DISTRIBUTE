import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'files.db'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 3:
                raise RuntimeError('数据库版本高于当前程序，拒绝降级')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, csrf TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS objects(id INTEGER PRIMARY KEY, path TEXT UNIQUE, kind TEXT, identity TEXT);
            CREATE TABLE IF NOT EXISTS links(id INTEGER PRIMARY KEY, token TEXT UNIQUE, object_id INTEGER REFERENCES objects(id),
              created REAL, expires REAL, enabled INTEGER DEFAULT 1, note TEXT DEFAULT '', display TEXT DEFAULT '',
              bytes INTEGER DEFAULT 0, last_access REAL);
            CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, link_id INTEGER, started REAL, ended REAL,
              ip TEXT, filename TEXT, method TEXT, status INTEGER, bytes INTEGER, outcome TEXT);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, created REAL, kind TEXT, source TEXT,
              destination TEXT, status TEXT, done INTEGER DEFAULT 0, total INTEGER DEFAULT 0, error TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE INDEX IF NOT EXISTS request_started ON requests(started);
            CREATE INDEX IF NOT EXISTS request_link ON requests(link_id);
            ''')
            # Atomic, additive v1 -> v2 migration; preserve existing history and authorizations.
            db.execute('BEGIN IMMEDIATE')
            columns = {r[1] for r in db.execute('PRAGMA table_info(requests)')}
            if 'duration' not in columns:
                db.execute('ALTER TABLE requests ADD COLUMN duration REAL')
            db.execute('CREATE TABLE IF NOT EXISTS ip_locations(ip TEXT PRIMARY KEY, location TEXT NOT NULL, expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS blocked_ips(ip TEXT PRIMARY KEY, created REAL NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS request_ip ON requests(ip)')
            db.execute('PRAGMA user_version=3')
            db.execute("UPDATE operations SET status='interrupted',error='服务重启；源文件保留，请检查 .httpd-* 暂存目录' WHERE status IN ('queued','running')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=15000')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def all(self, sql, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).lastrowid

    def setting(self, key, default=None):
        row = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(row['value']) if row else default

    def set_setting(self, key, value):
        self.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def backup(self):
        from datetime import datetime, timezone
        target = self.directory / ('backup-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f') + '.db')
        with self.connect() as source, sqlite3.connect(target) as dest:
            source.backup(dest)
        return target.name
