"""Internal Docker-only bridge between the Kinroot website and n8n."""
from __future__ import annotations

import re
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
import pipeline as p
import social_bridge as s

app = FastAPI(title='Kinroot local n8n bridge',docs_url=None,redoc_url=None)
ID_PATTERN=re.compile(r'^[0-9a-f]{12}$')
CLIP_PATTERN=re.compile(r'^[A-Za-z0-9_-]{2,90}(?:\.mp4)?$')

class Discovery(BaseModel):
    clip_id: str
    platform: str
    source_url: str = ''
    video_url: str = ''
    title: str = ''
    description: str = ''
    status: str = 'candidate_found'
    error: str = ''
    license: str = ''

@app.get('/health')
def health(): return {'status':'ok'}

@app.get('/api/jobs/{job_id}/tasks/{clip_id}')
def get_task(job_id:str,clip_id:str):
    if not ID_PATTERN.fullmatch(job_id) or not CLIP_PATTERN.fullmatch(clip_id):
        raise HTTPException(400,'Invalid identifier')
    try: return s.get_task_for_api(job_id,clip_id)
    except (ValueError, FileNotFoundError) as ex: raise HTTPException(404,str(ex))

@app.post('/api/jobs/{job_id}/candidates')
def receive_candidate(job_id:str, candidate:Discovery, background_tasks:BackgroundTasks):
    if not ID_PATTERN.fullmatch(job_id): raise HTTPException(400,'Invalid identifier')
    if not CLIP_PATTERN.fullmatch(candidate.clip_id): raise HTTPException(400,'Invalid clip')
    try:
        result=s.accept_candidate(job_id,candidate.model_dump())
        if result.get('auto_eligible'):
            background_tasks.add_task(s.auto_prepare_verified_candidate,job_id,candidate.clip_id,result['source_url'])
        return result
    except (ValueError, FileNotFoundError) as ex: raise HTTPException(400,str(ex))
