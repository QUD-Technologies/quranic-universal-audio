const source = `
# Cross-verse

A cross-verse segment spans a verse boundary. We split it because the published dataset stores one row per verse, so each verse can be searched and played on its own. How you split depends on what the reciter did, and the boundary tag is kept as metadata either way.

When the aligner already knows the cut, the card shows the pieces up front with a WASL · WAQF picker between them. Labelling is the whole edit: the split saves on the last label. If the suggested cut is off, use **Adjust** on the piece — any edit on a piece applies the split first, then runs as usual. The header chips (Unset · Wasl · Waqf) count boundaries and filter the list, so labelled items stay reviewable.

> By the end Unset should be zero. Auto-split is mostly accurate, but cursors might need adjusting in some cases, especially in Wasl.

## Waqf — the reciter paused

The reciter stopped at the boundary but the model missed the silence. Split at the pause.

::example{id="xverse_waqf"}
::example{id="xverse_multi"}

## Wasl — the reciter continued

There's no silence to cut. Split where the two verses separate most cleanly and tag the boundary wasl, so the data records that the reciter ran them together.

::example{id="xverse_wasl"}
`;

export default source;
