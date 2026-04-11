---
name: wan-r2v-dashscope
description: Generate a short video from a reference image with DashScope Wan R2V. Use when identity or style consistency matters.
---

# Wan Reference to Video

Use this skill when the task asks for a video that should stay faithful to a reference image or character.

## Command

```bash
python scripts/reference_to_video.py "Create a playful teaser while preserving the character identity" --input reference.png --output teaser.mp4
```

## Notes
- Requires `DASHSCOPE_API_KEY`
- Uses `wan2.7-r2v`
- Outputs an mp4 video file
