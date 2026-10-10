"""Stored segment times: how the Inspector gets, keeps and reads them.

Every timed segment's word, letter and phone times live per audio chapter in
``reciters/<slug>/timing/<ch>.json.br``, written by the aligner's neural timing head:

- :mod:`.aligner_timing` calls the aligner for one chapter (times kept where the segment is
  unchanged, the rest timed) — at the end of an align run, after a save, and in a
  timestamps run, which also builds the chapter's shards from them;
- :mod:`.retime_queue` re-times a chapter in the background after a save or undo;
- :mod:`.word_times` reads the stored times back as word intervals for the segment cards.
"""
