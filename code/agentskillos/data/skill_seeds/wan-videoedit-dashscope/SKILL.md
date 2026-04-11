---
name: wan-videoedit-dashscope
description: Edit an existing video with DashScope Wan VideoEdit. Use when the task explicitly asks to transform a supplied video.
---

# Wan Video Edit

Use this skill when the task already includes a source video and asks to recut, restyle, reshape, or visually transform it.

## Command

```bash
python scripts/video_edit.py "Turn the whole clip into a clay animation aesthetic" --input source.mp4 --output edited.mp4
```

## Notes
- Requires `DASHSCOPE_API_KEY`
- Uses `wan2.7-videoedit`
- Outputs an mp4 video file
