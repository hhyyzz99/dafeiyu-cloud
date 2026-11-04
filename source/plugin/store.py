"""Scoped manual and tool-written memory; additive migration of the v1 database."""
import ast
import hashlib
import math
import operator
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
BEIJING = timezone(timedelta(hours=8))

AUTO_LIMIT = 100
PROFILE_NAMES = {'称呼', '回复偏好', '语言偏好', '职业', '学习方向'}
SENSITIVE = re.compile(
    r'密码|密钥|验证码|身份证|银行卡|私钥|病历|诊断|性生活|性取向|宗教|政治立场|'
    r'password|api[ _-]?key|access[ _-]?token|secret|bearer|sk-[a-z0-9]|'
    r'(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[a-z]{2,}', re.I)


def search_terms(text):
    text = text.lower()
    terms = set(re.findall(r'[a-z0-9_]{2,}', text))
    for run in re.findall(r'[\u4e00-\u9fff]+', text):
        terms.update(run[i:i+2] for i in range(len(run)-1))
    return terms - {'什么', '怎么', '记得', '记住', '之前', '我的', '我是', '一下'}


def scope_key(platform: str, group: str, sender: str, session: str = '') -> str:
    import json
    if not sender:
        raise ValueError('缺少发言者身份，不能读写记忆。')
    room = ['group', group] if group else ['private']
    return hashlib.sha256(json.dumps([platform, room, sender], ensure_ascii=False).encode()).hexdigest()


class FishStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS memories (
                  scope TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
                  updated_at TEXT NOT NULL, PRIMARY KEY(scope, name));
                CREATE TABLE IF NOT EXISTS members (
                  scope TEXT PRIMARY KEY, turns INTEGER NOT NULL DEFAULT 0,
                  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS auto_memories (
                  scope TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
                  evidence TEXT NOT NULL, updated_at TEXT NOT NULL,
                  PRIMARY KEY(scope, name));
                CREATE TABLE IF NOT EXISTS memory_settings (
                  scope TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
                  revision INTEGER NOT NULL DEFAULT 0);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def remember(self, scope, name, value):
        name, value = name.strip(), value.strip()
        if not name or len(name) > 24 or not value or len(value) > 180:
            raise ValueError('记忆名称限 1—24 字，内容限 1—180 字。')
        now = datetime.now(BEIJING).isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exists = db.execute('SELECT 1 FROM memories WHERE scope=? AND name=?', (scope, name)).fetchone()
            count = db.execute('SELECT COUNT(*) FROM memories WHERE scope=?', (scope,)).fetchone()[0]
            if not exists and count >= 12:
                raise ValueError('鱼的小本本最多保存 12 条，请先忘记一条。')
            db.execute('INSERT INTO memories VALUES(?,?,?,?) ON CONFLICT(scope,name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at', (scope, name, value, now))
            db.execute('DELETE FROM auto_memories WHERE scope=? AND name=?', (scope, name))
            self._invalidate(db, scope)

    def memories(self, scope):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT name,value,updated_at FROM memories WHERE scope=? ORDER BY name', (scope,))]

    def forget(self, scope, name):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            n = db.execute('DELETE FROM memories WHERE scope=? AND name=?', (scope, name.strip())).rowcount
            n += db.execute('DELETE FROM auto_memories WHERE scope=? AND name=?', (scope, name.strip())).rowcount
            # A reply already in flight must not write forgotten information back.
            self._invalidate(db, scope)
            return n > 0

    @staticmethod
    def _invalidate(db, scope):
        db.execute('INSERT INTO memory_settings(scope,revision) VALUES(?,1) '
                   'ON CONFLICT(scope) DO UPDATE SET revision=revision+1', (scope,))

    def memory_settings(self, scope):
        with self.connect() as db:
            row = db.execute('SELECT enabled,revision FROM memory_settings WHERE scope=?', (scope,)).fetchone()
            return dict(row) if row else {'enabled': 0, 'revision': 0}

    def set_auto_memory(self, scope, enabled):
        with self.connect() as db:
            db.execute('INSERT INTO memory_settings VALUES(?,?,1) '
                       'ON CONFLICT(scope) DO UPDATE SET enabled=excluded.enabled,revision=revision+1',
                       (scope, int(enabled)))

    def auto_remember(self, scope, name, value, evidence, user_text, revision):
        if not all(isinstance(x, str) for x in (name, value, evidence, user_text)):
            raise ValueError('记忆内容格式不正确。')
        name, value, evidence = name.strip(), value.strip(), evidence.strip()
        if not (1 <= len(name) <= 24 and 1 <= len(value) <= 180 and 2 <= len(evidence) <= 400):
            raise ValueError('自动记忆名称限 24 字、内容限 180 字，需提供当前消息中的原话。')
        if evidence not in user_text:
            raise ValueError('未找到当前用户的原话依据，本条没有保存。')
        if SENSITIVE.search(name + '\n' + value + '\n' + evidence):
            raise ValueError('这类敏感内容不进入自动记忆。')
        if re.search(r'别记|不要记|不用记|不许记|不保存|不要保存|别保存|别存|不要存', user_text):
            raise ValueError('用户要求不要保存，本条没有保存。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = db.execute('SELECT enabled,revision FROM memory_settings WHERE scope=?', (scope,)).fetchone()
            if not state or not state['enabled']:
                raise ValueError('自动记忆尚未开启。用户可发送 /鱼自动记忆 开启。')
            if state['revision'] != revision:
                raise ValueError('记忆设置或内容已改变，本次旧请求没有保存，请重新确认。')
            if db.execute('SELECT 1 FROM memories WHERE scope=? AND name=?', (scope, name)).fetchone():
                raise ValueError('同名内容由用户手动固定；请用 /鱼记住 更新，不自动覆盖。')
            db.execute('INSERT INTO auto_memories VALUES(?,?,?,?,?) ON CONFLICT(scope,name) DO UPDATE SET '
                       'value=excluded.value,evidence=excluded.evidence,updated_at=excluded.updated_at',
                       (scope, name, value, evidence, datetime.now(BEIJING).isoformat()))
            db.execute('DELETE FROM auto_memories WHERE scope=? AND name NOT IN '
                       '(SELECT name FROM auto_memories WHERE scope=? ORDER BY updated_at DESC,name LIMIT ?)',
                       (scope, scope, AUTO_LIMIT))

    def all_memories(self, scope):
        with self.connect() as db:
            manual = [dict(r, source='手动固定', evidence='') for r in db.execute(
                'SELECT name,value,updated_at FROM memories WHERE scope=? ORDER BY name', (scope,))]
            automatic = [dict(r, source='自动整理') for r in db.execute(
                'SELECT name,value,evidence,updated_at FROM auto_memories WHERE scope=? ORDER BY updated_at DESC,name', (scope,))]
            return manual + automatic

    def recall(self, scope, query, limit=6, budget=900):
        """Local lexical retrieval, not embeddings. No unrelated recent-fact fallback."""
        terms = search_terms(query)
        ranked = []
        for r in self.all_memories(scope):
            matched = len(terms & search_terms(r['name'] + ' ' + r['value']))
            profile = r['name'] in PROFILE_NAMES
            if matched or profile:
                ranked.append((matched * 5 + int(profile) * 2, r))
        ranked.sort(key=lambda x: (x[0], x[1]['updated_at']), reverse=True)
        found, size = [], 0
        for _, record in ranked:
            cost = len(record['name']) + len(record['value'])
            if size + cost <= budget:
                found.append(record)
                size += cost
            if len(found) >= limit:
                break
        return found

    def clear_memory(self, scope):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            n = db.execute('DELETE FROM memories WHERE scope=?', (scope,)).rowcount
            n += db.execute('DELETE FROM auto_memories WHERE scope=?', (scope,)).rowcount
            self._invalidate(db, scope)
            return n

    def touch(self, scope):
        now = datetime.now(BEIJING).isoformat()
        with self.connect() as db:
            db.execute('INSERT INTO members VALUES(?,1,?,?) ON CONFLICT(scope) DO UPDATE SET turns=members.turns+1,last_seen=excluded.last_seen', (scope, now, now))

    def status(self, scope):
        with self.connect() as db:
            row = db.execute('SELECT turns,first_seen,last_seen FROM members WHERE scope=?', (scope,)).fetchone()
            return dict(row) if row else {'turns': 0, 'first_seen': None, 'last_seen': None}


def calculate(expression: str) -> str:
    if len(expression) > 100:
        raise ValueError('算式太长，限 100 个字符。')
    operators = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                 ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}

    def walk(node, depth=0):
        if depth > 12:
            raise ValueError('算式层数过多。')
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = node.value
        elif isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
            result = walk(node.operand, depth+1) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in operators:
            left, right = walk(node.left, depth+1), walk(node.right, depth+1)
            if isinstance(node.op, ast.Pow) and (abs(right) > 12 or abs(left) > 1e9):
                raise ValueError('幂运算范围过大。')
            result = operators[type(node.op)](left, right)
        else:
            raise ValueError('仅支持数字、括号和 + - * / % **。')
        if type(result) not in (int, float) or not math.isfinite(result) or abs(result) > 1e15:
            raise ValueError('计算结果超出支持范围。')
        return result

    try:
        return str(walk(ast.parse(expression, mode='eval').body))
    except (SyntaxError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('算式无效或运算超出范围。') from exc
