"""Online intake: enumerate a submission's source, match entries to chapters,
let the owner review the plan, then mint the delivery and start its align run.

    enumerate  ``enumerate.py`` (+ ``drive.py``) — playlist / folder / links → entries
    match      ``match.py`` — entry title → chapter(s), with a confidence
    plan       ``plan.py`` — ``payload.plan`` lifecycle + the live check
    identity   ``identity.py`` — slug / reciter / channel / source proposal + check
    mint       ``mint.py`` — plan → ``intake.ingest`` body → ``align_pipeline.runs.start``

Replaces the offline ``ingest_intake.py`` + LLM-reviewed chapter map.
"""
