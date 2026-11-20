# Kinroot v4 — 70+/100 Visual Match and Auto Review Queue

This package upgrades the **existing Kinroot v3** Streamlit review website and its local FastAPI bridge. The **n8n v3 production webhook path stays the same**: `POST /webhook/kinroot-source`.

## What is now implemented

- PDF input with complete ID + `_alt` validation; John = **12** files, Kyle = **22**.
- On PDF upload, default to a **two-clip search pilot** (can change the count in the form); uses your already-published n8n discovery webhook.
- Pinterest/TikTok/Douyin/YouTube candidate callbacks to the website, with Groq text-based **metadata shortlisting** (not visual proof).
- For sources with an authorized, directly accessible video file and clearly documented **open-license metadata** from a supported trusted media source, automatically start video acquisition in a background task.
- Use Groq vision to analyze up to 4 different 3-frame temporal windows, choose the highest-scoring window and require **at least 70/100 visual match**. Below threshold or no vision score: no review-ready export.
- Require original video **1080p or better**; trim a **3-second scene plus 1-second handles on each side** (5-second output). Avoid upscaling. Your client's rules still determine acceptance.
- A qualifying video appears in the on-site playable approval queue. You **Approve** or **Reject**. Rejection deletes the temporary original and preview. Approval verifies the output and preserves a named MP4 in approved exports.
- Keep unqualified or metadata-only candidates outside the prepared-video approval queue; retain diagnostic records.

## What is *not* solved by this update

- **Automatic downloading of arbitrary TikTok, Pinterest, Douyin, Facebook or YouTube videos is NOT implemented.** Social search APIs returning public page links, thumbnails or media URLs do not establish permission for commercial use. No scraping bypass, robots bypass, third-party download-page automation, or watermark removal is included.
- **Facebook public keyword search** is not connected: the Facebook Reels Apify Actor you found requires supplied Page URLs.
- A `70/100` score is a **model estimate on sampled frames, not measured probability** that footage is correct. You must review the motion, story, demographics, visual overlays and authorization for commercial usage.
- There may be **zero ready-to-review videos** if connected sources cannot supply permitted full-resolution files meeting the exact requested scene. This is not a defect in PDF extraction.
- Third-party searches can consume Apify credits or YouTube quota. The default 2-clip pilot avoids unexpectedly launching the whole 12- or 22-clip batch.

## Upgrade an existing v3 installation (recommended)

1. Back up `D:\kinroot-automation\clip-review\data` and your `.env` file.
2. Extract `Kinroot_v4_Update_Existing.zip` into `D:\kinroot-automation`, keeping the folder names. Replace **only** the indicated Python files and `compose.clip-review.yaml`. **Do not replace** your current `compose.yaml`, `.env`, or `clip-review/data`.
3. In PowerShell:

   ```powershell
   cd 'D:\kinroot-automation'
   docker compose -f compose.clip-review.yaml up -d --build --force-recreate
   docker compose -f compose.clip-review.yaml ps
   ```

4. Check that `GROQ_API_KEY` is set privately in your existing `.env`. Default vision model is `qwen/qwen3.8-27b`, which accepts up to three images per request. Optional v4 `.env` entries:

   ```dotenv
   KINROOT_MATCH_THRESHOLD=70
   KINROOT_MAX_VISION_WINDOWS=4
   ```

5. Open `http://localhost:8501` and choose John's existing job (12 clip requirements). Run a small n8n discovery test for `S10-A01` and click **Refresh queue**.
6. **No new n8n import is required** if v3 is published and working. If you prefer an annotated v4 canvas, import `Kinroot_FULL_n8n_v4.json` separately, reconnect the Apify Bearer Auth/YouTube Query Auth credentials, and publish **only the chosen workflow** for the `kinroot-source` production webhook. Do not leave two published workflows fighting for the same production webhook.

## Score and file handling

- **Text relevance score:** estimated from the candidate's title/description. `>=70` shows it in the *metadata shortlist*, not the playable review queue.
- **Visual relevance score:** computed from actual sampled frames of a permitted source video. `>=70`, 1080p, and five-second duration -> *playable review queue*. No score or `<70` -> **not ready**.
- **Approve:** output verified, exact clip filename retained in `approved/`, source and temporary preview removed.
- **Reject:** original and preview removed, source URL retained in the rejected history, so you don't unintentionally approve the same rejected source again.
- The output does not automatically clean watermarks, logos, or captions; users must still review these constraints.

## Troubleshooting

- **Candidates but no playable previews:** most likely metadata-only social links, no verified reuse permission, below-1080p direct media, or visual score below 70.
- **No candidates:** check n8n **Executions**, API credentials, and Apify Actor run logs; your website can still be working correctly.
- **Web 404:** publish the n8n workflow and confirm `POST /webhook/kinroot-source`.
- **Vision unavailable:** check `GROQ_API_KEY`, model availability, and free-tier limits. No-vision-score clips are never treated as 70+.
- **Old v3 previews scored 60-69:** they cannot be approved under this version; source them again.

## Validation performed

Offline tests were run for both PDFs' ID/alternative counts, n8n workflow connectivity shape, credential-free JSON, candidate/license gating, automatic file processing with mocked network and AI scores (69 rejects, 73 prepares), FFmpeg 1080p/5-second output and delete-on-reject. Live Apify, YouTube, Groq, and Docker-on-your-Windows-PC were **not** executed from this environment.
