"""
SHARP 4D Video Processor using the SHARP CLI.
"""

import os
import shutil
import tempfile
import logging
import numpy as np
import imageio.v2 as imageio
import imageio_ffmpeg
import torch
from pathlib import Path

# Import the CLI command and utilities directly
from sharp.cli.predict import predict_image, DEFAULT_MODEL_URL
from sharp.models import PredictorParams, create_predictor
from sharp.utils import io
from sharp.utils.gaussians import load_ply, save_ply
from plyfile import PlyData

# Force imageio to use the ffmpeg binary from the imageio-ffmpeg package
os.environ["IMAGEIO_FFMPEG_EXE"] = imageio_ffmpeg.get_ffmpeg_exe()

def probe_video_metadata(video_path: str | Path) -> tuple[float, int]:
    """
    Return (fps, total_frames) for a video path.
    """
    reader = imageio.get_reader(str(video_path), format="ffmpeg")
    try:
        meta = reader.get_meta_data() or {}
        fps = float(meta.get("fps", 30.0) or 30.0)
        total_frames = 0

        try:
            total_frames = int(reader.count_frames())
        except Exception:
            nframes = meta.get("nframes")
            if isinstance(nframes, (int, float)) and nframes > 0:
                total_frames = int(nframes)

        if total_frames <= 0:
            total_frames = 0
            for _ in reader:
                total_frames += 1

        return fps, total_frames
    finally:
        reader.close()

