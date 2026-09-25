"""Online intake: enumerate a submission's source, let the owner review the file
list + catalog identity, then mint the delivery and start its align run.

    enumerate  ``enumerate.py`` (+ ``drive.py``) — playlist / folder / links → files
    plan       ``plan.py`` — ``payload.plan`` lifecycle + the live check
    identity   ``identity.py`` — slug / reciter / channel / source proposal + check
    mint       ``mint.py`` — plan → ``intake.ingest`` body → ``align_pipeline.runs.start``

File titles are never read for chapters: a playlist's files are minted as the
manifest's ``sources`` and the align run's surah detection decides what each
holds (``align_pipeline/resolve.py``).
"""
