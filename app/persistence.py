"""SQLite persistence shared by HTTP requests and background workers."""
from collections.abc import MutableMapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path=None):
        self.path = str(path or os.getenv('RESUME_DB_PATH', './data/workbench.sqlite3'))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS objects(kind TEXT,key TEXT,payload TEXT,PRIMARY KEY(kind,key));
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,cache_key TEXT,status TEXT,payload TEXT,created_at TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS reusable_job ON jobs(cache_key)
                  WHERE status IN ('queued','running','completed');
                CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY,task_id TEXT,model TEXT,kind TEXT,
                  input_tokens INTEGER,output_tokens INTEGER,reported INTEGER,cost REAL,created_at TEXT);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=20)
        db.row_factory=sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def archive_resume(self, resume_id, vector_snapshot):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT payload FROM objects WHERE kind='resumes' AND key=?",(resume_id,)).fetchone()
            if row is None: raise KeyError(resume_id)
            record=json.loads(row[0])
            archive={'record':record,'vector':vector_snapshot,'deleted_at':now(),'cleanup_pending':True}
            db.execute("INSERT INTO objects VALUES('trash',?,?) ON CONFLICT(kind,key) DO UPDATE SET payload=excluded.payload",(resume_id,json.dumps(archive,ensure_ascii=False,default=str)))
            db.execute("DELETE FROM objects WHERE kind='resumes' AND key=?",(resume_id,))
        return archive

    def restore_resume(self, resume_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT payload FROM objects WHERE kind='trash' AND key=?",(resume_id,)).fetchone()
            if row is None: raise KeyError(resume_id)
            archive=json.loads(row[0])
            db.execute("INSERT INTO objects VALUES('resumes',?,?) ON CONFLICT(kind,key) DO UPDATE SET payload=excluded.payload",(resume_id,json.dumps(archive['record'],ensure_ascii=False,default=str)))
            db.execute("DELETE FROM objects WHERE kind='trash' AND key=?",(resume_id,))
        return archive['record']

    def get_job(self, job_id):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def create_job(self, text, cache_key):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT payload FROM jobs WHERE cache_key=? AND status IN ('queued','running','completed')",(cache_key,)).fetchone()
            if row:
                return json.loads(row[0]),False
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=6:
                raise ValueError('等待中的任务较多，请稍后提交。')
            job={'id':uuid.uuid4().hex,'dataset_key':cache_key,'text':text,'status':'queued','stage':'等待处理','progress':0,
                 'created_at':now(),'updated_at':now(),'result':None,'error':None,'elapsed_seconds':0}
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',(job['id'],cache_key,'queued',json.dumps(job,ensure_ascii=False),job['created_at']))
        return job,True

    def update_job(self, job_id, **updates):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            job=json.loads(row[0]); job.update(updates,updated_at=now())
            db.execute('UPDATE jobs SET status=?,payload=? WHERE id=?',(job['status'],json.dumps(job,ensure_ascii=False,default=str),job_id))
        return job

    def history(self, limit=50):
        with self.connect() as db:
            rows=db.execute('SELECT payload FROM jobs ORDER BY created_at DESC LIMIT ?',(limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def interrupt_unfinished(self):
        for job in self.history(10000):
            if job['status'] in ('queued','running'):
                self.update_job(job['id'],status='interrupted',stage='服务重启，任务已中断',error='服务在处理期间重启。请点击重试；不会自动产生新的模型调用。')


class PersistentMap(MutableMapping):
    def __init__(self, database, kind): self.database,self.kind=database,kind
    def __getitem__(self,key):
        with self.database.connect() as db:
            row=db.execute('SELECT payload FROM objects WHERE kind=? AND key=?',(self.kind,key)).fetchone()
        if row is None: raise KeyError(key)
        return json.loads(row[0])
    def __setitem__(self,key,value):
        with self.database.connect() as db:
            db.execute('INSERT INTO objects VALUES(?,?,?) ON CONFLICT(kind,key) DO UPDATE SET payload=excluded.payload',
                       (self.kind,key,json.dumps(value,ensure_ascii=False,default=str)))
    def __delitem__(self,key):
        with self.database.connect() as db:
            cursor=db.execute('DELETE FROM objects WHERE kind=? AND key=?',(self.kind,key))
            if not cursor.rowcount: raise KeyError(key)
    def __iter__(self):
        with self.database.connect() as db:
            keys=[r[0] for r in db.execute('SELECT key FROM objects WHERE kind=? ORDER BY rowid',(self.kind,))]
        return iter(keys)
    def __len__(self):
        with self.database.connect() as db:
            return db.execute('SELECT count(*) FROM objects WHERE kind=?',(self.kind,)).fetchone()[0]


database=Database()
