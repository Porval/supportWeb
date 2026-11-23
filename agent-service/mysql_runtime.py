"""Start the account-local MySQL over a private socket; provision only on explicit bootstrap."""
from pathlib import Path
import fcntl
import json
import os
import secrets
import subprocess
import sys
import time

home = Path.home()
data = Path(os.environ.get('ACTIVITY_DATA_DIR', home/'data'))
install = data/'mysql-dist/mysql-8.4.13-linux-glibc2.28-x86_64-minimal'
private = home/'.activityagent'
root = data/'agent-mysql'
sock = root/'db.sock'
env = dict(os.environ, LD_LIBRARY_PATH=f'{install}/lib/private:{install}/lib')
log = root/'mysql.log'
db = root/'store'
bootstrap = sys.argv[1:] == ['bootstrap']
def run_mysql(config, sql):
    return subprocess.run([str(install/'bin/mysql'), *config, '--default-character-set=utf8mb4', '--batch',
                           '--skip-column-names'], input=sql.encode(), stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          env=env,timeout=20)
def profile(name,user):
    path = private/name
    if not path.exists():
        if not bootstrap: raise RuntimeError('Database not provisioned')
        with path.open('x') as stream:
            stream.write(f'[client]\nuser={user}\npassword={secrets.token_hex(32)}\nprotocol=SOCKET\nsocket={sock}\n')
    key=next(s.split('=',1)[1] for s in path.read_text().splitlines() if s.startswith('password='))
    assert len(key)==64 and all(c in '0123456789abcdef' for c in key)
    return [f'--defaults-file={path}'],key

def main():
    os.umask(0o077)
    root.mkdir(mode=0o700, exist_ok=True)
    private.mkdir(mode=0o700,exist_ok=True)
    with (root/'startup.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        admin,root_key=profile('db-admin.cnf','root')
        agent,agent_key=profile('runtime.cnf','agent')
        cmd=[str(install/'bin/mysqld'),'--no-defaults',f'--basedir={install}',f'--datadir={db}',
             f'--log-error={log}', '--secure-file-priv=NULL', '--skip-networking','--mysqlx=OFF',
             '--skip-log-bin','--innodb-buffer-pool-size=64M','--performance-schema=OFF',
             '--innodb-use-native-aio=OFF','--skip-name-resolve',f'--socket={sock}',f'--pid-file={root}/mysql.pid']
        if not (db/'mysql').is_dir():
            if not bootstrap or (db.is_dir() and any(db.iterdir())):
                raise RuntimeError('Dedicated data directory requires initialization or recovery')
            subprocess.run(cmd+['--initialize-insecure'],check=True,env=env,timeout=120)
        if not sock.exists():
            subprocess.run(cmd+['--daemonize'],check=True,env=env,timeout=90)
        if not bootstrap:
            if run_mysql(agent,'SELECT COUNT(*) FROM jiatan_activity_agent.approved_case').returncode:
                raise RuntimeError('Agent MySQL identity unavailable')
            return
        if run_mysql(admin,'SELECT 1').returncode:
            blank=['--no-defaults','--user=root','--protocol=SOCKET',f'--socket={sock}']
            if run_mysql(blank,f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{root_key}';").returncode:
                raise RuntimeError('Existing database credentials do not match; aborted')
        cases=json.loads((private/'confirmed-cases.json').read_bytes())
        schema=Path(__file__).with_name('schema.sql').read_text()
        sql=[schema, f"CREATE USER IF NOT EXISTS 'agent'@'localhost' IDENTIFIED BY '{agent_key}';",
             "GRANT SELECT ON jiatan_activity_agent.approved_case TO 'agent'@'localhost';",
             "GRANT INSERT ON jiatan_activity_agent.context_use TO 'agent'@'localhost';", 'START TRANSACTION;']
        for row in cases:
            cols=('key','scope','hash','summary','hint','approvedAt')
            vals=','.join("CONVERT(X'"+row[k].encode().hex()+"' USING utf8mb4)" for k in cols)
            sql.append("INSERT INTO approved_case(case_key,case_scope,source_sha256,confirmed_summary,recognition_hint,approved_at,revision) "
                       f"VALUES({vals},{int(row['revision'])}) ON DUPLICATE KEY UPDATE case_key=case_key;")
        sql.append('COMMIT;')
        if run_mysql(admin,'\n'.join(sql)).returncode: raise RuntimeError('Case bootstrap failed')
        count=run_mysql(agent,'SELECT COUNT(*) FROM jiatan_activity_agent.approved_case WHERE active=TRUE')
        assert count.returncode==0 and int(count.stdout)==len(cases)
        print('Active cases:',len(cases),flush=True)
if __name__=='__main__': main()
