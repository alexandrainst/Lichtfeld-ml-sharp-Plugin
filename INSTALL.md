# Install

This fork is intended to replace the upstream `sharp_4d` plugin with a more remote-friendly version.

## Recommended Install

1. Open LichtFeld Studio.
2. Open the `Plugins` panel.
3. Install this fork from its GitHub URL.
4. Disable or remove the upstream `sharp_4d` plugin if it is already installed.
5. Restart LichtFeld Studio.

## Local Development Install

If you want to test directly from a local checkout, point LichtFeld Studio at this repo instead of the upstream plugin.

## Notes For Remote Sessions

- Native file pickers can hang or fail to focus over XRDP, FreeRDP, and TurboVNC.
- Use the `Manual Path` field in the plugin UI when working remotely.
- Large SHARP outputs can make playback and reload feel frozen. This fork disables autoplay and background preloading after processing or cached loads.

## Output Reuse

- Processed outputs are stored beside the source media in a sibling folder named `<input_name>_gaussians`.
- Those per-frame `.ply` files are the reusable SHARP output.
- Reopening the same input path lets the plugin discover and load cached output from disk.
