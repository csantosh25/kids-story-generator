# Reel Background Music

Drop royalty-free, locally-owned MP3 tracks in this folder to make them
selectable when generating a Reel (`python generate_reel.py`).

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
  service.
- If this folder is empty, Reel generation still works — narration-only,
  no music.
- Music is mixed well under the narration (default ~10% relative volume)
  so the narration always stays clearly audible.
