"""Kinroot Clip Review: PDF -> licensed video discovery -> AI-assisted scene selection -> clips.

Intentionally does NOT scrape social sites or remove watermarks.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

import fitz
import requests
from docx import Document

ROOT = Path('/app/data') if Path('/app').exists() else Path(__file__).resolve().parent / 'data'
JOBS = ROOT / 'jobs'
# Groq is preferred when a GROQ_API_KEY is provided; OpenAI remains an optional fallback.
HEADERS = {'User-Agent': 'KinrootLocalClipReview/0.1 (local personal video research; public-media API client)'}
MAX_BYTES = 80 * 1024 * 1024
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def ai_provider() -> str | None:
    if os.environ.get('GROQ_API_KEY', '').strip():
        return 'Groq'
    if os.environ.get('OPENAI_API_KEY', '').strip():
        return 'OpenAI'
    return None


def ai_available() -> bool:
    return ai_provider() is not None


def ai_model(vision: bool = False) -> str:
    if ai_provider() == 'Groq':
        return os.environ.get('GROQ_VISION_MODEL' if vision else 'GROQ_TEXT_MODEL',
                              'qwen/qwen3.8-27b' if vision else 'openai/gpt-oss-20b')
    return os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini')


def ai_client():
    from openai import OpenAI
    if ai_provider() == 'Groq':
        return OpenAI(api_key=os.environ['GROQ_API_KEY'].strip(),
                      base_url='https://api.groq.com/openai/v1', timeout=60.0)
    if ai_provider() == 'OpenAI':
        return OpenAI(api_key=os.environ['OPENAI_API_KEY'].strip(), timeout=60.0)
    raise RuntimeError('Set GROQ_API_KEY (free-tier available), or OPENAI_API_KEY, in the local .env file.')


def read_document(data: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == '.pdf':
        with fitz.open(stream=data, filetype='pdf') as doc:
            return '\n'.join(page.get_text(sort=True) for page in doc)
    if ext == '.txt':
        return data.decode('utf-8-sig', errors='replace')
    if ext == '.docx':
        d = Document(io.BytesIO(data))
        blocks = [p.text for p in d.paragraphs]
        for table in d.tables:
            blocks += [' | '.join(c.text for c in row.cells) for row in table.rows]
        return '\n'.join(blocks)
    raise ValueError('Upload a PDF, DOCX, or TXT file.')


def declared_clip_counts(text: str) -> tuple[int, int] | None:
    """Return (base IDs, alternatives) when the client explicitly states them."""
    m = re.search(r'(\d+)\s+clip\s+IDs?\s*\(\s*(\d+)\s+hook\s+clips?\s+need\s+2\s+options',
                  text, flags=re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _column_fragments(line: str) -> list[tuple[int, str]]:
    """Split fixed-width PDF table lines without losing their column offsets."""
    return [(m.start(), m.group(0).strip()) for m in re.finditer(r'\S.*?(?=\s{2,}|$)', line)
            if m.group(0).strip()]


def extract_table_plan(text: str) -> dict | None:
    """Read a Kinroot clip table deterministically, including _alt and wrapped rows.

    Only use this method when the brief visibly contains the 'MUST show' table.
    It prevents a language model from silently dropping requirements.
    """
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines)
                         if 'clip MUST show' in line and 'Start your search' in line), None)
    if header_index is None:
        return None
    rows: list[dict] = []
    for line in lines[header_index + 1:]:
        if re.search(r'^\s*Delivery message in Slack', line, flags=re.IGNORECASE):
            break
        m = re.match(r'^\s*([A-Za-z][A-Za-z0-9]{0,12}-[A-Za-z0-9]{2,12})(?=\s|$)', line)
        fragments = _column_fragments(line)
        if m:
            cid = m.group(1)
            # Kinroot fixed-width table: ID | voiceover | MUST SHOW | search.
            if len(fragments) < 3 or fragments[0][1] != cid:
                raise ValueError(f'Could not read the scene description for {cid}. Review the PDF table.')
            description = fragments[2][1]
            query = fragments[3][1] if len(fragments) >= 4 else description
            rows.append({'id': cid, 'description': description, 'query': query,
                         'status': 'needs_source', '_alt': False})
            continue
        if not rows:
            continue
        if re.match(r'^\s*\+\s*_alt\b', line):
            rows[-1]['_alt'] = True
        # Continuation rows can wrap scene descriptions and search suggestions.
        # The fixed-width table's scene column begins near character 38; the
        # search column begins near character 80. Ignore voiceover fragments.
        for pos, fragment in fragments:
            if fragment == '+ _alt':
                continue
            if 30 <= pos < 77:
                rows[-1]['description'] += ' ' + fragment
            elif pos >= 77:
                rows[-1]['query'] += ' ' + fragment

    if not rows:
        return None
    counts = declared_clip_counts(text)
    alt_count = sum(bool(row['_alt']) for row in rows)
    if counts and (len(rows), alt_count) != counts:
        raise ValueError(f'Incomplete PDF extraction: found {len(rows)} clip IDs and '
                         f'{alt_count} alternatives, but client requires {counts[0]} IDs '
                         f'and {counts[1]} alternatives. Do not start sourcing.')
    ids = [row['id'] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate clip IDs in PDF table; review the brief.')
    tasks = []
    for row in rows:
        base = {k: row[k] for k in ('id', 'description', 'query', 'status')}
        tasks.append(base)
        if row['_alt']:
            tasks.append({**base, 'id': row['id'] + '_alt'})
    editor_match = re.search(r'Clips for\s+([^:]+):', text)
    editor = editor_match.group(1).strip() if editor_match else 'Unknown editor'
    batch_match = re.search(r'Batch\s+(\d+)', text, flags=re.IGNORECASE)
    date_match = re.search(r'\b20\d{2}-\d{2}-\d{2}\b', text)
    id_match = re.search(r'\b(ED\d+)\s+' + re.escape(editor) + r'\b', text, flags=re.IGNORECASE)
    delivery_date = date_match.group(0) if date_match else ''
    output_directory = 'Kinroot Content / ' + (id_match.group(1) + ' ' if id_match else '') + editor
    if delivery_date:
        output_directory += ' / ' + delivery_date
    rules_match = re.search(r'(?m)^The woman:.*?(?=\n\s*ID\s+Voiceover line)', text, flags=re.DOTALL)
    rules = rules_match.group(0).strip() if rules_match else ''
    return {'editor': editor, 'batch': batch_match.group(1) if batch_match else '',
            'delivery_date': delivery_date, 'output_directory': output_directory,
            'rules': rules, 'tasks': tasks}


def validate_task_count(text: str, plan: dict) -> None:
    counts = declared_clip_counts(text)
    if not counts:
        return
    expected = sum(counts)
    tasks = plan.get('tasks', [])
    base = [t for t in tasks if not str(t.get('id', '')).endswith('_alt')]
    alts = [t for t in tasks if str(t.get('id', '')).endswith('_alt')]
    if len(tasks) != expected or len(base) != counts[0] or len(alts) != counts[1]:
        raise ValueError(f'Incomplete extraction: expected {counts[0]} clip IDs + '
                         f'{counts[1]} alternatives = {expected} files, '
                         f'but extracted {len(tasks)}. Sourcing is blocked; review the PDF.')


def extract_plan(text: str) -> dict:
    # All other briefs use AI parsing; never silently reuse a potentially changed old brief.
    if not ai_available():
        raise RuntimeError('For a new client brief, configure GROQ_API_KEY in the local .env file. The included Kyle PDF works without a key.')
    instruction = '''You are extracting a production footage clip brief. Output one JSON object ONLY with fields
editor, batch, delivery_date (YYYY-MM-DD or blank), output_directory, rules, tasks. tasks is an array
of {id,description,query,status} records. Exactly preserve all explicitly specified clip IDs and _alt
variants as separate files, one per item. status must be "needs_source". description is what MUST be
visible and overrules the search query. Never invent missing clip IDs. If a PDF is ambiguous, preserve
what is readable and state uncertainty inside a task's description. Do not infer approval or rights.
No markdown or backticks. All ids must use only alphanumeric, underscores, or hyphens.'''
    completion = ai_client().chat.completions.create(
        model=ai_model(),
        response_format={'type':'json_object'},
        messages=[{'role':'system','content':instruction}, {'role':'user','content':text[:85000]}],
        temperature=0,
    )
    plan = json.loads(completion.choices[0].message.content)
    if not isinstance(plan.get('tasks'), list) or not plan['tasks']:
        raise ValueError('The model could not extract any clip tasks.')
    tasks=[]
    for row in plan['tasks']:
        cid=str(row.get('id','')).strip().removesuffix('.mp4')
        if not re.fullmatch(r'[A-Za-z0-9_-]{2,90}',cid):
            continue
        tasks.append({'id':cid,'description':str(row.get('description','')),
                      'query':str(row.get('query',row.get('description',''))),
                      'status':'needs_source'})
    if len({a['id'] for a in tasks}) != len(tasks):
        raise ValueError('Duplicate clip IDs in the extracted plan; check PDF before processing.')
    plan['tasks']=tasks
    validate_task_count(text, plan)
    return plan


def _json_save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def create_job(data: bytes, filename: str) -> str:
    text=read_document(data,filename)
    if not text.strip() or len(text.strip()) < 20:
        raise ValueError('The document contains little readable text. Please upload a text-based PDF, DOCX, or TXT brief (scanned-image OCR is not included yet).')
    sample_sha='24e781c1026a5ad4e40db52b0732fa1bd87725d2c39cf76b13006eb901f6c8d7'
    # Prefer the exact table written by the client over probabilistic AI extraction.
    # The old extractor sometimes returned only 3 of John's required 12 files.
    plan = extract_table_plan(text)
    if plan is None:
        if hashlib.sha256(data).hexdigest() == sample_sha:
            plan = json.loads((Path(__file__).resolve().parent / 'kyle_batch03.json').read_text(encoding='utf-8'))
        else:
            plan = extract_plan(text)
    validate_task_count(text, plan)
    jid=uuid.uuid4().hex[:12]
    folder=JOBS / jid
    folder.mkdir(parents=True)
    (folder / 'incoming').mkdir()
    (folder / 'previews').mkdir()
    (folder / 'approved').mkdir()
    ext=Path(filename).suffix.lower()
    (folder / ('brief'+ext)).write_bytes(data)
    for task in plan['tasks']:
        task['candidates_checked']=0
        task['result_note']='Not searched yet'
        task['filename']=task['id']+'.mp4'
    plan.update({'job_id':jid,'brief_filename':Path(filename).name,'created_at':time.strftime('%Y-%m-%d %H:%M:%S'),
                 'status':'created','notes':[]})
    _json_save(folder/'job.json',plan)
    return jid


def get_job(jid: str) -> dict:
    if not re.fullmatch(r'[0-9a-f]{12}',jid):
        raise ValueError('Invalid job identifier')
    return json.loads((JOBS/jid/'job.json').read_text(encoding='utf-8'))


def save_job(job:dict) -> None:
    _json_save(JOBS/job['job_id']/'job.json',job)


def list_jobs() -> list[str]:
    if not JOBS.exists():
        return []
    return sorted([p.name for p in JOBS.iterdir() if (p/'job.json').exists()],reverse=True)


def _get(url: str, params=None, timeout=18):
    r=SESSION.get(url,params=params,timeout=timeout)
    r.raise_for_status()
    return r.json()


def _value(meta: dict, key: str) -> str:
    v=meta.get(key,{})
    if isinstance(v,dict): return str(v.get('value',''))
    return str(v)


def allowed_license(text: str) -> bool:
    """Conservatively limit automatic downloads to CC0 / PD / CC-BY; rights still reviewed."""
    t=re.sub(r'\s+',' ',text).lower().strip()
    if any(bad in t for bad in ('noncommercial','non-commercial','by-nc','/by-nc','-nd','noderivatives','no-derivatives','by-sa','sharealike','share-alike')):
        return False
    return ('cc0' in t or 'public domain' in t or 'publicdomain' in t or
            'creativecommons.org/licenses/by/' in t or 'cc by ' in t or 'cc-by-' in t)


def search_commons(query: str, limit=8) -> list[dict]:
    terms=' '.join(re.findall(r'[\w-]+',query)[:5])
    if not terms:return []
    data=_get('https://commons.wikimedia.org/w/api.php',params={
        'action':'query','generator':'search','gsrsearch':terms+' filetype:video',
        'gsrnamespace':6,'gsrlimit':min(limit,15),'prop':'imageinfo',
        'iiprop':'url|size|mime|extmetadata','format':'json',
        'iiextmetadatafilter':'LicenseShortName|UsageTerms|LicenseUrl|Artist|AttributionRequired'},timeout=25)
    out=[]
    for page in data.get('query',{}).get('pages',{}).values():
        for info in page.get('imageinfo',[]):
            mime=str(info.get('mime','')).lower()
            url=info.get('url','')
            if not (mime.startswith('video/') and url.startswith('https://upload.wikimedia.org/')):continue
            lic=' '.join(_value(info.get('extmetadata',{}),k) for k in ('LicenseShortName','LicenseUrl','UsageTerms'))
            if not allowed_license(lic): continue
            if int(info.get('size',0))>MAX_BYTES:continue
            out.append({'source':'Wikimedia Commons','title':page.get('title',''), 'download_url':url,
                        'source_url':'https://commons.wikimedia.org/wiki/'+quote(page.get('title','').replace(' ','_'),safe=':/'),
                        'license':lic,'credit':_value(info.get('extmetadata',{}),'Artist'),
                        'width':info.get('width',0),'height':info.get('height',0)})
    return out


def search_archive(query: str, limit=5) -> list[dict]:
    keywords=' '.join(re.findall(r'[A-Za-z0-9]+',query)[:4])
    if not keywords:return []
    data=_get('https://archive.org/advancedsearch.php',params={
        'q':f'mediatype:movies AND ({keywords})',
        'fl[]':['identifier','title'],'rows':min(limit,10),'output':'json'},timeout=25)
    out=[]
    for item in data.get('response',{}).get('docs',[]):
        ident=item.get('identifier','')
        if not ident or not re.fullmatch(r'[A-Za-z0-9_.-]+',ident):continue
        try:
            meta=_get('https://archive.org/metadata/'+ident,timeout=20)
            mm=meta.get('metadata',{})
            lic=str(mm.get('licenseurl',''))
            if not allowed_license(lic):continue
            files=[f for f in meta.get('files',[]) if str(f.get('name','')).lower().endswith(('.mp4','.webm','.ogv','.mov'))]
            files=[f for f in files if 0 < int(f.get('size',0)) <= MAX_BYTES]
            if not files:continue
            files.sort(key=lambda f:(str(f.get('source',''))!='original',int(f.get('size','0'))))
            f=files[0]
            out.append({'source':'Internet Archive','title':str(item.get('title',ident)),
                        'download_url':'https://archive.org/download/'+quote(ident)+'/'+quote(f['name']),
                        'source_url':'https://archive.org/details/'+quote(ident),
                        'license':lic,'credit':str(mm.get('creator','')),'width':0,'height':0})
        except Exception:
            continue
    return out


def term_overlap(query: str, title: str) -> float:
    drop={'the','a','an','and','with','in','on','at','to','for','of','her','his','their','own','video','footage','clip','woman','man'}
    a={x for x in re.findall(r'[a-z0-9]+',query.lower()) if len(x)>2 and x not in drop}
    b={x for x in re.findall(r'[a-z0-9]+',title.lower()) if len(x)>2 and x not in drop}
    if not a:return 0
    return len(a&b)/len(a)


def discover(task:dict, errors:list) -> list[dict]:
    phrase=task.get('query') or task['description']
    desc=task['description']
    phrases=list(dict.fromkeys([phrase,desc,' '.join(phrase.split()[:3])]))[:3]
    result=[]; seen=set()
    for p in phrases:
        for provider in (search_commons,search_archive):
            try:
                matches=provider(p, 6 if provider is search_commons else 3)
                for v in matches:
                    if v['download_url'] not in seen:
                        seen.add(v['download_url']); result.append(v)
            except Exception as e:
                errors.append(f'{provider.__name__}: {type(e).__name__} — {str(e)[:140]}')
    result.sort(key=lambda v:term_overlap(desc+' '+phrase,v['title']),reverse=True)
    return result[:12]


def download_file(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True,exist_ok=True)
    total=0
    try:
        with SESSION.get(url,stream=True,timeout=(15,55)) as r:
            r.raise_for_status()
            if int(r.headers.get('Content-Length') or 0)>MAX_BYTES:raise ValueError('Source exceeds 80 MB limit')
            with target.open('wb') as w:
                for block in r.iter_content(chunk_size=1024*512):
                    if block:
                        total+=len(block)
                        if total>MAX_BYTES:raise ValueError('Source exceeded 80 MB limit')
                        w.write(block)
        if total<10_000:raise ValueError('Downloaded file is too small')
    except Exception:
        target.unlink(missing_ok=True)
        raise


def video_info(p:Path) -> dict:
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format',
                                           '-of','json',str(p)],timeout=25))
    tracks=[s for s in data.get('streams',[]) if s.get('codec_type')=='video']
    if not tracks: raise ValueError('No video track')
    t=tracks[0]
    return {'width':int(t.get('width',0)), 'height':int(t.get('height',0)),
            'duration':float(data.get('format',{}).get('duration') or t.get('duration') or 0)}


def shot(p:Path, seconds:float) -> bytes:
    return subprocess.check_output(['ffmpeg','-hide_banner','-loglevel','error','-ss',str(seconds),
            '-i',str(p),'-frames:v','1','-vf','scale=640:-2','-q:v','3','-f','image2pipe','-vcodec','mjpeg','pipe:1'],timeout=35)


def relevance_threshold() -> int:
    """The product requirement is a strict >=70 visual relevance threshold."""
    return max(70, min(100, int(os.environ.get('KINROOT_MATCH_THRESHOLD', '70'))))


def assess_video(p: Path, requirement: str, duration: float) -> dict:
    """Inspect small temporal windows, not just the title or a single thumbnail.

    Each request contains at most three frames (current Groq vision model limit).
    This is still a sampled-frame estimate, never a guarantee of exact action.
    """
    if duration < 5.0:
        return {'score': 0, 'time': 0, 'reason': 'Source shorter than 5 seconds including handles.',
                'overlay_warning': 'Not inspected', 'frames_checked': 0}
    if not ai_available():
        return {'score': None, 'time': None, 'reason': 'Vision model not configured; no automated approval.',
                'overlay_warning': 'Manual inspection required', 'frames_checked': 0}
    lo, hi = 2.5, max(2.5, duration-2.5)
    n = max(1, min(5, int(os.environ.get('KINROOT_MAX_VISION_WINDOWS','4'))))
    centers = [lo + (hi-lo)*i/(n-1) for i in range(n)] if n>1 else [(lo+hi)/2]
    centers = list(dict.fromkeys(round(c, 2) for c in centers))
    best = None
    failures = []
    for center in centers:
        timestamps = [max(0.1, min(duration-0.1, center+offset)) for offset in (-1.0,0,1.0)]
        parts = [{'type':'text','text': (
            'You are assessing THREE CHRONOLOGICALLY ORDERED video frames from one short scene. '
            'Required visual scene: '+requirement[:3500] + '\n'
            'Grade the visible scene relevance 0-100, with at least 70 ONLY when the '
            'specific required objects AND action are supported by the frames. '
            'A topical video, talking head, stock image, overlay text, or adjacent condition '
            'does not equal the requested visual action. Do not infer a moving action from '
            'a single static image. If uncertain, score below 70. Do not infer rights, '
            'authenticity, or precise age. Respond with JSON keys: score (integer), '
            'reason (short), overlay_warning (short). No prose outside JSON. '
            'Frames appear in temporal order, one second apart.') }]
        try:
            for timestamp in timestamps:
                frame = base64.b64encode(shot(p,timestamp)).decode('ascii')
                parts.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+frame}})
            resp=ai_client().chat.completions.create(
                model=ai_model(vision=True),response_format={'type':'json_object'},
                messages=[{'role':'user','content':parts}],temperature=0)
            obj=json.loads(resp.choices[0].message.content)
            candidate={'score':max(0,min(100,int(obj.get('score',0)))),
                       'time':center,'reason':str(obj.get('reason',''))[:320],
                       'overlay_warning':str(obj.get('overlay_warning',''))[:240],
                       'frames_checked':len(timestamps)}
            if best is None or candidate['score']>best['score']:
                best=candidate
        except Exception as ex:
            failures.append(f'{type(ex).__name__}: {str(ex)[:120]}')
    if best is None:
        return {'score':None,'time':None,'reason':'Vision analysis failed: '+ '; '.join(failures[:2]),
                'overlay_warning':'Manual check required','frames_checked':0}
    best['windows_checked']=len(centers)-len(failures)
    best['threshold']=relevance_threshold()
    return best


def trim_preview(src:Path, dest:Path, info:dict, center:float, core_seconds:float=3, handle_seconds:float=1) -> dict:
    # Retain one second on either side when source duration permits.
    length=core_seconds+2*handle_seconds
    if info['duration'] < length:
        raise ValueError(f'Source must be at least {length:g} seconds to preserve the requested handles')
    start=max(0,min(center-core_seconds/2-handle_seconds,info['duration']-length))
    available=max(0.1,min(length,info['duration']-start))
    dest.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-ss',f'{start:.2f}','-i',str(src),
                    '-t',f'{available:.2f}','-map','0:v:0','-map','0:a?',
                    '-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',
                    '-c:a','aac','-movflags','+faststart',str(dest)],check=True,timeout=115)
    out=video_info(dest)
    if out['duration']<1 or min(out['width'],out['height'])<1080:
        dest.unlink(missing_ok=True)
        raise ValueError('Trim output failed duration or 1080p validation')
    return {'start':round(start,2),'duration':round(out['duration'],2)}


def process_one(job:dict, task_index:int) -> None:
    task=job['tasks'][task_index]
    if task['status'] not in ('needs_source','error'):
        return
    jobfolder=JOBS/job['job_id']
    errors=[]
    try:
        candidates=discover(task,errors)
        task['candidates_checked']=len(candidates)
        existing={t.get('source_url') for i,t in enumerate(job['tasks']) if i!=task_index and t.get('status') in ('review','approved')}
        candidate=None
        for v in candidates:
            if v['source_url'] in existing or v['source_url'] in task.get('rejected_sources', []):continue
            if term_overlap(task['description']+' '+task.get('query',''),v['title'])<.10:continue
            original=jobfolder/'incoming'/(task['id']+'.source')
            preview=jobfolder/'previews'/(task['id']+'.mp4')
            try:
                # A Commons metadata width/height can save needless large downloads.
                if v['width'] and v['height'] and min(int(v['width']),int(v['height']))<1080:continue
                download_file(v['download_url'],original)
                info=video_info(original)
                if min(info['width'],info['height'])<1080 or info['duration']<5:
                    raise ValueError('Does not meet 1080p and ≥3-second source checks')
                assessment=assess_video(original,task['description']+'; Client rules: '+str(job.get('rules','')),info['duration'])
                if assessment['score'] is None or assessment['score']<relevance_threshold():
                    raise ValueError(f'Visual score {assessment["score"]} below required {relevance_threshold()}/100 or AI unavailable')
                cut=trim_preview(original,preview,info,assessment['time'])
                candidate=(v,info,assessment,cut)
                break
            except Exception as e:
                errors.append(f'{v["source"]}: {str(e)[:125]}')
                original.unlink(missing_ok=True)
                preview.unlink(missing_ok=True)
        if candidate:
            v,info,assessment,cut=candidate
            task.update({'status':'review','source':v['source'],'source_url':v['source_url'],
                         'license':v['license'],'credit':v['credit'], 'source_title':v['title'],
                         'width':info['width'],'height':info['height'],'original_duration':round(info['duration'],2),
                         'clip_start':cut['start'],'clip_duration':cut['duration'],
                         'match_score':assessment['score'],'ai_note':assessment['reason'],
                         'overlay_warning':assessment['overlay_warning'],
                         'preview':str(jobfolder/'previews'/(task['id']+'.mp4')),
                         'original':str(jobfolder/'incoming'/(task['id']+'.source')),
                         'result_note':'Playable preview ready; manual scene, overlay, and reuse rights review REQUIRED.'})
        else:
            task.update({'status':'needs_source','result_note':
                         'No downloadable, licensed 1080p candidate passed current source/search checks. '
                         'Social platform metadata cannot be treated as a downloadable authorized source.',
                         'errors':errors[-4:]})
    except Exception as e:
        task.update({'status':'error','result_note':f'{type(e).__name__}: {str(e)[:240]}'})
    save_job(job)


def review(job:dict, task_index:int, approve:bool, notes:str='') -> None:
    task=job['tasks'][task_index]
    if task.get('status')!='review':
        raise ValueError('Only prepared previews can be approved or rejected.')
    source=Path(task['original']); preview=Path(task['preview'])
    if approve:
        if task.get('match_score') is None or int(task.get('match_score',0)) < relevance_threshold():
            raise ValueError(f'Cannot approve: visual relevance must be {relevance_threshold()}/100 or higher')
        if not preview.exists():raise ValueError('Preview file not present')
        out=(JOBS/job['job_id'])/'approved'/task['filename']
        shutil.copy2(preview,out)
        verify=video_info(out)
        if verify['duration']<1 or min(verify['width'],verify['height'])<1080:
            out.unlink(missing_ok=True)
            raise ValueError('Approved file verification failed')
        task.update({'status':'approved','approved_path':str(out),'review_note':notes})
    else:
        task.setdefault('rejected_sources',[])
        if task.get('source_url'):
            task['rejected_sources'].append(task['source_url'])
        task.update({'status':'rejected','review_note':notes})
    source.unlink(missing_ok=True)
    preview.unlink(missing_ok=True)
    if not approve:
        for candidate in task.get('social_candidates',[]):
            if candidate.get('source_url')==task.get('source_url'):
                candidate['status']='rejected'
    save_job(job)


def reset_rejected(job:dict, index:int):
    t=job['tasks'][index]
    if t['status'] not in ('rejected','needs_source','error'):return
    t.update({'status':'needs_source','result_note':'Queued to search again'})
    save_job(job)
