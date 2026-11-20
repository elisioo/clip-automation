# Kinroot v3 — Full n8n Social Discovery + Existing Review Website

This package upgrades the local Windows Docker project that you already started at `D:\kinroot-automation`.

It **does not** replace or edit your original `compose.yaml`, your n8n data volume, your saved owner account, your `.env` secrets, or your existing `clip-review/data/jobs` directory. Make a backup before replacing code.

## What works in this package

1. **Upload a client PDF/DOCX/TXT** in the Streamlit site. The existing parser creates exact clip IDs and `_alt` variants; it verifies stated counts (John: 8 IDs + 4 alt = 12, Kyle: 14 IDs + 8 alt = 22).
2. **Optional one-action auto-start:** tick *Automatically start n8n discovery* before you create the job, choose a maximum number of clip searches (2 by default), and new job requirements are sent directly to n8n's production webhook. The new job is stored before sourcing begins.
3. **n8n dynamically searches each selected clip**, rather than being hardcoded to `S10-A01`: Pinterest, TikTok, Douyin using Apify Actor APIs, and YouTube using the official Data API. Each source runs in its own branch, so one failing source need not stop the others.
4. Normalized result metadata is returned to the site's **candidate queue**, with Groq metadata relevance checks (if configured), original links, and available direct video URL/YouTube playback.
5. A **verified direct video file** (currently HTTPS MP4, MOV, WEBM, OGV on a known video host) can be downloaded *after you explicitly confirm you have the permission to obtain and commercially reuse that file*. The existing FFmpeg + Groq sparse-frame analysis checks resolution, suggests a scene, trims a ~5s output (3s central + up to 1s at each side), and shows the preview for final **Approve / Reject**. Rejected temporary clips are removed. The original file is removed after final decision.
6. Approved clips export with exact filenames and a source/review manifest.

## What is NOT fully automatic yet

- Arbitrary TikTok, Douyin, Facebook, YouTube and Pinterest downloads: search result metadata is NOT a license or a direct legal MP4. TikTok and Douyin branches will generally return source-page links, not authorized direct files. YouTube's official API provides discovery only. Pinterest may provide a video URL, often an HLS `.m3u8` stream, which this safe MVP will **not** download. Direct-file processing can occur only with rights confirmation.
- Facebook keyword search: the Facebook Reels Actor previously selected requires known Facebook Page URLs. There is **no working universal Facebook keyword-search branch** in this package. Add it only after verifying a suitable Actor and its input/output schema; don't pay for an unrelated actor.
- Groq scores are hypotheses, not proof of exact scene match, age, copyright, lack of captions, or 1080p. `ffprobe` measures actual resolution after a real file is available.
- No automatic watermark removal or platform restriction bypass.
- Live social API calls were **not** exercised in this test environment. Actor availability and cost must be verified with your Apify account. Apify's synchronous endpoints may time out and Actors can have separate charges. YouTube quota also applies.

## Before installing

Back up:

```
D:\kinroot-automation\clip-review\data
D:\kinroot-automation\compose.yaml
D:\kinroot-automation\.env
```

Close active sourcing operations and ensure no uploads are in progress. Do not delete your n8n Docker volume or use `docker compose down -v`.

## 1 — Copy the files

Unzip the files into `D:\kinroot-automation` **(not an extra nested folder)**. Windows should display:

```
D:\kinroot-automation\
    compose.yaml                       <-- original n8n setup, unchanged
    compose.clip-review.yaml           <-- updated website + new internal API service
    .env                               <-- your existing secrets, unchanged
    clip-review\app.py
    clip-review\api.py
    clip-review\social_bridge.py
    clip-review\pipeline.py
    clip-review\data\jobs\...          <-- existing jobs preserved
    Kinroot_FULL_n8n_v3.json            <-- import into n8n
    START_HERE.md
```

The ZIP contains no `.env` file and no existing saved job JSON files.

## 2 — Start n8n and the website

In PowerShell:

```powershell
cd "D:\kinroot-automation"
docker compose -f compose.yaml up -d
docker compose -f compose.clip-review.yaml up -d --build
docker compose -f compose.clip-review.yaml ps
```

Open:
- n8n: `http://localhost:5678`
- Kinroot Review: `http://localhost:8501`

Two new/rebuilt containers are expected: `kinroot-review` (Streamlit) and `kinroot-api` (internal FastAPI bridge). The `kinroot-api` port is **not published on Windows**, and both services should be on the same Compose project's default Docker network as n8n. The internal service URL used in n8n is `http://kinroot-api:8502`.

