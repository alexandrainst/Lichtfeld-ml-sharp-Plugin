<div align="center">

<h1>SHARP 4D Plugin for <a href="https://github.com/MrNeRF/LichtFeld-Studio">LichtFeld Studio</a></h1>

<img src="assets/milo.gif" alt="SHARP 4D Video Plugin Demo" width="85%"/>

</div>

A 4D Gaussian Splatting plugin for LichtFeld Studio, powered by [SHARP](https://github.com/apple/ml-sharp).
Convert videos into animated Gaussian sequences, or convert a single image into a Gaussian scene.

This fork keeps the original plugin ID (`sharp_4d`) but adds several practical fixes for remote and heavy-scene workflows.

## Why this plugin

- Convert `.mp4` videos into frame-by-frame Gaussian PLY sequences.
- Convert images into a single Gaussian PLY.
- Set exactly how many video frames to process with a frame slider.
- Built-in playback: play/pause, frame scrub, and playback FPS.
- Runs SHARP inside LichtFeld Studio.
- Manual path entry for remote sessions where native file dialogs do not behave correctly.
- Cached output reload from the generated `*_gaussians` folder.
- Safer default behavior for large frame sets by disabling autoplay and background preload after processing.

## Install

### From GitHub (recommended)

In LichtFeld Studio:
1. Open the **Plugins** panel.
2. Enter your fork URL.
3. Click **Install**.

See `INSTALL.md` for a more detailed setup and deployment workflow.


## Quick start

### 1. Open the panel
Open the **Sharp 4D Video** tab in LichtFeld Studio.

### 2. Select media
Use:
- **Select Video File (.mp4)** for video
- **Select Image File** for a single image

The plugin detects the media type automatically.

### 3. Video workflow
1. Set **Number of Frames to Convert** (slider).
2. If cached output exists, click **Load N Frames From Disk**.
3. Or click **Process N Frames** to run SHARP.
4. Playback stays idle after processing or cached load so large outputs do not immediately thrash the scene.

### 4. Image workflow
1. Select an image file.
2. Click **Process Image**.
3. The generated output is loaded immediately.

## Output location

Generated files are written next to the input as:
- `<input_name>_gaussians/`

For videos, this folder contains one PLY per frame (`frame_00000.ply`, etc.).
For images, it contains one PLY for the image.

## Fork changes

- Adds a `Manual Path` field so media can be opened without the native file picker.
- Keeps cached-output reload available from the generated `*_gaussians` folder.
- Stops autoplay after processing or cached load.
- Removes eager background preload that can freeze LichtFeld Studio on large SHARP frame sets.

## Requirements

- [LichtFeld Studio](https://github.com/MrNeRF/LichtFeld-Studio)

## Notes

- First run may take longer because SHARP model weights need to download.
- Large frame counts can take significant time and VRAM.

## Credits and License

This plugin integrates the **SHARP** (Spatio-temporal Hierarchical Auto-Regressive Point-clouds) architecture.

- **Plugin Code**: Released under [GPL-3.0-or-later](LICENSE).
- **SHARP Library**: Included as a library in `ml-sharp`. Please refer to `ml-sharp/LICENSE` and `ml-sharp/LICENSE_MODEL` for specific usage rights regarding the model and inference code.
