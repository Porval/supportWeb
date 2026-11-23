"""JT022 additive state-v2 schema; explicit offline migration, never handler DDL."""
import contextlib
import hashlib
import json
import os
import sqlite3
from account_primitives import fail,iso
from b_state import connect,canon,new_id

VERSION=1
TABLES={'jt022_boards','jt022_requirements','jt022_comments','jt022_images','jt022_commands','jt022_events','jt022_preferences'}
DDL='''
CREATE TABLE jt022_boards(id TEXT PRIMARY KEY CHECK(id='shared'),schema_version INTEGER NOT NULL,generation INTEGER NOT NULL,revision INTEGER NOT NULL,order_revisions TEXT NOT NULL,contains_examples INTEGER NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE jt022_requirements(id TEXT PRIMARY KEY,board_id TEXT NOT NULL REFERENCES jt022_boards(id),revision INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN ('review','doing','done')),position INTEGER NOT NULL,body TEXT NOT NULL,UNIQUE(board_id,status,position));
CREATE TABLE jt022_comments(id TEXT PRIMARY KEY,board_id TEXT NOT NULL REFERENCES jt022_boards(id),requirement_id TEXT NOT NULL REFERENCES jt022_requirements(id) ON DELETE CASCADE,actor_id TEXT NOT NULL REFERENCES account(id),author TEXT NOT NULL,text TEXT NOT NULL,time TEXT NOT NULL);
CREATE TABLE jt022_images(id TEXT PRIMARY KEY,board_id TEXT NOT NULL REFERENCES jt022_boards(id),comment_id TEXT NOT NULL REFERENCES jt022_comments(id) ON DELETE CASCADE,ordinal INTEGER NOT NULL,media_type TEXT NOT NULL,sha256 TEXT NOT NULL,byte_size INTEGER NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,content BLOB NOT NULL,UNIQUE(comment_id,ordinal));
CREATE TABLE jt022_commands(id TEXT PRIMARY KEY,actor_id TEXT NOT NULL REFERENCES account(id),action TEXT NOT NULL,idem_key TEXT NOT NULL,digest TEXT NOT NULL,generation INTEGER NOT NULL,status INTEGER NOT NULL,response TEXT NOT NULL,recorded_at TEXT NOT NULL,UNIQUE(actor_id,action,idem_key));
CREATE TABLE jt022_events(id TEXT PRIMARY KEY,generation INTEGER NOT NULL,board_revision INTEGER NOT NULL,actor_id TEXT NOT NULL REFERENCES account(id),action TEXT NOT NULL,target_id TEXT,request_id TEXT NOT NULL,at TEXT NOT NULL,summary TEXT NOT NULL);
CREATE TABLE jt022_preferences(user_id TEXT PRIMARY KEY REFERENCES account(id),comment_author TEXT NOT NULL);
CREATE INDEX jt022_comments_requirement ON jt022_comments(requirement_id,time,id);
'''
COLUMNS={
'jt022_boards':{'id','schema_version','generation','revision','order_revisions','contains_examples','created_at'},
'jt022_requirements':{'id','board_id','revision','status','position','body'},
'jt022_comments':{'id','board_id','requirement_id','actor_id','author','text','time'},
'jt022_images':{'id','board_id','comment_id','ordinal','media_type','sha256','byte_size','width','height','content'},
'jt022_commands':{'id','actor_id','action','idem_key','digest','generation','status','response','recorded_at'},
'jt022_events':{'id','generation','board_revision','actor_id','action','target_id','request_id','at','summary'},
'jt022_preferences':{'user_id','comment_author'}}

