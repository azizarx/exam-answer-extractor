# Layout OCR slowdown on the 4-vCPU production host

The 100-page submission was still classifying layouts after roughly 17 minutes.
Eight concurrent Tesseract processes used 391.78% CPU (four cores) with a load
average of 19.66. Each process also created an OpenMP thread team; page-level
parallelism multiplied that native parallelism. Memory use was about 1.5 GiB
of 7.5 GiB, and the configured Gemini limit was already 60 RPM.

A replay of the same rendered pages isolated the OCR stage, with Gemini
fallback disabled and unchanged 300-DPI images and classification rules:

| Configuration | Pages | OCR time | Result |
| --- | ---: | ---: | --- |
| Original, eight page workers | 8 | 93.05 s | Three OCR bands hit the benchmark's 45-second timeout |
| Only `OMP_THREAD_LIMIT=1` changed | 8 | 1.70 s | Same eight template IDs, no timeouts |
| Single-thread OCR, eight page workers | 100 | 21.50 s | Reference for full-PDF comparison |
| Final three-CPU cap, three OCR slots, single-thread OpenCV | 100 | 23.31 s | All 100 template IDs and warnings match the reference |

The final benchmark sampled 293.22% CPU, within the three-core container
budget and leaving one core of host capacity available. These are layout-stage
measurements, not full extraction/marking timings; Gemini latency and quota
still contribute to total processing time.

The fix sets one native thread per OCR process, limits OCR concurrency across
submissions, bounds each OCR band to 15 seconds, and stops waiting/queued work
when a submission is cancelled. The API and tracking UI support persistent,
idempotent cancellation. Already-running native/network operations return at
their next checkpoint.

Validation: 123 targeted tests pass both locally and inside the production
runtime image, covering layout classification,
extraction, marking, cancellation races, shared OCR capacity, and cancellation
while waiting for a slot. The frontend production build and changed-file lint
pass. Full frontend lint has a pre-existing `process`/`no-undef` error in
`frontend/vite.config.js`.
