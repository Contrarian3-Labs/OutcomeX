---
name: wan-i2v-dashscope
description: Turn an input image into a short video with DashScope Wan I2V. Use when the task asks to animate an image into motion.
---

# Wan Image to Video

Use this skill when the task includes an image and asks for motion, animation, teaser, or short video output.

## Command

```bash
python scripts/image_to_video.py "Animate the product with a slow cinematic camera move" --input frame.png --output clip.mp4
```

## Notes
- Requires `DASHSCOPE_API_KEY`
- Uses `wan2.7-i2v`
- Outputs an mp4 video file
