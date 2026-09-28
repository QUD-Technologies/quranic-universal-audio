const source = `
# Low Confidence Waqf

The aligner heard what may be a stop inside this segment, but not clearly enough to cut there. Each proposed cut asks one question: did the reciter stop at this word, or read straight through? Listen across the cut and label it.

> By the end Unset should be zero. Each label is recorded as an edit. WASL keeps the audio continuous; WAQF cuts at the stop. Save your edits when ready.

## Waqf — the reciter stopped

A real stop drops the final vowel of the word before the cut, and the next word starts afresh. In «ٱللَّهُ لَآ إِلَٰهَ إِلَّا هُوَ ٱلۡحَيُّ ٱلۡقَيُّومُ» (2:255) the reciter ends on «إِلَّا هُوۡ», pauses, then begins «ٱلۡحَيُّ» with its hamza voiced: "al-ḥayyu". Label WAQF and the segment is split at the cut.

## Wasl — the reciter continued

The vowel carries into the next word and the hamza of «ٱلۡ» stays silent: "illā huwa l-ḥayyu". A short dip in the waveform with no dropped vowel is not a stop. Label WASL and the segment stays whole.
`;

export default source;
