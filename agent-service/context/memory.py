"""MySQL access for Activity Agent context. Runtime and administration have separate accounts."""

import argparse
import json
from pathlib import Path
import re
import subprocess

MAX_HINTS = 3
DATABASE = 'jiatan_activity_agent'


def literal(value):
    return "CONVERT(X'" + value.encode('utf-8').hex() + "' USING utf8mb4) COLLATE utf8mb4_0900_as_cs"


class ContextUnavailable(RuntimeError):
    pass


class CaseMemory:
    def __init__(self, mysql, profile):
        self.mysql = str(mysql)
        path = Path(profile)
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise ContextUnavailable('A private MySQL client profile is required')
        self.profile = str(path)

    def execute(self, sql):
        try:
            result = subprocess.run([self.mysql, '--defaults-file=' + self.profile, '--batch',
                                     '--raw', '--skip-column-names', '--binary-mode',
                                     '--default-character-set=utf8mb4', DATABASE],
                                    input=sql.encode('utf-8'), capture_output=True, timeout=4, check=True)
            return result.stdout.decode('utf-8')
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise ContextUnavailable('Activity Agent MySQL query failed') from None

    def health(self):
        return self.execute('SELECT COUNT(*) FROM approved_case;').strip().isdigit()

    def approved(self, scope):
        result = self.execute(f"""
            SELECT JSON_OBJECT('key', c.case_key, 'revision', c.revision, 'hint', c.recognition_hint)
              FROM approved_case c
             WHERE c.active = TRUE AND c.case_scope = {literal(scope)}
               AND c.revision = (SELECT MAX(x.revision) FROM approved_case x
                                  WHERE x.case_key = c.case_key)
             ORDER BY c.approved_at DESC, c.case_key
             LIMIT {MAX_HINTS};
        """)
        return [json.loads(line) for line in result.splitlines()]

    def note_use(self, request_id, entries):
        tuples = ','.join(f"({literal(request_id)}, {literal(row['key'])}, {row['revision']})" for row in entries)
        if tuples:
            self.execute(f'INSERT IGNORE INTO context_use (request_id, case_key, case_revision) VALUES {tuples};')

    def approve(self, record, *, seed=False):
        fields = {'caseKey', 'revision', 'scope', 'sourceSha256', 'confirmedSummary', 'recognitionHint'}
        if (set(record) != fields or not re.fullmatch(r'[a-z0-9_/-]{1,80}', record['scope'])
                or not re.fullmatch(r'[a-z0-9-]{1,80}', record['caseKey'])
                or type(record['revision']) is not int or not 1 <= record['revision'] <= 65535
                or not re.fullmatch(r'[0-9a-f]{64}', record['sourceSha256'])
                or any(not isinstance(record[k], str) or not 1 <= len(record[k]) <= 500
                       for k in ('confirmedSummary', 'recognitionHint'))):
            raise ValueError('Invalid approved case')
        values = ','.join(literal(record[k]) for k in ('caseKey','scope','sourceSha256','confirmedSummary','recognitionHint'))
        # A packaged first revision must preserve later operator edits and deactivations.
        if seed and record['revision'] != 1:
            raise ValueError('Only initial case revisions may be seeded')
        duplicate = ' ON DUPLICATE KEY UPDATE case_key=case_key' if seed else ''
        self.execute(f"""INSERT INTO approved_case
                        (case_key, case_scope, source_sha256, confirmed_summary, recognition_hint, revision)
                        VALUES ({values},{record['revision']}){duplicate};""")


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description='Record a case that an operator has already approved')
    cli.add_argument('--mysql', default='/usr/local/mysql/bin/mysql')
    cli.add_argument('--admin-profile', required=True)
    cli.add_argument('approved_case', type=Path)
    args = cli.parse_args()
    CaseMemory(args.mysql, args.admin_profile).approve(json.loads(args.approved_case.read_text()))
    print('Approved case recorded')
