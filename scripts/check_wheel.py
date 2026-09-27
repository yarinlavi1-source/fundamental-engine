"""Verify the packaged brain and execution away from the repository checkout."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import zipfile
ROOT=Path(__file__).resolve().parents[1]
wheels=sorted((ROOT/'runs/wheels').glob('fundamental_engine-*.whl'))
if not wheels:raise SystemExit('Build a wheel in runs/wheels first')
with tempfile.TemporaryDirectory() as temp:
    with zipfile.ZipFile(wheels[-1]) as archive:archive.extractall(temp)
    code='''import sys,json
sys.path.insert(0,sys.argv[1])
from fundamental_engine.supervisor import packet,catalog,review
from fundamental_engine import __version__
records=catalog()['packets']
assert all(packet(r['id'])['content'] for r in records)
case=json.loads(open(sys.argv[2],encoding='utf-8').read())
assert review(case)['status']=='ready_for_conditional_synthesis'
from fundamental_engine.valuation import value_company
v=json.loads(open(sys.argv[3],encoding='utf-8').read())
assert len(value_company(v)['annual_values'])==6
from fundamental_engine.discovery import scan
d=json.loads(open(sys.argv[4],encoding='utf-8').read())
assert scan(d)['opportunities'][0]['research_lane']=='underwrite_early_growth'
from fundamental_engine.plain import plain_verdict
p=json.loads(open(sys.argv[5],encoding='utf-8').read());p['valuation_case']=v
assert plain_verdict(p)['price']['bucket']=='cheap'
print(json.dumps({'plain_verdict':'passed','discovery':'passed','annual_valuation':'passed','version':__version__,'packaged_packets':len(records),'installed_review':'passed'}))
'''
    completed=subprocess.run([sys.executable,'-I','-c',code,temp,str(ROOT/'examples/research_dossier_demo.json'),str(ROOT/'examples/valuation/infrastructure.json'),str(ROOT/'examples/discovery_demo.json'),str(ROOT/'examples/plain_demo.json')],
                              cwd=temp,text=True,capture_output=True,timeout=20)
    if completed.returncode:raise SystemExit(completed.stderr)
    print(completed.stdout.strip())
