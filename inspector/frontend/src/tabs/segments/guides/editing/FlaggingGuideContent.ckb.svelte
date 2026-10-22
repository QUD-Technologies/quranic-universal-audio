<script lang="ts">
    import MockSegCard from './MockSegCard.svelte';
    import { synthPeaks } from './synth-peaks';

    const wave = synthPeaks(150, 11);
</script>

<div class="eg-root">
    <p>
        زۆربەی دەستکارییەکان شتێک چاک دەکەنەوە. <strong>نیشانە</strong> تەنها پرسیار یان لێدوانێک جێدەهێڵێت.
        بۆ حاڵەتەکانێک بەکاری بهێنە کە چارەسەرێکی ڕوونیان نییە، دەنگێکی نائاسایین یان هەڵەی قارییەکەیە.
        لەگەڵ نیشانەکە لێدوانێک زیاد بکە.
    </p>
    <div class="eg-card-frame"><MockSegCard peaks={wave} emphasize={['flag']} /></div>
    <p>کاتێک نیشانەیەک دانرا، هەر کەسێک دەتوانێت وەڵام بداتەوە بۆ یارمەتیدانی چارەسەرکردنی کێشەکە.</p>
    <div class="eg-card-frame"><MockSegCard peaks={wave} flagged showFlagComment /></div>
</div>
