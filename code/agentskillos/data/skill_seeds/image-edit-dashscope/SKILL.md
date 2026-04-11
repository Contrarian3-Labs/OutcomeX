---
name: image-edit-dashscope
description: Edit an input image with DashScope Wan image editing. Use when the task explicitly asks to modify an existing image.
---

# Image Edit via DashScope

Use this skill when the task already includes an input image and the goal is to change, revise, retouch, or restyle it.

## Inputs
- a source image path or URL
- a concrete editing prompt

## Command

```bash
python scripts/image_edit.py "Make the lighting cleaner and remove the background clutter" --input source.png --output edited.png
```

## Notes
- Requires `DASHSCOPE_API_KEY`
- Uses `wan2.7-image-pro`
- Saves the edited result locally
