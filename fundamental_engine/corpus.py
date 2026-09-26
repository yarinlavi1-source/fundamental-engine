"""Local, dated source corpus. Text is untrusted data; never executed as instructions."""
import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from .research import text

KINDS = {'filing', 'earnings_release', 'transcript', 'commentary', 'research', 'user_note'}


class Corpus:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS documents(
          id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, origin_id TEXT NOT NULL,
          title TEXT NOT NULL, locator TEXT NOT NULL, published_at TEXT NOT NULL,
          available_at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
          metadata TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS documents_cutoff ON documents(available_at);
        CREATE INDEX IF NOT EXISTS documents_origin ON documents(origin_id);
        ''')

    def close(self):
        self.db.close()

    def ingest(self, document):
        for key in ('id','title','locator','origin_id','body'):
            text(document[key], key)
        if document['kind'] not in KINDS:
            raise ValueError('Unknown document kind')
        if len(document['body'].encode('utf-8')) > 2*1024*1024:
            raise ValueError('Document exceeds 2 MiB text limit')
        if date.fromisoformat(document['available_at']) < date.fromisoformat(document['published_at']):
            raise ValueError('Availability precedes publication')
        normalized = re.sub(r'\s+', ' ', document['body']).strip()
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        # A content hash is identity, not evidence of truth; revisions need new IDs.
        metadata = json.dumps({k:v for k,v in document.items() if k != 'body'},sort_keys=True,ensure_ascii=False,allow_nan=False)
        old = self.db.execute('SELECT * FROM documents WHERE id=?',(document['id'],)).fetchone()
        if old:
            if old['content_hash'] != digest or old['metadata'] != metadata:
                raise ValueError('Document ID already exists with different content/metadata; use a revision ID')
            return {'id':old['id'],'status':'already_present','content_hash':digest}
        duplicate = self.db.execute('SELECT id FROM documents WHERE content_hash=?',(digest,)).fetchone()
        with self.db:
            self.db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)',(
                document['id'],digest,document['origin_id'],document['title'],document['locator'],
                document['published_at'],document['available_at'],document['kind'],document['body'],metadata))
        return {'id':document['id'],'status':'stored','content_hash':digest,
                'duplicate_content_of':duplicate['id'] if duplicate else None,
                'note':'Source claims remain unverified; shared origins do not constitute independent corroboration.'}

    def search(self, query, as_of, limit=5):
        date.fromisoformat(as_of)
        text(query,'query')
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1 <= limit <= 20:
            raise ValueError('limit must be an integer from 1 to 20')
        terms = set(re.findall(r'\w+',query.casefold()))
        if not terms or len(terms)>30:
            raise ValueError('Query requires 1..30 terms')
        results = []
        for r in self.db.execute('SELECT * FROM documents WHERE available_at<=?',(as_of,)):
            hay = (r['title']+' '+r['body']).casefold()
            matches = sum(t in hay for t in terms)
            if matches:
                at = min((r['body'].casefold().find(t) for t in terms if t in r['body'].casefold()), default=0)
                results.append({'id':r['id'],'title':r['title'],'locator':r['locator'],
                    'available_at':r['available_at'],'kind':r['kind'],'origin_id':r['origin_id'],
                    'content_hash':r['content_hash'],'matched_terms':matches,
                    'excerpt':r['body'][max(0,at-180):max(0,at-180)+1800]})
        # Deduplicate both exact copies and same-origin syndication in the result set.
        seen_hashes, seen_origins, out = set(), set(), []
        for r in sorted(results,key=lambda x:(-x['matched_terms'],x['id'])):
            if r['content_hash'] in seen_hashes or r['origin_id'] in seen_origins:
                continue
            seen_hashes.add(r['content_hash']); seen_origins.add(r['origin_id']); out.append(r)
            if len(out) == limit:
                break
        return {'results':out,'as_of':as_of,'untrusted_source_text':True,
                'note':'Literal term retrieval, not a truth ranking. Common origins deduplicated; metadata is analyst supplied.'}
