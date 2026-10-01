# Queue and Spaces rollout — 19 September 2026

Production API: https://aimarker-bk.seamo-official.org  
Frontend: https://aimarker.seamo-official.org  
Integrator guide: https://aimarker.seamo-official.org/integration/

## Deployed behavior

- Uploads stream to persistent local disk before a submission/job is acknowledged.
  The database is authoritative; Redis transports job IDs. Idempotency receipts
  reconcile retries, including uncertain upload responses.
- One processing worker executes ten-page batches, yielding between batches.
  A separate archive worker handles private object transfers and cleanup.
  Database leases, filesystem locks and committed extraction/marking checkpoints
  protect against duplicate deliveries and interrupted workers.
- Maximum PDF size: 3,221,225,472 bytes. Two concurrent uploads, a 20 GiB disk
  reserve, shared upload/workspace reservations, 10,000 pages and 25 million
  rendered pixels per page bound admission and processing.
- The heavy worker has a 2.5-CPU / 4-GiB budget. API, dispatcher, Redis and
  archival use separate budgets. Gemini calls share Redis-backed pacing.
- Original PDFs, every converted page, diagram crops and available result
  snapshots archive privately under the `aimarker/` Spaces namespace. Multipart
  transfers resume after interruption; complete objects receive SHA-256 readback.
- After verification, new submissions retain a 24-hour local cache. Restored
  cache files schedule cleanup again. **Submission IDs through 24 are pinned
  locally**, preserving every pre-rollout submission. No historical backfill or
  deletion runs automatically. Database results remain local and authoritative.
- Candidate page lookup uses the existing paper code and the candidate number
  as a string. Repeated matches return 409 with disambiguating submission/page IDs.
- Explicit deletion is a tombstoned job covering local/remote artifacts and
  orphan multipart uploads. IDs are not reused after deletion. API and CLI
  documentation, OpenAPI and the downloadable client reflect the new contracts.

## Verification evidence

| Check | Outcome |
| --- | --- |
| Regression suite | 143 tests passed locally; 69 queue/auth/marking/client tests also passed in the final production image |
| Verified cache cleanup | A synthetic fixture aged past the cache interval lost only its verified local copies; its image still streamed from Spaces and restoring its source scheduled another cleanup |
| Orphan multipart recovery | Explicit fixture deletion also aborted an uncommitted multipart upload in that fixture’s namespace |
| Existing data preservation | 2,884 existing DB rows and 468 existing files matched their pre-deployment hashes |
| Exact 3 GiB HTTPS multipart upload | Accepted in 69.5 seconds; stored size and SHA-256 matched; oversized request rejected with 413 |
| Large-upload client memory | Approximately 104 MiB peak RSS for the test sender; no whole-file buffering |
| Real queue and private archive | Multi-page synthetic PDFs completed through Celery/Redis and produced verified private artifacts; anonymous reads returned 403 |
| Crash/restart recovery | Worker killed during the second batch of a 31-page submission, Redis restarted; all 31 candidates/pages eventually committed exactly once |
| Remote image fallback | PNG served with matching checksum while both local PNG and original PDF were unavailable |
| Synthetic cleanup | Explicit queued deletion removed only rollout fixtures and their private object namespaces |
| Frontend | Valid/invalid authentication, session reload, live 3 GiB limit and embedded integration guide checked in Chromium |
| Published documentation | Guide, OpenAPI, client and ZIP matched generated files; OpenAPI matched the live backend |

The exact-size upload is a transport test using a padded synthetic PDF; it does
not measure OCR throughput for an arbitrary 3 GiB scan.

## Representative 100-page run

The original file retained for submission 10 was uploaded as **submission 29**:
https://aimarker.seamo-official.org/track/29

- Extraction reached 100 pages at approximately 212 seconds.
- All 100 candidates finished marking at approximately 239 seconds.
- Verified archival finished at approximately 257 seconds (4 minutes 17 seconds).
- Marking run 19 completed, and `/submission/29/marked-json` returned HTTP 200.
- All original page numbers 1–100 were present exactly once; the original source
  file was unchanged.
- During a 12-sample processing window, public `/health` p95 was 28 ms and max
  was 29 ms. The worker's sampled peak CPU was 208% (about 2.08 cores), and its
  last sampled memory usage was 1.48 GiB. These are observations from one run,
  not a production latency or throughput guarantee.

Compared with the previous run of the same file, all 100 template selections
and all MCQ answers matched. Twelve of 2,500 answers differed, all in free
response questions 22–25; one candidate-number extraction also differed.
This comparison is not a ground-truth accuracy evaluation. Existing answers,
marks and identity values were not overwritten.

## Operations and recovery

The original backup and preservation baseline are retained on the server in
`/home/auto-deploy/queue-release-20260919-164652/`. Subsequent image/configuration
release directories are retained under `/home/auto-deploy/queue-*20260919*`.
Secrets remain in protected environment files and are excluded from Git,
Docker build contexts, public docs and frontend bundles.

See `DEPLOY.md` for service controls, limits, migrations, cache policy and rollback.
Keep SQLite on one host; adding processing hosts requires a database and shared
staging design. Spaces archival is not a database backup.
