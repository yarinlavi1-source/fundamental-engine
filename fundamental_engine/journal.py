"""Append-only research checkpoints with optimistic concurrency and focused deltas."""
from datetime import datetime, timezone, date
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from .supervisor import review


class Journal:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS research_checkpoints(
          case_id TEXT NOT NULL, revision INTEGER NOT NULL, company_id TEXT NOT NULL,
          as_of TEXT NOT NULL, created_at TEXT NOT NULL, content_hash TEXT NOT NULL,
          case_json TEXT NOT NULL, review_json TEXT NOT NULL, PRIMARY KEY(case_id,revision))''')
        self.db.commit()

    def close(self):
        self.db.close()

    def save(self,case,case_id=None,expected_revision=0):
        if isinstance(expected_revision,bool) or not isinstance(expected_revision,int) or expected_revision<0:
            raise ValueError('expected_revision must be a nonnegative integer')
        result=review(case)
        raw=json.dumps(case,sort_keys=True,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>3*1024*1024:
            raise ValueError('Checkpoint exceeds 3 MiB')
        if case_id is None:
            if expected_revision!=0:
                raise ValueError('New case expects revision 0')
            case_id=str(uuid.uuid4())
        else:
            case_id=str(uuid.UUID(case_id))
        try:
            self.db.execute('BEGIN IMMEDIATE')
            prior=self.db.execute('SELECT * FROM research_checkpoints WHERE case_id=? ORDER BY revision DESC LIMIT 1',(case_id,)).fetchone()
            revision=prior['revision'] if prior else 0
            if revision!=expected_revision:
                raise ValueError('Revision conflict: reload latest checkpoint before updating')
            if prior:
                if prior['company_id']!=case['identity']['company_id']:
                    raise ValueError('Cannot change issuer identity within a research case')
                if date.fromisoformat(case['as_of'])<date.fromisoformat(prior['as_of']):
                    raise ValueError('Historical rewind requires a new case ID')
            self.db.execute('INSERT INTO research_checkpoints VALUES(?,?,?,?,?,?,?,?)',(
              case_id,revision+1,case['identity']['company_id'],case['as_of'],datetime.now(timezone.utc).isoformat(),
              hashlib.sha256(raw.encode()).hexdigest(),raw,json.dumps(result,ensure_ascii=False,allow_nan=False)))
            self.db.commit()
        except Exception:
            self.db.rollback();raise
        return {'case_id':case_id,'revision':revision+1,'status':result['status'],'next_actions':result['next_actions'],
                'note':'Stored locally; private portfolio/source data is not published to GitHub.'}

    def load(self,case_id,revision=None):
        case_id=str(uuid.UUID(case_id))
        if revision is not None:
            if isinstance(revision,bool) or not isinstance(revision,int) or revision<1:
                raise ValueError('revision must be a positive integer')
            row=self.db.execute('SELECT * FROM research_checkpoints WHERE case_id=? AND revision=?',(case_id,revision)).fetchone()
        else:
            row=self.db.execute('SELECT * FROM research_checkpoints WHERE case_id=? ORDER BY revision DESC LIMIT 1',(case_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown research checkpoint')
        return {'case_id':case_id,'revision':row['revision'],'created_at':row['created_at'],
                'content_hash':row['content_hash'],'case':json.loads(row['case_json']),'review':json.loads(row['review_json'])}

    def history(self,company_id):
        rows=self.db.execute('SELECT case_id,revision,as_of,created_at,content_hash FROM research_checkpoints WHERE company_id=? ORDER BY as_of,created_at',(company_id,))
        return [dict(r) for r in rows]

    def compare(self,case_id,first_revision,second_revision):
        a=self.load(case_id,first_revision)['review'];b=self.load(case_id,second_revision)['review']
        before={c['id']:c for c in a['claim_assessments']};after={c['id']:c for c in b['claim_assessments']}
        changes=[]
        for cid in sorted(before.keys()|after.keys()):
            old,new=before.get(cid),after.get(cid)
            if old!=new:
                changes.append({'claim_id':cid,'before':old,'after':new})
        return {'case_id':case_id,'from_revision':first_revision,'to_revision':second_revision,
                'status_before':a['status'],'status_after':b['status'],
                'claim_changes':changes,'gates_before':a['gates'],'gates_after':b['gates'],
                'new_questions':[q for q in b['questions'] if q not in a['questions']]}
