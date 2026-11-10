import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException, Request

HASHER = PasswordHasher()


class Auth:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()
        self.dummy = HASHER.hash(secrets.token_urlsafe(24))

    def initialize(self, username, password):
        if not username.strip() or len(password) < 12:
            raise ValueError('用户名不能为空，密码至少 12 个字符')
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM admins').fetchone():
                raise ValueError('管理员已初始化')
            db.execute('INSERT INTO admins(username,password) VALUES (?,?)', (username.strip(), HASHER.hash(password)))

    def login(self, username, password, ip):
        now = time.time()
        with self.lock:
            # Bound memory and throttle globally as well as per client; ignore proxy headers.
            for key in list(self.attempts):
                while self.attempts[key] and self.attempts[key][0] < now - 300:
                    self.attempts[key].popleft()
                if not self.attempts[key]:
                    del self.attempts[key]
            if len(self.attempts[ip]) >= 5 or len(self.attempts['*']) >= 40:
                raise HTTPException(429, '登录尝试过多，请 5 分钟后重试')
            self.attempts[ip].append(now)
            self.attempts['*'].append(now)
        row = self.db.one('SELECT * FROM admins WHERE username=?', (username,))
        try:
            HASHER.verify(row['password'] if row else self.dummy, password)
            valid = row is not None
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid:
            raise HTTPException(401, '用户名或密码错误')
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (self.digest(token), csrf, now + self.config.session_hours * 3600))
        return token, csrf

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def require(self, request: Request):
        token = request.cookies.get('httpd_session', '')
        row = self.db.one('SELECT * FROM sessions WHERE hash=? AND expires>?', (self.digest(token), time.time()))
        if not row:
            raise HTTPException(401, '请先登录')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if not secrets.compare_digest(request.headers.get('x-csrf-token', ''), row['csrf']):
                raise HTTPException(403, 'CSRF 校验失败')
        return row
