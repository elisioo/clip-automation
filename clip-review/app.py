import io
import json
import os
import zipfile
import requests
import social_bridge as sb
from pathlib import Path

import streamlit as st
import pipeline as p

st.set_page_config(page_title='Kinroot • AI Clip Review',page_icon='🎬',layout='wide')
st.markdown('''<style>
.block-container {max-width:1180px;padding-top:2rem;padding-bottom:3rem}
[data-testid="stMetric"] {background:rgba(128,128,128,.08);padding:14px;border-radius:12px}
div[data-testid="stVerticalBlockBorderWrapper"] {border-radius:14px}
</style>''',unsafe_allow_html=True)

st.title('🎬 Kinroot • Clip Review')
st.caption('Upload a brief → n8n searches → 70+/100 visual match gate → 1080p trim → you approve or reject')

if not p.ai_available():
    st.warning('AI is OFF. Add a GROQ_API_KEY (free tier available) in your local .env file and restart the container. The included Kyle PDF works as a demo without AI; other briefs need a key.')
else:
    st.success(f'{p.ai_provider()} AI is configured: text model {p.ai_model()} · image model {p.ai_model(vision=True)}. Scene scores are suggestions, not proof of accuracy, rights, or authenticity.')

with st.expander('Important production requirements',expanded=False):
    st.write('This edition can invoke an **n8n social-discovery workflow** for Pinterest, TikTok, Douyin and YouTube, then receive metadata candidates back from n8n. Social-video downloads are NOT automatically authorized by search results. The optional legacy open-license sources remain separate.')
    st.write('Only 1080p-or-better original files are accepted. A 3-second central shot plus up to 1 second of handles on each side is used for the preview. No upscaling or watermark removal. **You must verify scene details, age/appearance, branding/overlays, authenticity, reuse permission, and attribution before approval.**')
    st.write('For explicitly verified open-license, direct media, the system can automatically acquire, visually score and trim the best scene. Social search URLs alone are never treated as authorized downloads. Other direct files can be processed after you verify rights. A clip is NOT placed in the playable review queue without a vision score of at least 70/100 and 1080p. Rejected previews and temporary source files are deleted. Scores are estimates, not guarantees.')

st.subheader('1 · Upload the client instructions')
upload=st.file_uploader('Choose a PDF, DOCX, or TXT client brief',type=['pdf','docx','txt'],help='Only the client brief is required. Do not upload or paste video links.')
auto_search=st.checkbox('Automatically start a small n8n discovery test when uploading a brief (uses provider credits)',value=True)
auto_count=st.number_input('Maximum clips to auto-search on upload',min_value=1,max_value=40,value=2,step=1,disabled=not auto_search)
if st.button('Create clip tasks from this brief',type='primary',disabled=upload is None):
    try:
        data=upload.getvalue()
        if len(data)>20*1024*1024: raise ValueError('Keep the brief below 20 MB.')
        jid=p.create_job(data, upload.name)
        st.session_state['job_id']=jid
        if auto_search:
            job_to_start=p.get_job(jid)
            url=os.environ.get('KINROOT_N8N_WEBHOOK','http://kinroot-n8n:5678/webhook/kinroot-source')
            sent=0
            for task_to_start in job_to_start['tasks'][:int(auto_count)]:
                try:
                    resp=requests.post(url,json={'job_id':jid,'clip_id':task_to_start['id']},timeout=12)
                    resp.raise_for_status()
                    sent+=1
                except Exception as ex:
                    st.warning('Auto-search could not reach n8n: '+str(ex)[:140]);break
            st.success(f'Brief parsed, {len(job_to_start["tasks"])} tasks created, {sent} searches triggered in n8n.')
        else:
            st.success('Brief parsed. Clip tasks created. Start social discovery in the next section.')
        st.rerun()
    except Exception as e:
        st.error(f'Could not read brief: {e}')

