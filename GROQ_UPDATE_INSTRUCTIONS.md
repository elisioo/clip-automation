# Kinroot Clip Review — Groq update

This update adds Groq for text extraction from new client PDFs and for AI scene matching of up to 3 sampled video frames. Groq has a rate-limited free tier; commercial/video usage rights remain separate.

## Windows / Docker installation

1. Keep Docker Desktop running. **Back up** your existing `D:\kinroot-automation` directory and especially `clip-review\data` before updating.
2. Extract the contents of this update ZIP directly into `D:\kinroot-automation` and agree to replace the four matching files. The ZIP does **not** include your existing data folder or n8n compose file.
3. Open PowerShell:

```powershell
cd "D:\kinroot-automation"
Copy-Item .env.example .env
notepad .env
```

4. Get a Groq key from https://console.groq.com/keys and put it after `GROQ_API_KEY=` in `.env` (no quotation marks). Never share this file or screenshot the key.
5. Restart with updated configuration:

```powershell
docker compose -f compose.clip-review.yaml up -d --build --force-recreate
docker compose -f compose.clip-review.yaml ps
```

6. Open http://localhost:8501 again. The top notice should say **Groq AI is configured**. If the old warning remains, check that the file is named `.env` (not `.env.txt`) in `D:\kinroot-automation`, and rerun the commands.
7. Upload the client brief again; click **Create clip tasks from this brief**. Inspect all filenames, descriptions and required variants carefully. Then click **Search, analyze & trim** to search the *currently connected, rights-labeled Wikimedia Commons/Internet Archive sources*.

## Important

- **Does not add TikTok/Pinterest/Douyin video downloading or n8n integration.** Those remain future integrations requiring supported access to reusable video files.
- AI evaluates sampled images only, not full-frame continuous footage. It cannot guarantee match quality, watermark-free originals, licenses or commercial rights.
- Groq free usage has limits; large batches may be rate-limited. No API key is included.
- The application uses Groq's official OpenAI-compatible endpoint `https://api.groq.com/openai/v1`. To use OpenAI instead, clear GROQ_API_KEY and populate OPENAI_API_KEY in `.env`.
- Some scanned PDFs have no extractable text; OCR is not yet included. The app provides a clearer error if document text cannot be extracted.
- Do not publish the local review site to the internet without adding authentication, authorization and hardened hosting.
