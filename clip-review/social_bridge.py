"""Store social-discovery candidates and process authorized original video files.

No platform-access bypass, watermark removal, or assumptions about licensing.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import threading
import time
import requests
from pathlib import Path
from urllib.parse import urlparse

import pipeline as p

_LOCK = threading.RLock()
MAX_CANDIDATES = 25
VIDEO_HOST_SUFFIXES = ('.pinimg.com', '.pinterest.com', '.apifyusercontent.com',
                       '.apify.com', '.archive.org', '.wikimedia.org')
# Social APIs discover videos; they do not establish commercial-use permission.
TRUSTED_RIGHTS_HOSTS = {'Wikimedia Commons': ('commons.wikimedia.org',),
                        'Internet Archive': ('archive.org',)}


def _safe_https_url(url: str) -> bool:
    """Only HTTPS public hosts; no local/private targets or userinfo."""
    if not isinstance(url, str) or len(url) > 2048:
        return False
    u = urlparse(url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None, 443):
        return False
    host = u.hostname.lower().rstrip('.')
    if host in ('localhost', 'host.docker.internal') or host.endswith(('.local', '.internal')):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return True


def _media_host_allowed(url: str) -> bool:
    if not _safe_https_url(url):
        return False
    host = urlparse(url).hostname.lower().rstrip('.')
    return any(host == x.lstrip('.') or host.endswith(x) for x in VIDEO_HOST_SUFFIXES)


def _score(scene: str, title: str, description: str) -> int:
    return round(100 * p.term_overlap(scene, title + ' ' + description))


def _rank_with_ai(scene: str, title: str, description: str) -> tuple[int | None, str]:
    if not p.ai_available() or os.environ.get('KINROOT_METADATA_AI', 'true').lower() != 'true':
        return None, 'Metadata keyword score only; visual match unverified.'
    prompt = ("Only judge the TEXT metadata, not unseen video. Required shot: " + scene +
              "\nCandidate title: " + title[:450] + "\nDescription: " + description[:1000] +
              "\nOutput JSON {\"score\":0..100,\"reason\":\"short explanation\"}. "
              "Do not assume that swollen ankles equals a thumb pressing into an ankle. "
              "Never assert ownership, license, video quality, or a confirmed visual match.")
    try:
        r = p.ai_client().chat.completions.create(model=p.ai_model(),
            response_format={'type':'json_object'},
            messages=[{'role':'user','content':prompt}],temperature=0)
        data = json.loads(r.choices[0].message.content)
        return max(0,min(100,int(data.get('score',0)))),str(data.get('reason',''))[:250]
    except Exception as ex:
        return None, 'AI metadata check unavailable: ' + str(ex)[:130]


def _task(job: dict, clip_id: str):
    clip_id = clip_id.removesuffix('.mp4')
    for index, task in enumerate(job['tasks']):
        if task['id'] == clip_id:
            return index, task
    raise ValueError('Unknown clip ID: ' + clip_id)


def accept_candidate(job_id: str, item: dict) -> dict:
    """Receive an n8n discovery record; do not interpret metadata as licensing."""
    with _LOCK:
        job = p.get_job(job_id)
        idx, task = _task(job, str(item.get('clip_id','')))
        platform = str(item.get('platform','Unknown'))[:30]
        status = str(item.get('status','candidate_found'))
        if status in ('source_error', 'no_results'):
            task.setdefault('search_diagnostics',[]).append({
                'platform': platform, 'status':status,
                'error':str(item.get('error',''))[:300]})
            task['search_diagnostics'] = task['search_diagnostics'][-30:]
            p.save_job(job)
            return {'ok':True, 'recorded':status}
        url = str(item.get('source_url') or '').strip()
        if not _safe_https_url(url):
            return {'ok':False, 'recorded':'invalid_source_url'}
        existing = task.setdefault('social_candidates',[])
        if url in task.get('rejected_sources',[]):
            return {'ok':True,'recorded':'previously_rejected'}
        if any(c.get('source_url') == url for c in existing):
            return {'ok':True,'recorded':'duplicate'}
        if len(existing) >= MAX_CANDIDATES:
            return {'ok':True,'recorded':'cap_reached'}
        title = str(item.get('title') or '')[:600]
        desc = str(item.get('description') or '')[:1700]
        score = _score(task['description'],title,desc)
        # Avoid many text AI requests per task on free tiers; rank first 5 only.
        ai_score, explanation = _rank_with_ai(task['description'],title,desc) if len(existing)<5 else (None,'Further AI checks skipped to conserve quota.')
        media_url = str(item.get('video_url') or item.get('media_url') or '')
        if not _media_host_allowed(media_url):
            media_url = ''
        licence = str(item.get('license') or '')[:300]
        rights_verified = (platform in TRUSTED_RIGHTS_HOSTS
                           and urlparse(url).hostname in TRUSTED_RIGHTS_HOSTS.get(platform,())
                           and p.allowed_license(licence) and bool(media_url))
        new = {
            'platform':platform,'source_url':url,'video_url':media_url,
            'license':licence,
            'auto_acquisition_allowed':rights_verified,
            'title':title,'description':desc,'score':ai_score if ai_score is not None else score,
            'ai_score':ai_score,'ai_reason':explanation,'rights_status':'licensed_source' if rights_verified else 'unverified',
            'metadata_qualified': ai_score is not None and ai_score >= p.relevance_threshold(),
            'status':'candidate_found','discovered_at':time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        existing.append(new)
        existing.sort(key=lambda c:c.get('score',0),reverse=True)
        task['result_note']=f'{len(existing)} discovery candidates; rights and scene unverified.'
        p.save_job(job)
        return {'ok':True, 'recorded':'candidate', 'candidates':len(existing),
                'auto_eligible': bool(rights_verified),
                'source_url':url}


def prepare_preview(job_id: str, clip_id: str, source_url: str, rights_confirmed: bool=False, auto_verified: bool=False) -> dict:
    """Download and trim only after a human has verified permission for this candidate.

    An HTTPS direct media URL is needed; a YouTube watch page is not a media file.
    """
    if not rights_confirmed and not auto_verified:
        raise ValueError('Confirm you have permission to obtain and reuse this source before processing.')
    with _LOCK:
        job = p.get_job(job_id)
        idx, task = _task(job,clip_id)
        matches = [c for c in task.get('social_candidates',[]) if c.get('source_url') == source_url]
        if not matches: raise ValueError('Unknown source candidate')
        candidate = matches[0]
        if auto_verified and not candidate.get('auto_acquisition_allowed'):
            raise ValueError('Automatic acquisition blocked: source licensing is not verified.')
        if task['status'] in ('review','approved'):
            return {'ok':True, 'status':task['status'],'filename':task['filename']}
        if candidate.get('status')=='rejected':
            raise ValueError('Previously rejected candidate cannot be reprocessed.')
        media_url = candidate.get('video_url','')
        extension = urlparse(media_url).path.lower().rsplit('.',1)[-1]
        if extension not in ('mp4','webm','mov','ogv'):
            raise ValueError('The provider supplied a stream or webpage, not a supported direct video file. This connector cannot safely retrieve it yet.')
        if not _media_host_allowed(media_url):
            raise ValueError('This provider did not supply a supported HTTPS video file. A source page cannot be downloaded as an MP4 automatically.')
        folder = p.JOBS / job_id
        original = folder/'incoming'/(task['id']+'.source')
        preview = folder/'previews'/(task['id']+'.mp4')
        original.unlink(missing_ok=True)
        preview.unlink(missing_ok=True)
        try:
            # Do not follow redirects to unverified hosts or load internal endpoints.
            total=0
            with p.SESSION.get(media_url,stream=True,timeout=(15,55),allow_redirects=False) as response:
                response.raise_for_status()
                if response.status_code >= 300:
                    raise ValueError('Video host redirected to another URL; recheck source access and authorization.')
                if int(response.headers.get('Content-Length') or 0) > p.MAX_BYTES:
                    raise ValueError('Source exceeds 80 MB limit')
                with original.open('wb') as handle:
                    for chunk in response.iter_content(chunk_size=512*1024):
                        if chunk:
                            total += len(chunk)
                            if total > p.MAX_BYTES:
                                raise ValueError('Source exceeds 80 MB limit')
                            handle.write(chunk)
            if total < 10_000:
                raise ValueError('Downloaded source is too small for a real video')
            info=p.video_info(original)
            if min(info['width'],info['height'])<1080 or info['duration']<5:
                raise ValueError('Source is below 1080p or shorter than 5 seconds including handles')
            vision = p.assess_video(original,task['description']+'; '+str(job.get('rules','')),info['duration'])
            if vision['score'] is None or vision['score'] < p.relevance_threshold():
                raise ValueError(f'Vision score {vision["score"]} below {p.relevance_threshold()}/100; no preview prepared')
            cut=p.trim_preview(original,preview,info,vision['time'])
            task.update({'status':'review','source':candidate['platform'],
                'source_url':candidate['source_url'],'license':candidate.get('license') or ('Manually checked by operator' if rights_confirmed else 'Unverified'),
                'credit':'See original post','source_title':candidate['title'],
                'width':info['width'],'height':info['height'],
                'clip_start':cut['start'],'clip_duration':cut['duration'],
                'match_score':vision['score'],'ai_note':vision['reason'],
                'overlay_warning':vision['overlay_warning'],
                'preview':str(preview),'original':str(original),
                'result_note':'Trim ready; visual and usage-rights review REQUIRED before final approval.'})
            candidate['rights_status']='user_confirmed' if rights_confirmed else 'licensed_source'
            candidate['status']='prepared_for_review'
            p.save_job(job)
            return {'ok':True,'status':'review','filename':task['filename']}
        except Exception:
            original.unlink(missing_ok=True)
            preview.unlink(missing_ok=True)
            raise



def auto_prepare_verified_candidate(job_id: str, clip_id: str, source_url: str) -> dict:
    """Background work for explicitly licensed direct-media sources only.

    Social video links stay in the metadata queue until valid acquisition and
    commercial use permissions are established. Failed/low-score temp files
    are removed by prepare_preview; the failure is recorded for diagnosis.
    """
    try:
        return prepare_preview(job_id, clip_id, source_url, auto_verified=True)
    except Exception as ex:
        with _LOCK:
            job=p.get_job(job_id)
            _,task=_task(job,clip_id)
            for cand in task.get('social_candidates',[]):
                if cand.get('source_url') == source_url:
                    cand['status']='not_qualified'
                    cand['processing_note']=str(ex)[:330]
            task.setdefault('search_diagnostics',[]).append({
                'platform':'Auto visual processing',
                'status':'no_qualifying_trim','error':str(ex)[:330]})
            task['search_diagnostics']=task['search_diagnostics'][-30:]
            p.save_job(job)
        return {'ok':False, 'reason':str(ex)[:330]}


def get_task_for_api(job_id: str, clip_id: str) -> dict:
    job=p.get_job(job_id)
    _,task=_task(job,clip_id)
    # Build search queries from PDF and scene; avoid using search suggestion as source of truth.
    scene = task['description']
    q = task.get('query',scene).replace('TikTok ', '').strip(" ' \t\r\n")
    phrase = scene if len(q)<4 else (scene + ' ' + q[:80])
    cn={'swollen ankles':'脚踝 肿胀', 'thumb':'手指 按压 脚踝 凹陷',
        'shoes':'老人 穿鞋 肿脚', 'fibrin':'纤维蛋白 动画',
        'natto':'纳豆 特写', 'mother':'母亲 女儿 家庭',
        'recliner':'老人 躺椅 抬腿'}
    cphrase=' '.join(v for k,v in cn.items() if k in scene.lower()) or '中老年 生活 真实视频'
    return {'job_id':job_id,'clip_id':task['id'], 'filename':task['filename'],
            'scene':scene,'search_query':phrase[:180],
            'tiktok_query':phrase[:150], 'pinterest_query':phrase[:150],
            'youtube_query':phrase[:150], 'douyin_query':cphrase,
            'max_results':3}
