---
name: wan-t2v-dashscope
description: Generate a short video directly from a text prompt with DashScope Wan T2V. Use when the task asks for a video and there is no required input image.
---

# Wan Text to Video

Use this skill when the task asks for a text-to-video result, including short clips, teaser videos, motion shots, or AI-generated video from a pure prompt.

## Command

```bash
python scripts/text_to_video.py "Create a cinematic portrait video with soft lighting" --output clip.mp4
```

## Notes
- Requires `DASHSCOPE_API_KEY`
- Uses `wan2.2-t2v-plus`
- Outputs an mp4 video file
