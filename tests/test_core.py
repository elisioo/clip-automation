import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'clip-review'))
import pipeline as p

SAMPLE=Path(__file__).resolve().parents[1]/'sample'/'Kyle_Clip_List.pdf'

def test_sample_parsing():
    data=SAMPLE.read_bytes()
    jid=p.create_job(data,SAMPLE.name)
    j=p.get_job(jid)
    assert len(j['tasks'])==22
    assert j['tasks'][0]['filename']=='S5-A01.mp4'
    assert j['tasks'][1]['filename']=='S5-A01_alt.mp4'
    assert len([t for t in j['tasks'] if t['id'].endswith('_alt')])==8
    return j

def test_rights_filter():
    assert p.allowed_license('CC BY 4.0')
    assert p.allowed_license('CC0')
    assert p.allowed_license('Public Domain')
    assert not p.allowed_license('CC BY-NC 4.0')
    assert not p.allowed_license('CC BY-SA 4.0')
    assert not p.allowed_license('All rights reserved')

def test_trim_and_review():
    j=test_sample_parsing()
    folder=p.JOBS/j['job_id']; src=folder/'incoming'/'S5-A01.source'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',
                    'color=c=blue:s=1920x1080:r=2:d=6',
                    '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-f','mp4',str(src)],check=True,timeout=90)
    info=p.video_info(src)
    assert info['width']==1920 and info['height']==1080
    preview=folder/'previews'/'S5-A01.mp4'
    cut=p.trim_preview(src,preview,info,center=3.0)
    assert preview.is_file() and 4.5 <= cut['duration'] <= 5.5
    j['tasks'][0].update({'status':'review','preview':str(preview),'original':str(src)})
    p.review(j,0,True,'test approve')
    assert j['tasks'][0]['status']=='approved'
    assert (folder/'approved'/'S5-A01.mp4').is_file()
    assert not src.exists() and not preview.exists()
    # Test second clip reject and cleanup too.
    j['tasks'][1].update({'status':'review','preview':str(folder/'previews'/'S5-A01_alt.mp4'),
                           'original':str(folder/'incoming'/'S5-A01_alt.source')})
    Path(j['tasks'][1]['preview']).write_bytes(b'junk')
    Path(j['tasks'][1]['original']).write_bytes(b'junk')
    p.review(j,1,False,'test reject')
    assert j['tasks'][1]['status']=='rejected'
    assert not Path(j['tasks'][1]['preview']).exists() and not Path(j['tasks'][1]['original']).exists()
    print('All offline checks passed. Job ID:',j['job_id'])

if __name__=='__main__':
    test_rights_filter()
    test_trim_and_review()
