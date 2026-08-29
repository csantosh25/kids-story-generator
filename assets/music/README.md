# Reel Background Music

Drop royalty-free, locally-owned MP3 tracks in this folder to make them
usable when generating a Reel (`python generate_reel.py`).

Example layout:

```
assets/music/
    gentle_01.mp3
    playful_01.mp3
    happy_01.mp3
```

Rules:

- Only `.mp3` files placed here manually are used. This project never
  downloads music automatically and never calls an AI music-generation
  service. Zero API calls are involved in selecting or mixing music.
- If this folder is empty (or every file in it is invalid/corrupt), Reel
  generation still works — narration-only, no music. A bad file is
  skipped in favour of any other valid track rather than failing the
  Reel.
- When at least one valid track exists, `python generate_reel.py
  --content-id ...` (and the interactive picker, if you just press Enter)
  selects one **deterministically** from the story's content ID: the same
  story always picks the same track when regenerated, while different
  stories can land on different tracks. The interactive picker also lets
  you choose a specific track, or "0" for no music.
- The selected track (or the fact that none was available) is recorded in
  that story's `reel_script.json` under a `"music"` key.
- Music is mixed well under the narration (default ~10% relative volume)
  so the narration always stays clearly audible, with a short fade-in/
  fade-out. A track shorter than the Reel is looped; a longer track is
  trimmed — narration/video duration is always authoritative, music never
  changes how long the Reel is.
