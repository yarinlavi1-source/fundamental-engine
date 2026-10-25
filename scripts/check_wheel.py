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
assert 'underwriting_audit' in value_company(v)
assert 'reproduction_input' in value_company(v)
from fundamental_engine.valuation_audit import audit_valuation
assert packet('valuation_integrity')['content']
from fundamental_engine.discovery import scan
d=json.loads(open(sys.argv[4],encoding='utf-8').read())
assert scan(d)['opportunities'][0]['research_lane']=='underwrite_early_growth'
from fundamental_engine.plain import plain_verdict
p=json.loads(open(sys.argv[5],encoding='utf-8').read());p['valuation_case']=v
assert plain_verdict(p)['price']['bucket'] in {'cheap','cheap_even_bear'}
from fundamental_engine.profile import classify_company
assert classify_company(json.loads(open(sys.argv[6],encoding='utf-8').read()))['lynch']=='stalwart'
from fundamental_engine.drivers import build_case, implied_growth_shift
from fundamental_engine.valuation_tools import implied_cost_of_equity
spec=json.loads(open(sys.argv[7],encoding='utf-8').read())
dc=build_case(spec)
assert value_company(dc)['status']=='conditional_valuation'
assert implied_cost_of_equity(dc)['status']=='solved'
assert implied_growth_shift(spec)['status']=='solved'
from fundamental_engine.quarter import quarter_review
assert packet('quarter_review')['content']
from fundamental_engine.econ import registry, reading_plan
reg=registry()
assert reg['economics_version']==1 and len(reg['models'])==10 and len(reg['overlays'])==6 and reg['rules']['_status']
assert reading_plan('broker')['steps'][1]['missing'] and packet('economic_models')['content']
from fundamental_engine.econ import econ_review
ib=econ_review(json.loads(open(sys.argv[8],encoding='utf-8').read()),{'card_type':'financials'})
assert ib['selection']['primary']=='broker' and ib['review']['status']=='assessed' and '1,340' in ib['text_he']['how_it_earns'] and ib['text_he']['note']
pq=json.loads(open(sys.argv[9],encoding='utf-8').read())
ov={'primary':'data_platform_software','reason':'customers x revenue per customer (8-K)','evidence_ids':['pltr_8k_q2_2026_ex991']}
peak=lambda m:[f['severity'] for f in quarter_review({**pq,'economics':{'model':m}})['flags'] if f['key'] in ('peak_margin_run_rate','operating_leverage_or_peak')]
assert peak(ov)==['medium'] and peak('data_platform_software')==['high']
from fundamental_engine.evidence_pack import to_inputs, to_bundle
ep=json.loads(open(sys.argv[10],encoding='utf-8').read())
assert to_inputs(ep)['company_path']['scenarios']['base']['source_tier']=='own_history' and to_bundle(ep)['sources']
assert 'Position' in packet('master_process')['content'] and 'syndicated' in packet('sources_registry')['content']
from fundamental_engine.scenario_reasons import build as scenario_reasons
assert scenario_reasons({})['status']=='not_available'
assert 'scenario_reasons' in packet('annual_valuation')['content']
print(json.dumps({'scenario_reasons':'passed','evidence_pack':'passed','skeleton_packets':'passed','quarter_review':'passed','econ_registry':'passed','econ_review':'passed','econ_peak_exception':'passed','drivers':'passed','plain_verdict':'passed','discovery':'passed','annual_valuation':'passed','version':__version__,'packaged_packets':len(records),'installed_review':'passed'}))
'''
    completed=subprocess.run([sys.executable,'-I','-c',code,temp,str(ROOT/'examples/research_dossier_demo.json'),str(ROOT/'examples/valuation/infrastructure.json'),str(ROOT/'examples/discovery_demo.json'),str(ROOT/'examples/plain_demo.json'),str(ROOT/'examples/real/avgo_plain_2025.json'),str(ROOT/'examples/valuation/software_drivers.json'),
                               str(ROOT/'tests/fixtures/econ/ibkr_2026q3.json'),str(ROOT/'tests/fixtures/econ/pltr_2026q2_filed.json'),str(ROOT/'examples/evidence_pack_demo.json')],
                              cwd=temp,text=True,capture_output=True,timeout=20)
    if completed.returncode:raise SystemExit(completed.stderr)
    print(completed.stdout.strip())