## 3 — Import full n8n workflow

In n8n:
1. Workflows → New → top-right menu `...` → **Import from File**.
2. Choose `Kinroot_FULL_n8n_v3.json`.
3. Name: **Kinroot — PDF to Social Candidates to Review Site v3**.
4. In **Search Pinterest / Search TikTok / Search Douyin**, choose **Authentication → Generic Credential Type → Bearer Auth**. Reuse the **same** Apify Bearer Auth credential (raw token, without typing `Bearer` again).
5. In **Search YouTube**, choose **Generic Credential Type → Query Auth** and your Google API credential where the name is `key`.
6. Save, then **Publish** the workflow (the production webhook needs to be active). The webhook's production path must be `/webhook/kinroot-source`.
7. Do not add a second webhook with the same path to another published workflow.

**No API tokens are in the JSON file.** The Groq key stays in your existing local `.env`; it is used by the website's parsing, relevance ranking and frame analysis, not by an n8n AI Agent node. This is intentional: deterministic API/FFmpeg nodes should handle external actions, while AI recommends matches.

## 4 — Start ONE test clip

In the Kinroot Review site, select the existing John job (12 required files). Under **Social discovery through n8n**, set **Clip requirements to search in this batch = 1**. Click **Start n8n social search for 1 clip**.

Check:
1. n8n → workflow → **Executions**. The workflow should receive `job_id` and `clip_id` from the website, fetch that clip's actual scene description, and invoke the search sources.
2. n8n → Pinterest and YouTube search branches should return records if your credentials/quota allow.
3. Website → **Refresh queue** → **Candidate discovery queue**. You should see source URLs and relevance notes.
4. If a direct authorized media file is available, confirm usage rights in the candidate card, click **Acquire & trim this authorized candidate**, inspect the trimmed preview, and finally Approve or Reject.

If you want the website to **start searches automatically when uploading a NEW brief**, tick the auto-search checkbox on the upload form. Choose a small batch first to control third-party costs.

## 5 — Costs and permissions

- Apify free credits are limited; Actor prices differ. Running 12 clips through 3 Apify actors can use credits or fail with a plan restriction. Start with 1.
- YouTube Data API has quota; the official API **does not provide an arbitrary MP4 download**.
- The client brief requires 1080p, age/scene fit, no logos/captions/watermarks and accurate filenames. Every final approval must verify these.
- Public social posts and scraped media URLs do not automatically confer commercial reuse permission. The package stores social candidates, but does not silently download them as authorized deliverables.
- `.env` contains keys. Never send it or commit it publicly.

## Troubleshooting

**Website says it cannot contact n8n:** `docker compose -f compose.yaml ps` and make sure the v3 workflow is **published**. The container DNS name must be `kinroot-n8n` (from your original setup).

**n8n Get clip task fails:** `docker compose -f compose.clip-review.yaml ps`. The `kinroot-api` service should be `Up`. From n8n's network, the hostname is `kinroot-api:8502`. If your Compose project name changed, put all services onto the same Docker network.

**Apify 402 / actor is paid:** Check API token authentication and the Actor's pricing. Disable that branch if it requires a payment you don't want. Don't send your key in screenshots.

**Pinterest gives 0 items:** Try broader search terms; inspect Apify Actor Runs and the dataset. The workflow records empty-result diagnostics instead of fabricating matches.

**YouTube returns a watch URL but no downloadable video:** Expected. Official YouTube Data API is discovery, not a download API.

**`m3u8` URL cannot be trimmed:** Expected for this MVP; HLS playlists may refer to unknown segment hosts and aren't treated as a safe direct original file.

**Metadata AI fails:** The website falls back to a keyword relevance score; check Groq free-tier rate limits/model availability. AI is optional for discovery but helpful for ranking and frame analysis.

**n8n node errors:** In the specific node, inspect the Output error and reselect its credential. Some community Apify Actor schemas can change. Search adapters need adjustment to their actual results.

## Validation performed

- Python syntax checks passed for `app.py`, `pipeline.py`, `api.py`, `social_bridge.py`.
- Offline tests passed: dynamic n8n graph integrity, candidate ingestion/duplicate suppression, blocked local URLs, John's 12-file extraction, Kyle's 22-file extraction, FFmpeg 1080p trim and approve/reject cleanup.
- Live Docker/n8n/Apify/YouTube/Groq interoperability **has not been tested** in this environment. Treat the first one-clip run as an integration test.