jobs=p.list_jobs()
if jobs:
    chosen=st.selectbox('Current sourcing job',options=jobs,index=jobs.index(st.session_state['job_id']) if st.session_state.get('job_id') in jobs else 0,
                         format_func=lambda j:f'{p.get_job(j).get("editor","Brief")} · {p.get_job(j).get("brief_filename","")} · {j}')
    st.session_state['job_id']=chosen
else:
    st.info('Upload your PDF to create the first sourcing job. No source video link is needed.')
    st.stop()

job=p.get_job(chosen)
# Guard older incomplete jobs created before the parser fix.
brief_files = [f for f in (p.JOBS / chosen).glob('brief.*') if f.is_file()]
if brief_files:
    brief_file = brief_files[0]
    try:
        original_text = p.read_document(brief_file.read_bytes(), brief_file.name)
        p.validate_task_count(original_text, job)
    except ValueError as exc:
        st.error(f'⚠️ This is an incomplete older job: {exc} Re-upload the client brief '
                 'using the updated parser, then select the new job before sourcing.')
        st.stop()
st.subheader('2 · Automatic sourcing')
st.caption(f'{job.get("editor","Unknown editor")} · {len(job["tasks"])} required files · Due {job.get("delivery_date","not stated")} · Visual match threshold: {p.relevance_threshold()}/100')
counts={status:sum(t['status']==status for t in job['tasks']) for status in ['needs_source','review','approved','rejected','error']}
a,b,c,d=st.columns(4)
a.metric('Required clips',len(job['tasks']))
b.metric('Playable for review',counts['review'])
c.metric('Approved',counts['approved'])
d.metric('Not sourced yet',counts['needs_source']+counts['error'])

st.markdown('### Social discovery through n8n')
st.caption('One click sends each selected requirement to your imported n8n workflow. n8n searches supported third-party APIs and sends candidate metadata back here. Actor searches can consume Apify credits.')
waiting=[t for t in job['tasks'] if t['status'] in ('needs_source','error','rejected')]
if waiting:
    max_batch=min(12,len(waiting))
    batch_size=st.number_input('Clip requirements to search in this batch', min_value=1,max_value=max_batch,value=min(2,max_batch),step=1)
    if st.button(f'🚀 Start n8n social search for {batch_size} clip(s)',type='primary'):
        endpoint=os.environ.get('KINROOT_N8N_WEBHOOK','http://kinroot-n8n:5678/webhook/kinroot-source')
        sent=0; errs=[]
        for t in waiting[:batch_size]:
            try:
                r=requests.post(endpoint,json={'job_id':chosen,'clip_id':t['id']},timeout=12)
                r.raise_for_status()
                sent+=1
            except Exception as exc:
                errs.append(f'{t["id"]}: {str(exc)[:150]}')
        if sent: st.success(f'Sent {sent} sourcing requests to n8n. Open n8n Executions to monitor, then click Refresh queue.')
        if errs: st.error('Could not start some searches: '+' | '.join(errs))
else:
    st.info('All clip tasks already have a review or approved status.')

with st.expander('What happens when n8n finds candidates?'):
    st.write('Candidate links, metadata relevance scores and available media previews appear in the Social candidate queue below. A social video is **not automatically licensed** for commercial reuse. Direct-file trimming is available only after you confirm source permission; otherwise use the original platform preview to evaluate relevance.')


with st.expander('Instructions extracted from client brief',expanded=False):
    st.write(job.get('rules',''))
    st.dataframe([{'Filename':t['filename'],'Must show':t['description'],'Search suggestion':t.get('query','')} for t in job['tasks']],use_container_width=True,hide_index=True)

pending=[i for i,t in enumerate(job['tasks']) if t['status'] in ('needs_source','error')]
runcol,refreshcol=st.columns([2,1])
with runcol:
    run=st.button(f'Optional legacy archive search for {len(pending)} clips',disabled=not pending)
with refreshcol:
    if st.button('Refresh queue'):
        st.rerun()