class SharpProcessor:
    def __init__(self):
        # We don't want to reset basicConfig if LFS has set it up, but we get a logger
        self.logger = logging.getLogger("SharpProcessor")

    def _load_predictor(self):
        import ssl
        import urllib.request
    
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.logger.info(f"Using device: {device}")
    
        # TEMP: disable SSL verification for model download
        ssl_context = ssl._create_unverified_context()
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl_context)
        )
        urllib.request.install_opener(opener)
    
        state_dict = torch.hub.load_state_dict_from_url(DEFAULT_MODEL_URL, progress=True)
    
        gaussian_predictor = create_predictor(PredictorParams())
        gaussian_predictor.load_state_dict(state_dict)
        gaussian_predictor.eval()
        gaussian_predictor.to(device)
    
        return gaussian_predictor, torch.device(device)

    def process_video(
        self,
        video_path: str,
        output_dir: str,
        progress_callback=None,
        max_frames: int | None = None,
    ) -> tuple[list[str], float]:
        """
        Process a video file using the 'sharp predict' CLI command (in-process).
        """
        video_path = Path(video_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if max_frames is not None and max_frames <= 0:
            raise ValueError("max_frames must be > 0 when provided")

        for old_ply in output_dir.glob("frame_*.ply"):
            old_ply.unlink()

        # 1. Create temporary directory for frames
        temp_dir = Path(tempfile.mkdtemp(prefix="sharp_frames_"))
        try:
            self.logger.info(f"Extracting frames to {temp_dir}")
            
            # Ensure we are passing a string
            video_path_str = str(video_path)
            
            # Force ffmpeg backend to ensure MP4 support
            reader = imageio.get_reader(video_path_str, format='ffmpeg')
            meta = reader.get_meta_data()
            fps = meta.get("fps", 30.0)
            
            try:
                total_frames = reader.count_frames()
            except:
                total_frames = 0

            if max_frames is None:
                extract_total = total_frames
                extract_total_msg = total_frames if total_frames > 0 else None
            else:
                extract_total = min(total_frames, max_frames) if total_frames > 0 else max_frames
                extract_total_msg = extract_total if extract_total > 0 else max_frames

            for i, frame in enumerate(reader):
                if max_frames is not None and i >= max_frames:
                    break

                if progress_callback:
                    if extract_total_msg:
                        msg = f"Extracting frame {i+1}/{extract_total_msg}"
                    else:
                        msg = f"Extracting frame {i+1}"
                    progress_callback(i, extract_total, msg)
                
                frame_path = temp_dir / f"frame_{i:05d}.jpg"
                imageio.imsave(frame_path, frame)

            reader.close()

            # 2. Run SHARP Inference (In-Process)
            self.logger.info("Running SHARP Inference...")
            
            image_paths = sorted(list(temp_dir.glob("*.jpg")))
            total_frames = len(image_paths)
            
            if total_frames == 0:
                raise RuntimeError(f"No frames found in {temp_dir}")

            # Load model
            if progress_callback:
                progress_callback(0, total_frames, "Loading SHARP model...")
            
            gaussian_predictor, torch_device = self._load_predictor()

            for i, image_path in enumerate(image_paths):
                if progress_callback:
                    progress_callback(i, total_frames, f"SHARP Inference: Processing frame {i+1}/{total_frames}")
                
                # Load image using SHARP's utility
                image, _, f_px = io.load_rgb(image_path)
                height, width = image.shape[:2]
                
                # Predict Gaussians
                gaussians = predict_image(gaussian_predictor, image, f_px, torch_device)
                
                # Save as PLY
                save_ply(gaussians, f_px, (height, width), output_dir / f"{image_path.stem}.ply")

            self.logger.info("SHARP Inference complete.")
            
            # Cleanup model to free memory
            del gaussian_predictor
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            # 3. Collect generated PLY files
            ply_files = sorted([str(p) for p in output_dir.glob("frame_*.ply")])
            return ply_files, fps

        finally:
            # Cleanup temp frames
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def process_image(self, image_path: str, output_dir: str, progress_callback=None) -> list[str]:
        """
        Process a single image file and export one Gaussian Splat PLY.
        """
        image_path = Path(image_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if progress_callback:
            progress_callback(0, 1, "Loading SHARP model...")

        gaussian_predictor, torch_device = self._load_predictor()

        if progress_callback:
            progress_callback(0, 1, "Running SHARP Inference...")

        image, _, f_px = io.load_rgb(image_path)
        height, width = image.shape[:2]
        gaussians = predict_image(gaussian_predictor, image, f_px, torch_device)

        output_path = output_dir / f"{image_path.stem}.ply"
        save_ply(gaussians, f_px, (height, width), output_path)

        del gaussian_predictor
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        if progress_callback:
            progress_callback(1, 1, "Complete")

        return [str(output_path)]

def load_gaussian_ply(ply_path):
    """
    Load a Gaussian Splat PLY file and return tensors suitable for scene.add_splat()

    Returns:
        means    : [N, 3]
        sh0      : [N, 1, 3]
        scaling  : [N, 3]
        rotation : [N, 4]  (wxyz)
        opacity  : [N, 1]
    """
    ply = PlyData.read(ply_path)
    v = ply["vertex"].data

    # --- Means ---
    means = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)

    # --- SH0 (RGB) ---
    sh0 = np.stack(
        [v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]],
        axis=1
    ).astype(np.float32)
    sh0 = sh0[:, None, :]  # [N, 1, 3]

    # --- Opacity ---
    opacity = v["opacity"].astype(np.float32)[:, None]

    # --- Scaling ---
    scaling = np.stack(
        [v["scale_0"], v["scale_1"], v["scale_2"]],
        axis=1
    ).astype(np.float32)

    # --- Rotation ---
    rotation = np.stack(
        [v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]],
        axis=1
    ).astype(np.float32)

    # IMPORTANT: ensure wxyz order
    # If SHARP writes xyzw, swap here:
    # rotation = rotation[:, [3, 0, 1, 2]]

    return means, sh0, scaling, rotation, opacity
def extract_data_from_ply(ply_path):
    """
    Extract point cloud data (means and colors) from a SHARP PLY file.
    """
    gaussians, metadata = load_ply(Path(ply_path))
    xyz = gaussians.mean_vectors.detach().cpu().numpy().reshape(-1, 3)
    rgb = gaussians.colors.detach().cpu().numpy().reshape(-1, 3)
    rgb = np.clip(rgb, 0.0, 1.0)
    return xyz, rgb

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("video", help="Input video path")
    parser.add_argument("output", help="Output directory")
    args = parser.parse_args()
    
    proc = SharpProcessor()
    files, fps = proc.process_video(args.video, args.output, lambda i, t, m: print(f"{m} ({i}/{t})"))
    print(f"Processed {len(files)} frames at {fps} FPS.")
