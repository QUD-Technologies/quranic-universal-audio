-- mp3quran moved every moshaf to `cdn.mp3quran.net/audio/<reciter>/rN/`; the
-- old `serverN.mp3quran.net` hosts only 301 there. Match the new host so intake
-- maps it to the channel, and keep the old pattern for historical sidecars.

UPDATE channels
SET host_patterns = json_insert(host_patterns, '$[#]', 'cdn.mp3quran.net')
WHERE slug = 'mp3quran'
  AND NOT EXISTS (
    SELECT 1 FROM json_each(channels.host_patterns) WHERE value = 'cdn.mp3quran.net'
  );
