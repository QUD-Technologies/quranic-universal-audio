const source = `
# Low Confidence

Low confidence are calculations and algorithms that mark segments where the model wasn't sure its text matched the audio. It's a signal to check, not proof of an error — most are correct and align perfectly, so use the ignore button.

When something is off, the cause varies and so does the fix — it can be any edit.

> By the end this should be zero (for <85%): each one either corrected or ignored.

::example{id="lowconf_wrong_word"}
`;

export default source;
