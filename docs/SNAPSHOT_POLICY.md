# Snapshot policy (stage 0)

1. **A snapshot is the data itself, not only its hash.** Every file is kept byte-exact with a manifest entry:
   source, retrieval time (UTC), information date, code version (git commit), label, SHA-256 and size
   (`fundamental_engine/snapshot.py: entry/verify`).
2. **Point in time.** A fact counts as known at an as-of date only if filed at least 2 days earlier
   (`snapshot.cutoff/known_at`). Raw files that contain later filings are labelled as such and are used only through
   the filtered views. Restatements known by the as-of date are listed, not silently replaced.
3. **Honest labels.** A view rebuilt later from a later download is labelled `RECONSTRUCTED`, never presented as an
   original same-day snapshot. Adjusted prices, revised data and anything that cannot be re-created reliably are
   marked in the manifest or the result file.
4. **Prices** are the as-traded close of the last trading day on or before the as-of date, with later split
   adjustments undone, and are used as verified only when a second source agrees within 1%; otherwise UNVERIFIED or
   missing.
5. **Where it lives.** Public SEC data may be published; vendor prices, position sizes and account values never go
   to Git. Every snapshot set is kept in at least two places outside the working container and read back
   (SHA-256 identical) before it counts as stored. A storage route that was not tested end-to-end is not assumed to
   work.
6. **No silent workarounds.** If a source, a store or a permission fails, the failure is written down and the
   affected criterion is reported as not met.
