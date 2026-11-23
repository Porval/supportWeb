"""JT021 offline test-v2 migration and explicit synthetic provisioning."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from account_primitives import fail,iso
from test_accounts import TestAccounts,derive,ITERATIONS,TestPaths,inconsistent

STATE='state-v2.sqlite3';MARKER='authority.json'
PROVISION={
'local_admin':(None,['operations.read','operations.download','accounts.manage']),
'local_submitter':('marketing',['operations.read','operations.download','submissions.create','submissions.read_own','submissions.edit_own','activities.propose','materials.read','materials.download']),
'local_manager_initial':('management',['operations.read','operations.download','reviews.read','reviews.decide','materials.read','materials.download']),
'local_pricing':('pricing',['operations.read','operations.download','reviews.read','reviews.decide','materials.read','materials.download']),
'local_manager_final':('management',['operations.read','operations.download','reviews.read','reviews.decide','materials.read','materials.download'])}
DDL='''
CREATE TABLE b_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE system_permissions(user_id TEXT NOT NULL REFERENCES account(id),permission TEXT NOT NULL,PRIMARY KEY(user_id,permission));
CREATE TABLE explicit_grants(user_id TEXT NOT NULL REFERENCES account(id),action TEXT NOT NULL,resource_id TEXT NOT NULL,PRIMARY KEY(user_id,action,resource_id));
CREATE TABLE review_plan(stage TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES account(id));
CREATE TABLE provision_batches(id TEXT PRIMARY KEY,state TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE account_profile_audit(id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,target_id TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,at REAL NOT NULL);
CREATE TABLE material_blobs(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES account(id),filename TEXT NOT NULL,media_type TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,content BLOB NOT NULL,received_at REAL NOT NULL,parse_support TEXT NOT NULL);
CREATE TABLE b_submissions(id TEXT PRIMARY KEY,submitter_id TEXT NOT NULL REFERENCES account(id),revision INTEGER NOT NULL,state TEXT NOT NULL,activity_id TEXT,version_id TEXT,parent_id TEXT,received_at REAL NOT NULL);
CREATE TABLE submission_revisions(submission_id TEXT NOT NULL REFERENCES b_submissions(id),revision INTEGER NOT NULL,input_digest TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(submission_id,revision));
CREATE TABLE analysis_runs(id TEXT PRIMARY KEY,submission_id TEXT NOT NULL REFERENCES b_submissions(id),input_revision INTEGER NOT NULL,input_digest TEXT NOT NULL,catalog_revision TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL,started_at REAL,finished_at REAL);
CREATE UNIQUE INDEX one_active_analysis ON analysis_runs(submission_id,input_revision) WHERE state IN ('queued','running','cancel_requested');
CREATE TABLE parse_cache(sha256 TEXT NOT NULL,parser_version TEXT NOT NULL,limits_version TEXT NOT NULL,worker_hash TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(sha256,parser_version,limits_version,worker_hash));
CREATE TABLE resolutions(submission_id TEXT NOT NULL,input_revision INTEGER NOT NULL,candidate_id TEXT NOT NULL,activity_id TEXT NOT NULL,version_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(submission_id,input_revision,candidate_id));
CREATE TABLE activity_identities(fingerprint TEXT PRIMARY KEY,activity_id TEXT NOT NULL);
CREATE TABLE version_reviews(version_id TEXT PRIMARY KEY,activity_id TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,approval_mode TEXT,submission_id TEXT NOT NULL);
CREATE TABLE review_tasks(id TEXT PRIMARY KEY,version_id TEXT NOT NULL REFERENCES version_reviews(version_id),stage TEXT NOT NULL,assignee_id TEXT REFERENCES account(id),revision INTEGER NOT NULL,state TEXT NOT NULL,UNIQUE(version_id,stage));
CREATE TABLE review_decisions(id TEXT PRIMARY KEY,node_id TEXT UNIQUE NOT NULL REFERENCES review_tasks(id),version_id TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES account(id),payload TEXT NOT NULL);
CREATE TABLE b_history(id TEXT PRIMARY KEY,activity_id TEXT,version_id TEXT,submission_id TEXT,payload TEXT NOT NULL);
CREATE TABLE commands(id TEXT PRIMARY KEY,actor_id TEXT NOT NULL REFERENCES account(id),action TEXT NOT NULL,idem_key TEXT NOT NULL,digest TEXT NOT NULL,resource_id TEXT,status INTEGER NOT NULL,response TEXT NOT NULL,recorded_at REAL NOT NULL,UNIQUE(actor_id,action,idem_key));
CREATE TABLE version_sources(version_id TEXT NOT NULL,submission_id TEXT NOT NULL,input_revision INTEGER NOT NULL,material_ids TEXT NOT NULL,PRIMARY KEY(version_id,submission_id,input_revision));
CREATE TABLE incomplete_relations(activity_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
'''

def canon(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def fingerprint(value):return hashlib.sha256(canon(value).encode()).hexdigest()
def new_id(prefix):return prefix+'-'+str(uuid.uuid4())
def is_v2(paths):return (paths.directory/STATE).exists() or (paths.directory/MARKER).exists()
def private_atomic(path,value):
    temp=path.with_name('.'+path.name+'-'+secrets.token_hex(8)+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False);f.write('\n');f.flush();os.fsync(f.fileno())
    os.chmod(temp,0o600);os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
def credential_path(paths,username):return paths.directory/('credentials.json' if username=='local_reader' else 'credentials-'+username+'.json')
def document(password,username,version,created,expires):return {'mode':'test','url':'http://127.0.0.1:52669/','username':username,'password':password,'credentialVersion':version,'createdAt':iso(created),'expiresAt':iso(expires)}
def connect(paths,readonly=False):
    p=paths.directory/STATE
    if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o777 != 0o600:inconsistent()
    db=sqlite3.connect(p.as_uri()+('?mode=ro' if readonly else '?mode=rw'),uri=True,timeout=3,isolation_level=None);db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=3000')
    if readonly:db.execute('PRAGMA query_only=ON')
    return db

def migrate(paths):
    os.umask(0o077)
    with paths.exclusive():
        target=paths.directory/STATE;marker=paths.directory/MARKER
        if marker.exists():
            if marker.is_symlink() or not target.exists():inconsistent()
            with contextlib.closing(connect(paths,True)) as db:
                meta={r[0]:json.loads(r[1]) for r in db.execute('SELECT * FROM b_meta')}
                if meta.get('schema_version')!=2 or meta.get('authority')!='state-v2' or db.execute('PRAGMA quick_check').fetchone()[0]!='ok':inconsistent()
                marker_value=json.loads(marker.read_text())
                if marker_value!={'schemaVersion':2,'authority':'state-v2','migrationId':meta['migrationId']}:inconsistent()
                return {'schemaVersion':2,'authority':'state-v2','duplicate':True,'migrationId':meta['migrationId']}
        hashes={name:hashlib.sha256((paths.directory/name).read_bytes()).hexdigest() for name in ['accounts.sqlite3','operations.sqlite3','session.key','credentials.json']}
        pending=paths.directory/'.migration-b.json'
        if pending.exists():
            plan=json.loads(pending.read_text())
            if plan['sourceHashes']!=hashes:inconsistent()
        else:
            # Validate sources without changing the original reader or its data.
            TestAccounts(paths)
            plan={'migrationId':new_id('MIG'),'sourceHashes':hashes}
            private_atomic(pending,plan)
        if target.exists():
            with contextlib.closing(connect(paths,True)) as db:
                meta={r[0]:json.loads(r[1]) for r in db.execute('SELECT * FROM b_meta')}
                if meta.get('migrationId')!=plan['migrationId'] or meta.get('sourceHashes')!=hashes:inconsistent()
            private_atomic(marker,{'schemaVersion':2,'authority':'state-v2','migrationId':plan['migrationId']})
            return {'schemaVersion':2,'authority':'state-v2','duplicate':False,'migrationId':plan['migrationId'],'recoveredPublication':True}
        temp=paths.directory/('.state-v2-'+secrets.token_hex(10)+'.sqlite3')
        with contextlib.closing(sqlite3.connect((paths.directory/'accounts.sqlite3').as_uri()+'?mode=ro',uri=True)) as source,contextlib.closing(sqlite3.connect(temp)) as dest:
            source.backup(dest)
            dest.execute('PRAGMA foreign_keys=ON')
            dest.execute("ATTACH DATABASE ? AS old_ops",((paths.directory/'operations.sqlite3').as_uri()+'?mode=ro',))
            for name,sql in dest.execute("SELECT name,sql FROM old_ops.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                dest.execute(sql)
                dest.execute('INSERT INTO "'+name+'" SELECT * FROM old_ops."'+name+'"')
            dest.commit();dest.execute('DETACH DATABASE old_ops')
            dest.execute('BEGIN IMMEDIATE')
            for declaration in ['business_role TEXT','name TEXT','remark TEXT','phone TEXT',"status TEXT NOT NULL DEFAULT 'active'",'profile_version INTEGER NOT NULL DEFAULT 1']:
                dest.execute('ALTER TABLE account ADD COLUMN '+declaration)
            dest.execute("UPDATE account SET name='本地只读体验'")
            for statement in DDL.split(';'):
                if statement.strip():dest.execute(statement)
            reader=dest.execute('SELECT id FROM account').fetchone()[0]
            for action in ['operations.read','operations.download']:dest.execute('INSERT INTO explicit_grants VALUES(?,?,?)',(reader,action,'*'))
            for key,value in {'schema_version':2,'authority':'state-v2','migrationId':plan['migrationId'],'sourceHashes':hashes,'datasetCounter':0,'catalogRevision':'jt020-test-r1'}.items():dest.execute('INSERT INTO b_meta VALUES(?,?)',(key,canon(value)))
            # Existing body/history remain as legacy evidence, never reconstructed decisions.
            for id,body in dest.execute('SELECT id,body FROM history').fetchall():
                value=json.loads(body);value['migrationSource']='legacy';dest.execute('UPDATE history SET body=? WHERE id=?',(canon(value),id))
            if dest.execute('PRAGMA foreign_key_check').fetchone():inconsistent()
            dest.commit()
            if dest.execute('PRAGMA quick_check').fetchone()[0]!='ok':inconsistent()
            source_count=source.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
            if dest.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]!=source_count:inconsistent()
        os.chmod(temp,0o600)
        with temp.open('rb') as f:os.fsync(f.fileno())
        if {n:hashlib.sha256((paths.directory/n).read_bytes()).hexdigest() for n in hashes}!=hashes:inconsistent()
        os.replace(temp,target)
        private_atomic(marker,{'schemaVersion':2,'authority':'state-v2','migrationId':plan['migrationId']})
        return {'schemaVersion':2,'authority':'state-v2','duplicate':False,'migrationId':plan['migrationId'],'readerPreserved':True}

def provision(paths,clock=time.time):
    os.umask(0o077)
    with paths.exclusive():
        if not (paths.directory/MARKER).exists():inconsistent()
        from b_auth import BAccounts
        BAccounts(paths,clock,allow_delivery_inconsistent=True)
        with contextlib.closing(connect(paths)) as db:
            existing=db.execute('SELECT * FROM provision_batches').fetchall()
            if existing:
                if len(existing)!=1 or existing[0]['state'] not in ['pending','ready']:inconsistent()
                batch=dict(existing[0]);payload=json.loads(batch['payload'])
                if batch['state']=='ready':
                    for item in payload['users']:
                        row=db.execute('SELECT * FROM account WHERE username=?',(item['username'],)).fetchone()
                        doc=json.loads(credential_path(paths,item['username']).read_text())
                        if doc['credentialVersion']!=row['credential_version'] or not __import__('hmac').compare_digest(derive(doc['password'],row['salt']),row['verifier']):inconsistent()
                    return {'batchId':batch['id'],'state':'ready','duplicate':True,'users':[{'username':r['username'],'id':r['id'],'credentialsPath':str(credential_path(paths,r['username'])),'credentialVersion':r['credential_version'],'expiresAt':iso(r['expires_at'])} for r in db.execute("SELECT * FROM account WHERE username!='local_reader' ORDER BY username")]}

            else:
                batch={'id':new_id('PROVISION'),'state':'pending'};stage=paths.directory/('.provision-'+batch['id']);stage.mkdir(mode=0o700)
                payload={'stage':stage.name,'users':[]};created=clock()
                db.execute('BEGIN IMMEDIATE')
                try:
                    ids={}
                    for username,(role,actions) in PROVISION.items():
                        if db.execute('SELECT 1 FROM account WHERE username=?',(username,)).fetchone():inconsistent()
                        uid='test-'+str(uuid.uuid4());ids[username]=uid;password=secrets.token_urlsafe(24);salt=secrets.token_bytes(24)
                        db.execute('INSERT INTO account(id,username,salt,verifier,algorithm,iterations,derived_length,credential_version,created_at,expires_at,permissions,scope,business_role,name,remark,phone,status,profile_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(uid,username,salt,derive(password,salt),'pbkdf2_hmac_sha256',ITERATIONS,32,1,created,created+86400,canon(actions),'*',role,username,None,None,'active',1))
                        for action in actions:
                            if action=='accounts.manage':db.execute('INSERT INTO system_permissions VALUES(?,?)',(uid,action))
                            else:db.execute('INSERT INTO explicit_grants VALUES(?,?,?)',(uid,action,'own' if action.endswith('_own') else 'assigned' if action.startswith('reviews.') else '*'))
                        staged=stage/('credentials-'+username+'.json');private_atomic(staged,document(password,username,1,created,created+86400))
                        payload['users'].append({'username':username,'id':uid,'credentialsPath':str(credential_path(paths,username)),'credentialVersion':1,'expiresAt':iso(created+86400),'stage':str(staged.name)})
                    for node,name in [('initial','local_manager_initial'),('pricing','local_pricing'),('final','local_manager_final')]:db.execute('INSERT INTO review_plan VALUES(?,?)',(node,ids[name]))
                    db.execute('INSERT INTO provision_batches VALUES(?,?,?)',(batch['id'],'pending',canon(payload)));db.commit()
                except BaseException:db.rollback();raise
            if payload['stage']!='.provision-'+batch['id']:inconsistent()
            stage=paths.directory/payload['stage']
            if stage.is_symlink() or not stage.is_dir() or stage.stat().st_mode & 0o777 != 0o700:inconsistent()
            for item in payload['users']:
                if item['username'] not in PROVISION or item['stage']!='credentials-'+item['username']+'.json':inconsistent()
                destination=credential_path(paths,item['username']);staged=stage/item['stage']
                if not destination.exists():
                    if not staged.is_file() or staged.is_symlink():inconsistent()
                    os.replace(staged,destination)
                row=db.execute('SELECT * FROM account WHERE id=?',(item['id'],)).fetchone();doc=json.loads(destination.read_text())
                if doc['credentialVersion']!=row['credential_version'] or not __import__('hmac').compare_digest(derive(doc['password'],row['salt']),row['verifier']):inconsistent()
            db.execute("UPDATE provision_batches SET state='ready' WHERE id=?",(batch['id'],))
            return {'batchId':batch['id'],'state':'ready','duplicate':bool(existing),'users':[{k:v for k,v in u.items() if k!='stage'} for u in payload['users']]}
