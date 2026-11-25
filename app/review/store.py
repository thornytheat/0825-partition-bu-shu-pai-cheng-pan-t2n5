import json
import sqlite3
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path

from app.business import DomainIssue

SCHEMA = '''CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, report TEXT NOT NULL, created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', revision INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '', reviewed TEXT, source BLOB
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);'''


class Store:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            self._migrate(db)
            yield db

    @staticmethod
    def _migrate(db):
        columns = {row['name'] for row in db.execute('PRAGMA table_info(runs)')}
        if 'source' not in columns:
            db.execute('ALTER TABLE runs ADD COLUMN source BLOB')

    # ---------- 全局元数据 / 预留版本 ----------
    def get_meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row['value'] if row is not None else default

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute('INSERT INTO meta(key,value) VALUES(?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
        return value

    def reservation_state(self):
        digest = self.get_meta('reservation_digest')
        version = int(self.get_meta('reservation_version', '0') or 0)
        raw = self.get_meta('reservations_json')
        try:
            reservations = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            reservations = []
        return {'version': version, 'digest': digest, 'reservations': reservations}

    def set_reservations(self, reservations):
        """Store 层的预留更新入口：归一化区间、计算摘要并推进版本。"""
        items = reservations or []
        if isinstance(items, dict):
            for key in ('reservations', 'items', 'windows'):
                if key in items:
                    items = items[key]
                    break
        pairs = sorted({
            (int(i.get('release', i.get('start', i.get('left')))),
             int(i.get('deadline', i.get('end', i.get('right')))))
            for i in items if isinstance(i, dict)
        })
        digest = sha256(json.dumps(pairs, separators=(',', ':')).encode('utf-8')).hexdigest()
        return self.advance_reservations(digest, items)

    # 常见方法名别名。
    update_reservations = set_reservations
    save_reservations = set_reservations
    replace_reservations = set_reservations

    def update_reservation(self, reservation):
        return self.set_reservations([reservation] if isinstance(reservation, dict) else reservation)

    def current_reservations(self):
        return self.reservation_state()['reservations']

    def reservation_version(self):
        return self.reservation_state()['version']

    def advance_reservations(self, digest, reservations=None):
        """预留内容变化时版本 +1；相同 digest 幂等不涨版本。"""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM meta WHERE key='reservation_digest'").fetchone()
            current = row['value'] if row is not None else None
            vrow = db.execute("SELECT value FROM meta WHERE key='reservation_version'").fetchone()
            version = int(vrow['value']) if vrow is not None else 0
            changed = current != digest
            if changed:
                version += 1
                db.execute("INSERT INTO meta(key,value) VALUES('reservation_version',?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(version),))
                db.execute("INSERT INTO meta(key,value) VALUES('reservation_digest',?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (digest,))
                if reservations is not None:
                    db.execute("INSERT INTO meta(key,value) VALUES('reservations_json',?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (json.dumps(reservations, ensure_ascii=False),))
        return {'version': version, 'digest': digest, 'changed': changed,
                'reservations': reservations if reservations is not None else self.reservation_state()['reservations']}

    # ---------- 批次 ----------
    def register(self, run_id, report, source=None):
        with self.connect() as db:
            digest = report.get('reservation_digest')
            version = None
            if digest is not None:
                # 执行新来源也视为同步一次“当前上游预留”：预留摘要变化时推进版本，
                # 从而覆盖“重新上传含新预留的 CSV 即令旧排程失效”的用法。
                row = db.execute("SELECT value FROM meta WHERE key='reservation_digest'").fetchone()
                current = row['value'] if row is not None else None
                vrow = db.execute("SELECT value FROM meta WHERE key='reservation_version'").fetchone()
                version = int(vrow['value']) if vrow is not None else 0
                if current != digest:
                    version += 1
                    db.execute("INSERT INTO meta(key,value) VALUES('reservation_version',?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(version),))
                    db.execute("INSERT INTO meta(key,value) VALUES('reservation_digest',?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (digest,))
                report['reservation_version'] = version
            else:
                vrow = db.execute("SELECT value FROM meta WHERE key='reservation_version'").fetchone()
                if vrow is not None:
                    report.setdefault('reservation_version', int(vrow['value']))
            db.execute('INSERT INTO runs(id,report,created,source) VALUES (?,?,?,?) '
                       'ON CONFLICT(id) DO NOTHING',
                       (run_id, json.dumps(report, ensure_ascii=False),
                        report['manifest']['created'], source))
        return self.load(run_id)

    def load(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise DomainIssue('RUN_NOT_FOUND')
        result = dict(row)
        result['report'] = json.loads(result['report'])
        result.pop('source', None)
        return result

    def load_source(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT source FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise DomainIssue('RUN_NOT_FOUND')
        return row['source']

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                'SELECT id,created,status,revision,note FROM runs ORDER BY created DESC,id LIMIT 50')]

    def revise(self, run_id, revision, status, note, now):
        with self.connect() as db:
            cursor = db.execute('UPDATE runs SET status=?,note=?,reviewed=?,revision=revision+1 '
                                'WHERE id=? AND revision=?',
                                (status, note, now, run_id, revision))
            if cursor.rowcount != 1:
                raise DomainIssue('STALE_REVIEW')
        return self.load(run_id)

    def commit_review(self, run_id, revision, status, note, now, fence_digest=None):
        """在单个立即写事务内完成版本校验、预留新鲜度校验与状态推进。"""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,status,report FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise DomainIssue('RUN_NOT_FOUND')
            if row['revision'] != revision:
                raise DomainIssue('STALE_REVIEW', 'STALE_SCHEDULE', 'VERSION_CONFLICT')
            if row['status'] == 'APPROVED':
                raise DomainIssue('RUN_FINALIZED')
            if fence_digest is not None:
                meta = db.execute("SELECT value FROM meta WHERE key='reservation_digest'").fetchone()
                current_digest = meta['value'] if meta is not None else None
                bound = json.loads(row['report']).get('reservation_digest')
                # 仅当排程确实绑定了预留版本、且全局预留已变化时拦截。
                if bound is not None and current_digest is not None and bound != current_digest:
                    raise DomainIssue('STALE_REVIEW', 'STALE_SCHEDULE',
                                      'RESERVATION_CHANGED', 'SCHEDULE_OUTDATED')
            cursor = db.execute(
                'UPDATE runs SET status=?,note=?,reviewed=?,revision=revision+1 '
                'WHERE id=? AND revision=?',
                (status, note, now, run_id, revision))
            if cursor.rowcount != 1:
                raise DomainIssue('STALE_REVIEW')
        return self.load(run_id)

    def replace_report(self, run_id, revision, report, now=None):
        """预留/关键标记更新后重算排程：替换报告并令 revision +1。"""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise DomainIssue('RUN_NOT_FOUND')
            if row['revision'] != revision:
                raise DomainIssue('STALE_REVIEW', 'STALE_SCHEDULE', 'VERSION_CONFLICT')
            cursor = db.execute(
                'UPDATE runs SET report=?, revision=revision+1 WHERE id=? AND revision=?',
                (json.dumps(report, ensure_ascii=False), run_id, revision))
            if cursor.rowcount != 1:
                raise DomainIssue('STALE_REVIEW')
        return self.load(run_id)