if run:
    bar=st.progress(0,text='Searching permitted public video collections…')
    log=st.empty()
    for n,i in enumerate(pending,1):
        p.process_one(job,i)
        status=job['tasks'][i]['status']
        log.caption(f'{n}/{len(pending)} — {job["tasks"][i]["filename"]}: {status}')
        bar.progress(n/len(pending),text=f'Processed {n} of {len(pending)} requirements')
    st.success('Search pass completed. Review the playable candidates below; unmatched clips are clearly marked.')
    st.rerun()

st.subheader('3 · Metadata shortlist (not yet visually verified)')
st.caption(f'Only candidates estimated at {p.relevance_threshold()}/100 or above using TEXT metadata appear here. Actual video-frame scoring is required before a playable clip reaches approval.')
shown=0
below_count=0
for i,t in enumerate(job['tasks']):
    candidates=t.get('social_candidates',[])
    if not candidates: continue
    shortlisted=[c for c in candidates if c.get('ai_score') is not None and int(c.get('ai_score',0))>=p.relevance_threshold() and c.get('status')!='rejected']
    below_count += len(candidates)-len(shortlisted)
    if not shortlisted: continue
    shown+=len(shortlisted)
    with st.expander(f'{t["filename"]} · {len(shortlisted)} metadata candidates at ≥{p.relevance_threshold()} · Must show: {t["description"][:100]}',expanded=i==0):
        for ci,c in enumerate(shortlisted[:10]):
            with st.container(border=True):
                st.write(f'**{ci+1}. {c["platform"]}** · text-only relevance: {c.get("ai_score","—")}/100 — NOT a visual match')
                if c.get('processing_note'): st.warning('Automatic processing note: '+c['processing_note'])
                st.write(c.get('title','') or '(No title)')
                if c.get('description'): st.caption(c['description'][:450])
                if c.get('ai_reason'): st.caption('AI metadata note: '+c['ai_reason'])
                if c.get('source_url'): st.link_button('View original source',c['source_url'])
                video_url=c.get('video_url','')
                if c.get('platform')=='YouTube' and c.get('source_url'):
                    st.video(c['source_url'])
                elif video_url and video_url.lower().split('?')[0].endswith('.mp4'):
                    st.video(video_url)
                elif video_url:
                    st.caption('External video stream was found, but browser playback depends on the provider/format.')
                st.warning('Text score only. Visual relevance, footage resolution and commercial reuse rights remain unverified until processing and your final review.')
                if video_url and t['status'] not in ('review','approved'):
                    confirmed=st.checkbox('I verified permission to obtain and use this video for the client ad.',key=f'rights_{chosen}_{t["id"]}_{ci}')
                    if st.button('✂️ Acquire & trim this authorized candidate',key=f'trim_{chosen}_{t["id"]}_{ci}',disabled=not confirmed):
                        try:
                            sb.prepare_preview(chosen,t['id'],c['source_url'],rights_confirmed=True)
                            st.success('Preview trimmed. Check the review queue below.')
                            st.rerun()
                        except Exception as ex:
                            st.error(f'Cannot prepare this candidate: {ex}')
                elif not video_url:
                    st.caption('This search result provides a page URL only; no supported direct media URL for FFmpeg.')
if not shown:
    st.info(f'No metadata candidates scored {p.relevance_threshold()}/100 or higher yet. This does not necessarily mean searches failed—check provider results and Groq connection.')
if below_count:
    st.caption(f'{below_count} lower-score, unscored or rejected candidate records are retained for diagnostics; they are not in the approval queue.')

st.subheader(f'4 · Visually qualified, trimmed clips (≥{p.relevance_threshold()}/100)')
review_indices=[i for i,t in enumerate(job['tasks']) if t['status']=='review']
if not review_indices:
    st.info('No playable previews are awaiting review yet. Run automatic sourcing first. If no matching, licensed 1080p video is found, the system will report Needs Source instead of inventing a result.')
