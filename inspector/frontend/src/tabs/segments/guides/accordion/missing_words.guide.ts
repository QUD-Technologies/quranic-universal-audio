const source = `
# Missing Words

Missing words are words that don't appear in any segment, or gaps / forward "jumps" between two segments. 

Usually it is present in a segment's audio but the model did not detect it — auto-fill intelligently attaches the word to the correct side, and when the side is ambiguous you can auto-fill up or down.

Sometimes both the audio and the word(s) is missing. Fill it and adjust the segment boundary to cover it. 

A word present in the text but audio is partially / fully missing in the segment tends to surface under Low Confidence instead.

> By the end this should be zero — every recited word placed, unless there are issues in the audio.

## Auto-fill handles it

::example{id="missing_autofill"}

## Fill, then adjust the boundary

::example{id="missing_fill_start"}
::example{id="missing_fill_extend"}
`;

export default source;
