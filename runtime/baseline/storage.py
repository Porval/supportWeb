"""SQLite snapshots: demo connections are read-only; normal operations start empty."""
import contextlib
import json
import os
from pathlib import Path
import sqlite3
from account_primitives import fail

ENTITIES=('brands','products','activities','activity_versions','materials','submissions','review_nodes','decisions','history','external_facts','metrics')

def validate_dir(directory):
    raw=Path(directory)
    if not raw.is_absolute():raise ValueError('data-dir must be absolute')
    for p in [raw,*raw.parents]:
        if p.is_symlink():raise ValueError('data directory must not contain symlinks')
    root=Path(__file__).resolve().parents[2]
    dest=raw.resolve()
    for forbidden in [root/'backend/account-system/var',root/'tools/coordination/var']:
        if dest==forbidden or forbidden in dest.parents:raise ValueError('old private data must not be opened')
    return dest

def prepare_db(directory,data,*,mode):
    directory=validate_dir(directory);os.umask(0o077)
    directory.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(directory,0o700)
    path=directory/'operations.sqlite3'
    if path.exists():raise ValueError('database already exists; never overwrite')
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        for key,value in data['meta'].items():db.execute('INSERT INTO metadata VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
        db.execute('INSERT INTO metadata VALUES(?,?)',('mode',json.dumps(mode)))
        for entity in ENTITIES:
            db.execute('CREATE TABLE '+entity+'(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
            for row in data.get(entity,[]):db.execute('INSERT INTO '+entity+' VALUES(?,?)',(row['id'],json.dumps(row,ensure_ascii=False)))
        db.execute('CREATE TABLE activity_brands(activity_id TEXT NOT NULL,brand_id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(activity_id,brand_id))')
        db.execute('CREATE TABLE activity_products(activity_id TEXT NOT NULL,product_id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(activity_id,product_id))')
        for r in data.get('activity_brands',[]):db.execute('INSERT OR IGNORE INTO activity_brands VALUES(?,?,?)',(r['activityId'],r['brandId'],json.dumps(r,ensure_ascii=False)))
        for r in data.get('activity_products',[]):db.execute('INSERT OR IGNORE INTO activity_products VALUES(?,?,?)',(r['activityId'],r['productId'],json.dumps(r,ensure_ascii=False)))
        db.commit()
    os.chmod(path,0o600)
    return path

def initialize_normal(directory):
    directory=validate_dir(directory)
    if not (directory/'operations.sqlite3').exists():
        prepare_db(directory,{'meta':{'schemaVersion':1,'datasetRevision':'normal-empty-v1','dataOrigin':'local_normal','sourceReady':False,'relationsComplete':False,'sourceHashes':[]}},mode='normal')

class Snapshots:
    def __init__(self,directory,mode):
        self.directory=validate_dir(directory);self.path=self.directory/'operations.sqlite3';self.mode=mode
        if not self.path.is_file() or self.path.is_symlink():raise ValueError('offline seed is required')
        with self.read() as db:
            m={r[0]:json.loads(r[1]) for r in db.execute('SELECT key,value FROM metadata')}
            if m.get('mode')!=mode or m.get('schemaVersion')!=1:raise ValueError('dataset mode/schema mismatch')
    @contextlib.contextmanager
    def read(self):
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=3,isolation_level=None)
        db.execute('PRAGMA query_only=ON');db.execute('PRAGMA busy_timeout=3000');db.execute('BEGIN')
        try:yield db
        finally:db.rollback();db.close()
    def load(self,db):
        data={entity:[json.loads(r[0]) for r in db.execute('SELECT body FROM '+entity+' ORDER BY id')] for entity in ENTITIES}
        data['activity_brands']=[json.loads(r[0]) for r in db.execute('SELECT body FROM activity_brands ORDER BY activity_id,brand_id')]
        data['activity_products']=[json.loads(r[0]) for r in db.execute('SELECT body FROM activity_products ORDER BY activity_id,product_id')]
        data['meta']={r[0]:json.loads(r[1]) for r in db.execute('SELECT key,value FROM metadata')}
        return data
