import json
import os
from pathlib import Path
import tempfile
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'clip-review'))
import pipeline as p
import social_bridge as s
import api

SAMPLE=Path(__file__).resolve().parents[1]/'sample'/'Kyle_Clip_List.pdf'

def test_candidate_bridge(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'JOBS',tmp_path/'jobs')
    monkeypatch.setattr(s.p,'JOBS',tmp_path/'jobs')
    monkeypatch.setenv('KINROOT_METADATA_AI','false')
    jid=p.create_job(SAMPLE.read_bytes(),SAMPLE.name)
    task=s.get_task_for_api(jid,'S5-A01')
    assert 'recliner' in task['scene'].lower()
    assert task['clip_id']=='S5-A01'
    r=s.accept_candidate(jid,{'clip_id':'S5-A01','platform':'Pinterest',
        'source_url':'https://www.pinterest.com/pin/12345/',
        'video_url':'https://i.pinimg.com/videos/example.mp4',
        'title':'Elderly woman in recliner legs up','description':'Natural scene','status':'candidate_found'})
    assert r['recorded']=='candidate'
    assert p.get_job(jid)['tasks'][0]['social_candidates'][0]['source_url'].startswith('https://')
    r2=s.accept_candidate(jid,{'clip_id':'S5-A01','platform':'Pinterest',
        'source_url':'https://www.pinterest.com/pin/12345/'})
    assert r2['recorded']=='duplicate'
    assert not s._media_host_allowed('http://localhost:8502/a.mp4')
    assert not s._media_host_allowed('https://127.0.0.1/a.mp4')
    assert not s._media_host_allowed('https://evilpinimg.com/a.mp4')
    assert s._media_host_allowed('https://i.pinimg.com/videos/a.mp4')
    assert api.get_task(jid,'S5-A01')['clip_id']=='S5-A01'


def test_n8n_workflow_import_shape():
    w=json.loads((Path(__file__).resolve().parents[1]/'Kinroot_FULL_n8n_v3.json').read_text())
    names={n['name'] for n in w['nodes']}
    for src,values in w['connections'].items():
        assert src in names
        for branch in values['main']:
            for edge in branch: assert edge['node'] in names
    assert any(n['type']=='n8n-nodes-base.webhook' for n in w['nodes'])
    assert len([n for n in w['nodes'] if n['name'].startswith('Send ')])==4
    assert not any(n.get('credentials') for n in w['nodes'])
