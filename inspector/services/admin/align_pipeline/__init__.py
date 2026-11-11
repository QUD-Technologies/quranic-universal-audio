"""Native align pipeline — one click in the Requests tab, four stages on existing Spaces.

The audio manifest's chapters are grouped by physical file (``sources``): a
single-chapter file, or a *combined* file holding several chapters, acquired
into a source slot ``audio/<901+i>.mp3`` and split after aligning.

    acquire   CPU HF Job (``qua_jobs/acquire_audio.py``) persists single-chapter
              audio + peaks to ``reciters/<slug>/{audio,peaks}/`` and combined
              files to their slot; ``manifest`` writes size/duration back.
    align     per-file loop against the aligner Space's ``/api/v1/batches``
              (alignment-only, ``audio_ref=hf://buckets/…``), raw results staged
              under ``staging/<slug>/<run>/{chapters,sources}/``; then
              ``stage_split`` partitions combined files by surah (``partition``)
              and cuts them per chapter (``qua_jobs/split_audio.py``).
    sidecars  one reciter-wide ``/api/v1/extraction/sidecars`` call (low-confidence
              probe + auto-split cursors, MFA on the phoneme Space).
    assemble  in-process ``promote_build`` → ``reciters/<slug>/{detailed,segments,
              …}``; ``auto_detect`` then fires ``alignment_completed``.

Online intake (``services/admin/intake_plan/``) mints a slugless submission's
delivery and starts a run here. ``runs`` is the public surface (start / retry / cancel / status); ``runner`` owns
the worker threads; the ``stage_*`` modules are the stages; ``adapt`` turns the
aligner's public rows into the staged-run shapes ``promote_build`` reads.
See ``docs/reference/align-pipeline.md``.
"""