for i in review_indices:
    t=job['tasks'][i]
    with st.container(border=True):
        st.markdown(f'#### {t["filename"]} · Review required')
        st.write('**Must show:** '+t['description'])
        left,right=st.columns([1.4,1])
        with left:
            v=Path(t.get('preview',''))
            if v.is_file(): st.video(v,format='video/mp4')
            else:st.error('The preview file is missing; do not approve.')
        with right:
            st.write(f'**Source:** {t.get("source","—")}')
            st.write(f'**Video title:** {t.get("source_title","—")}')
            st.write(f'**Resolution:** {t.get("width",0)} × {t.get("height",0)}')
            st.write(f'**Trim:** starts {t.get("clip_start",0)} s · duration {t.get("clip_duration",0)} s')
            if t.get('match_score') is None:
                st.error('No visual score: cannot approve')
            else:
                st.write(f'**Visual scene estimate:** {t["match_score"]}/100 (minimum {p.relevance_threshold()})')
            if t.get('ai_note'):st.caption(t['ai_note'])
            if t.get('overlay_warning'):st.warning(t['overlay_warning'])
            st.write(f'**License metadata:** {t.get("license","Unknown")}')
            if t.get('credit'):st.caption('Creator / credit: '+t['credit'][:200])
            if t.get('source_url'):st.link_button('View original source and verify rights',t['source_url'])
        note=st.text_input('Review notes / swap details',key=f'note_{chosen}_{t["id"]}',placeholder='e.g. exact scene matches; license checked; no overlays')
        cols=st.columns(2)
        if cols[0].button(f'✅ Approve {t["id"]}',key=f'yes_{chosen}_{t["id"]}',type='primary',use_container_width=True):
            try:p.review(job,i,True,note);st.rerun()
            except Exception as e:st.error(str(e))
        if cols[1].button(f'❌ Reject {t["id"]}',key=f'no_{chosen}_{t["id"]}',use_container_width=True):
            try:p.review(job,i,False,note);st.rerun()
            except Exception as e:st.error(str(e))

st.subheader('5 · Full task board')
status_icon={'approved':'✅','review':'▶️','needs_source':'🔍','rejected':'❌','error':'⚠️'}
st.dataframe([{'File':t['filename'],'Status':status_icon.get(t['status'],'')+' '+t['status'],
               'Requirement':t['description'],'Source':t.get('source','—'),
               'AI score':t.get('match_score','—'),
               'Notes':t.get('review_note') or t.get('result_note','')} for t in job['tasks']],
             use_container_width=True,hide_index=True)

rejected=[(i,t) for i,t in enumerate(job['tasks']) if t['status']=='rejected']
if rejected:
    with st.expander('Rejected clips — retry sourcing'):
        for i,t in rejected:
            if st.button('Reset '+t['filename']+' for a new search',key='retry_'+t['id']):
                p.reset_rejected(job,i)
                st.rerun()

with st.expander('Needs Source / troubleshooting'):
    for t in job['tasks']:
        if t['status'] in ('needs_source','error'):
            st.markdown(f'**{t["filename"]}** — {t.get("result_note", "Not searched yet")}')
            if t.get('errors'):
                st.caption('; '.join(t['errors'][-3:]))

approved=[t for t in job['tasks'] if t['status']=='approved']
if approved:
    st.subheader('6 · Export approved deliverables')
    st.caption('The ZIP contains only approved MP4s, plus a source/credit manifest. Check all usage conditions and final filenames before submitting.')
    buf=io.BytesIO()
    manifest={'editor':job.get('editor'),'delivery_date':job.get('delivery_date'),
              'approved':[{k:t.get(k) for k in ['filename','description','source','source_url','license','credit','review_note']} for t in approved],
              'unfilled':[t['filename'] for t in job['tasks'] if t['status']!='approved']}
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for t in approved:
            fp=Path(t['approved_path'])
            if fp.is_file():z.write(fp,arcname=t['filename'])
        z.writestr('source_and_review_manifest.json',json.dumps(manifest,indent=2,ensure_ascii=False))
    st.download_button('⬇️ Download approved clips ZIP',data=buf.getvalue(),file_name=f'Kinroot_{chosen}_approved.zip',mime='application/zip')

st.divider()
st.caption('Kinroot v4 · 70+ score is an AI estimate from sampled video frames, not proof of accuracy, authenticity, or licensing. No platform restriction bypass. No clip is automatically approved.')