def validate(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'jt022_*'")}
    if tables!=TABLES:fail(503,'SCHEDULE_NOT_READY','排期模块未迁移或版本不一致')
    rows=db.execute('SELECT * FROM jt022_boards').fetchall()
    if len(rows)!=1 or rows[0]['id']!='shared' or rows[0]['schema_version'] not in [1,2]:fail(503,'SCHEDULE_NOT_READY','排期模块版本不一致')
    version=rows[0]['schema_version']
    for name in TABLES:
        expected=COLUMNS[name]|({'author_identity_json'} if name=='jt022_comments' and version==2 else set())
        if {r[1] for r in db.execute('PRAGMA table_info('+name+')')}!=expected:fail(503,'SCHEDULE_NOT_READY','排期表结构与版本不一致')
    return board(db)

def board(db):
    r=db.execute("SELECT * FROM jt022_boards WHERE id='shared'").fetchone()
    if not r:fail(503,'SCHEDULE_NOT_READY','共享排期板不可用')
    try:orders=json.loads(r['order_revisions'])
    except (ValueError,TypeError):fail(503,'SCHEDULE_NOT_READY','排期版本不可用')
    if set(orders)!={'review','doing','done'} or any(type(v) is not int or v<0 for v in orders.values()) or r['generation']<1 or r['revision']<0:fail(503,'SCHEDULE_NOT_READY','排期版本不可用')
    return {'boardId':'shared','generation':r['generation'],'boardRevision':r['revision'],'orderRevisions':orders,'containsExamples':bool(r['contains_examples'])}

def migrate(paths,clock=__import__('time').time):
    os.umask(0o077)
    with paths.exclusive():
        # Existing validator checks authority, credentials/key permissions and source DB.
        from b_auth import BAccounts
        BAccounts(paths,clock)
        with contextlib.closing(connect(paths)) as db:
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'jt022_*'")}
            if names:return {'schemaVersion':schema_version(db),'duplicate':True,**validate(db)}
            backup=paths.directory/('jt022-backup-'+new_id('MIG')+'.sqlite3')
            fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
            with contextlib.closing(sqlite3.connect(backup)) as dest:
                db.backup(dest)
                if dest.execute('PRAGMA quick_check').fetchone()[0]!='ok':fail(503,'PERSISTENCE_UNAVAILABLE','迁移备份校验失败')
            with backup.open('rb') as f:os.fsync(f.fileno())
            digest=hashlib.sha256(backup.read_bytes()).hexdigest()
            try:
                db.execute('BEGIN IMMEDIATE')
                for statement in DDL.split(';'):
                    if statement.strip():db.execute(statement)
                db.execute('INSERT INTO jt022_boards VALUES(?, ?,1,0,?,0,?)',('shared',VERSION,canon({'review':0,'doing':0,'done':0}),iso(clock())))
                result=validate(db)
                if db.execute('PRAGMA foreign_key_check').fetchone():fail(503,'PERSISTENCE_UNAVAILABLE','迁移外键校验失败')
                db.commit()
            except BaseException:db.rollback();raise
            directory_fd=os.open(paths.directory,os.O_RDONLY)
            try:os.fsync(directory_fd)
            finally:os.close(directory_fd)
            return {'schemaVersion':VERSION,'duplicate':False,'backupPath':str(backup),'backupSha256':digest,**result}

def migrate_comment_identity(paths,clock=__import__('time').time):
    os.umask(0o077)
    with paths.exclusive():
        from b_auth import BAccounts
        BAccounts(paths,clock)
        with contextlib.closing(connect(paths)) as db:
            validate(db)
            if schema_version(db)==2:return {'schemaVersion':2,'duplicate':True,**board(db)}
            backup=paths.directory/('jt022-identity-backup-'+new_id('MIG')+'.sqlite3')
            fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
            with contextlib.closing(sqlite3.connect(backup)) as dest:
                db.backup(dest)
                if dest.execute('PRAGMA quick_check').fetchone()[0]!='ok':fail(503,'PERSISTENCE_UNAVAILABLE','身份迁移备份校验失败')
            with backup.open('rb') as f:os.fsync(f.fileno())
            digest=hashlib.sha256(backup.read_bytes()).hexdigest()
            try:
                db.execute('BEGIN IMMEDIATE')
                db.execute('ALTER TABLE jt022_comments ADD COLUMN author_identity_json TEXT NULL')
                db.execute("UPDATE jt022_boards SET schema_version=2 WHERE id='shared'")
                validate(db)
                if db.execute('PRAGMA foreign_key_check').fetchone():fail(503,'PERSISTENCE_UNAVAILABLE','身份迁移外键校验失败')
                db.commit()
            except BaseException:db.rollback();raise
            directory_fd=os.open(paths.directory,os.O_RDONLY)
            try:os.fsync(directory_fd)
            finally:os.close(directory_fd)
            return {'schemaVersion':2,'duplicate':False,'backupPath':str(backup),'backupSha256':digest,**board(db)}

def schema_version(db):return db.execute("SELECT schema_version FROM jt022_boards WHERE id='shared'").fetchone()[0]
