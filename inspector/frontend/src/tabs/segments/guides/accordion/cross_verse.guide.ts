const source = `
# Cross-verse

A cross-verse segment spans a verse boundary. We split it because the published dataset stores one row per verse, so each verse can be searched and played on its own. How you split depends on what the reciter did, and the boundary tag is kept as metadata either way.

> Nothing to do here. Every verse end is either auto-classified WAQF / WASL or flagged in **Low Confidence Waqf**, and this category never blocks Mark Ready. It is useful for checking and reviewing the WASL / WAQF verdicts.

The card shows the pieces with a WASL · WAQF picker between them. To correct a verdict, pick the other label; if a cut is off, use **Adjust** on the piece. The header chips (Unset · Wasl · Waqf) count boundaries and filter the list; it opens on Unset.

## Waqf — the reciter paused

The reciter stopped at the boundary but the model missed the silence. Split at the pause.

::example{id="xverse_waqf"}
::example{id="xverse_multi"}

## Wasl — the reciter continued

There's no silence to cut. Split where the two verses separate most cleanly and tag the boundary wasl, so the data records that the reciter ran them together.

::example{id="xverse_wasl"}
`;

export default source;
