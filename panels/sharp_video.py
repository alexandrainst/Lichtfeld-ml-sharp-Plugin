# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sharp 4D Video Panel."""

import threading
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List

import lichtfeld as lf
from lfs_plugins.types import Panel

from .. import sharp_processor

class Stage(Enum):
    IDLE = "idle"
    PROCESSING = "processing"
    PLAYING = "playing"
    ERROR = "error"

class InputKind(Enum):
    NONE = "none"
    VIDEO = "video"
    IMAGE = "image"

@dataclass
class ProcessResult:
    success: bool
    ply_files: List[str] = field(default_factory=list)
    fps: float = 30.0
    error: Optional[str] = None

class ProcessingJob:
    def __init__(
        self,
        input_path: str,
        input_kind: InputKind,
        max_video_frames: Optional[int] = None,
    ):
        self.input_path = input_path
        self.input_kind = input_kind
        self.max_video_frames = max_video_frames
        self.progress = 0.0
        self.status = ""
        self.result = None
        self._thread = None
        self._lock = threading.Lock()

    def start(self, callback):
        self._thread = threading.Thread(target=self._run, args=(callback,), daemon=True)
        self._thread.start()

    def _run(self, callback):
        try:
            if self.input_kind == InputKind.VIDEO:
                processor = sharp_processor.SharpProcessor()
                
                def prog_cb(i, total, msg):
                    with self._lock:
                        self.status = msg
                        if total > 0:
                            self.progress = (i / total) * 100
                
                # Output dir is adjacent to video
                v_path = Path(self.input_path)
                out_dir = v_path.parent / (v_path.stem + "_gaussians")
                
                files, fps = processor.process_video(
                    self.input_path,
                    str(out_dir),
                    prog_cb,
                    max_frames=self.max_video_frames,
                )
                
                # Unload model by deleting processor instance (assuming cleanup happens in __del__ or by GC)
                # If explicit unload needed, add method to processor. Here GC handles it.
                del processor
                
            elif self.input_kind == InputKind.IMAGE:
                processor = sharp_processor.SharpProcessor()

                def prog_cb(i, total, msg):
                    with self._lock:
                        self.status = msg
                        if total > 0:
                            self.progress = (i / total) * 100

                image_path = Path(self.input_path)
                out_dir = image_path.parent / (image_path.stem + "_gaussians")
                files = processor.process_image(self.input_path, str(out_dir), prog_cb)
                fps = 0.0
                del processor
            else:
                raise RuntimeError("Unsupported input type")

            with self._lock:
                self.result = ProcessResult(True, files, fps)
                self.progress = 100.0
                self.status = "Complete"
            
            callback(self.result)
            
        except Exception as e:
            logging.error(f"Processing failed: {e}")
            with self._lock:
                self.result = ProcessResult(False, error=str(e))
                self.status = f"Error: {e}"
            callback(self.result)

class SharpVideoPanel(Panel):
    idname = "sharp_4d.video_panel"
    label = "Sharp 4D Video"
    space = "MAIN_PANEL_TAB"
    order = 10000
    VIDEO_EXTENSIONS = {".mp4"}
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".heic"}
    SUPPORTED_MEDIA_LABEL = "Supported: .mp4, .jpg, .jpeg, .png, .bmp, .tif, .tiff, .webp, .heic"
    ACTION_BUTTON_HEIGHT = 32

    def __init__(self):
        self.input_path = ""
        self.input_kind = InputKind.NONE
        
        self.job = None
        self.ply_files = []
        self.playback_fps = 30.0
        self.current_frame_idx = 0
        self.last_frame_time = 0.0
        self.is_playing = False
        self._last_displayed_frame_idx = None
        
        self.frame_cache = {} 
        self.cache_limit = 150 
        
        self.stage = Stage.IDLE
        self.error_message = ""
        self.source_fps = 30.0
        self.video_total_frames = 1
        self.image_complete_message = ""
        self.max_video_frames_input = 1

    def draw(self, layout):
        layout.heading("Sharp 4D Media")
        layout.text_wrapped(
            "Select one media file (.mp4 or image). The plugin auto-detects the type and runs the correct pipeline."
        )
        layout.separator()

        stage_label, stage_color = self._stage_style()
        layout.text_colored(f"Stage: {stage_label}", stage_color)
        layout.separator()

        if self.stage == Stage.PROCESSING:
            if self.job:
                with self.job._lock:
                    status = self.job.status or "Initializing..."
                    progress = max(0.0, min(100.0, self.job.progress))
                    layout.text_colored(f"Status: {status}", (0.35, 0.70, 1.0, 1.0))
                    layout.progress_bar(progress / 100.0, f"{progress:.1f}%")
            return

        if layout.collapsing_header("Configuration", default_open=True):
            layout.label("Media File")
            if layout.button_styled("Select Video File (.mp4)", "primary", (190, 28)):
                selected = lf.ui.open_video_file_dialog()
                if selected:
                    self._set_input_path(selected)
            layout.same_line()
            if layout.button("Select Image File"):
                selected = lf.ui.open_image_dialog(self._input_start_dir())
                if selected:
                    self._set_input_path(selected)
            layout.same_line()
            if layout.small_button("Clear"):
                self._set_input_path("")

            layout.text_disabled(self.SUPPORTED_MEDIA_LABEL)

            detected_label = self.input_kind.value if self.input_kind != InputKind.NONE else "not detected"
            layout.label(f"Detected Type: {detected_label}")
            layout.text_selectable(self.input_path if self.input_path else "No media file selected")

            if self.input_kind == InputKind.VIDEO:
                layout.text_colored("Video detected: playback controls are enabled.", (0.20, 0.80, 0.45, 1.0))
                layout.label("Number of Frames to Convert:")
                layout.same_line()
                _, self.max_video_frames_input = layout.slider_int(
                    "##max_frames_to_convert",
                    self.max_video_frames_input,
                    1,
                    self.video_total_frames,
                )
                self.max_video_frames_input = max(1, min(self.max_video_frames_input, self.video_total_frames))

                selected_limit = self._selected_video_frame_limit()
                layout.label(f"Frame Limit: {selected_limit}/{self.video_total_frames} frames will be converted.")
                _, self.playback_fps = layout.slider_float("Playback FPS", self.playback_fps, 1.0, 120.0)
                layout.label(f"Detected Source FPS: {self.source_fps:.2f}")
                layout.label(f"Total Frames in Video: {self.video_total_frames}")
            elif self.input_kind == InputKind.IMAGE:
                layout.text_colored("Image detected: a single Gaussian output will be produced.", (0.20, 0.80, 0.45, 1.0))
            elif self.input_path:
                layout.text_colored(
                    "Unsupported type. Please use .mp4 or a supported image extension.",
                    (1.0, 0.45, 0.25, 1.0),
                )

        if self.stage in {Stage.IDLE, Stage.ERROR}:
            if self.input_kind == InputKind.VIDEO:
                selected_limit = self._selected_video_frame_limit()
                cached_count = self._existing_output_count()
                if cached_count > 0:
                    load_count = min(selected_limit, cached_count)
                    if layout.button_styled(
                        f"Load {load_count} Frames From Disk",
                        "primary",
                        (-1, self.ACTION_BUTTON_HEIGHT),
                    ):
                        loaded = self._load_existing_output(frame_limit=selected_limit)
                        if not loaded:
                            self.error_message = "Could not load cached frames from disk."
                            self.stage = Stage.ERROR

                if layout.button_styled(
                    f"Process {selected_limit} Frames",
                    "primary",
                    (-1, self.ACTION_BUTTON_HEIGHT),
                ):
                    self._start_processing()
            elif self.input_kind == InputKind.IMAGE:
                if layout.button_styled("Process Image", "primary", (-1, self.ACTION_BUTTON_HEIGHT)):
                    self._start_processing()
            else:
                if layout.button_styled("Process Media", "primary", (-1, self.ACTION_BUTTON_HEIGHT)):
                    self._start_processing()
            
            if self.stage == Stage.ERROR and self.error_message:
                layout.text_colored(f"Error: {self.error_message}", (1.0, 0.25, 0.25, 1.0))

        if self.ply_files:
            layout.separator()
            if self.input_kind == InputKind.VIDEO:
                layout.heading("Result Playback")
                layout.label(f"Frames: {len(self.ply_files)}")
                if layout.button("Pause" if self.is_playing else "Play"):
                    self.is_playing = not self.is_playing
                    self.stage = Stage.PLAYING if self.is_playing else Stage.IDLE
                    self.last_frame_time = time.time()
                if layout.button("Reset Frame"):
                    self.current_frame_idx = 0
                    self._update_scene_frame(0)
                    self._last_displayed_frame_idx = 0

                _, self.current_frame_idx = layout.slider_int(
                    f"Frame {self.current_frame_idx+1}/{len(self.ply_files)}", 
                    self.current_frame_idx, 0, len(self.ply_files)-1
                )

                if not self.is_playing and self.current_frame_idx != self._last_displayed_frame_idx:
                    self._update_scene_frame(self.current_frame_idx)
                    self._last_displayed_frame_idx = self.current_frame_idx
            else:
                layout.heading("Result")
                if self.image_complete_message:
                    layout.text_colored(self.image_complete_message, (0.20, 0.80, 0.45, 1.0))

        if self.is_playing and self.ply_files:
            now = time.time()
            frame_duration = 1.0 / max(1.0, self.playback_fps)
            if now - self.last_frame_time >= frame_duration:
                self.current_frame_idx = (self.current_frame_idx + 1) % len(self.ply_files)
                self._update_scene_frame(self.current_frame_idx)
                self._last_displayed_frame_idx = self.current_frame_idx
                self.last_frame_time = now

    def _start_processing(self):
        self.error_message = ""  # Clear previous errors
        self.image_complete_message = ""
        
        if not self.input_path.strip():
            self.error_message = "Please select a video or image file"
            self.stage = Stage.ERROR
            return
        
        path = Path(self.input_path)
        if not path.exists() or not path.is_file():
            self.error_message = f"File does not exist: {self.input_path}"
            self.stage = Stage.ERROR
            return

        input_kind = self._detect_input_kind(self.input_path)
        if input_kind is None:
            self.error_message = "Unsupported file type. Select an .mp4 video or supported image file."
            self.stage = Stage.ERROR
            return

        self.input_kind = input_kind
        self._reset_result_state()
        self.job = ProcessingJob(
            self.input_path,
            self.input_kind,
            max_video_frames=self._selected_video_frame_limit(),
        )
        self.stage = Stage.PROCESSING
        self.job.start(self._on_complete)

    def _on_complete(self, result: ProcessResult):
        if result.success:
            self.ply_files = sorted(result.ply_files)
            self.stage = Stage.IDLE 
            self.current_frame_idx = 0
            self._last_displayed_frame_idx = 0
            self.image_complete_message = ""

            if self.input_kind == InputKind.VIDEO and result.fps > 0:
                self.source_fps = result.fps
                self.playback_fps = result.fps

            self.is_playing = self.input_kind == InputKind.VIDEO and len(self.ply_files) > 1
            self.stage = Stage.PLAYING if self.is_playing else Stage.IDLE
            if self.input_kind == InputKind.IMAGE:
                self.image_complete_message = "Processing completed."
            self.last_frame_time = time.time()
            self.frame_cache.clear()
            if self.ply_files:
                self._update_scene_frame(0)
            
            threading.Thread(target=self._preload_frames, daemon=True).start()
        else:
            self.error_message = result.error or "Processing failed"
            self.stage = Stage.ERROR

    def _input_start_dir(self) -> str:
        if self.input_path:
            parent = Path(self.input_path).parent
            if parent.exists():
                return str(parent)
        return str(Path.home())

    def _detect_input_kind(self, input_path: str) -> Optional[InputKind]:
        suffix = Path(input_path).suffix.lower()
        if suffix in self.VIDEO_EXTENSIONS:
            return InputKind.VIDEO
        if suffix in self.IMAGE_EXTENSIONS:
            return InputKind.IMAGE
        return None

    def _set_input_path(self, input_path: str):
        normalized_path = input_path.strip()
        if not normalized_path:
            self.input_path = ""
            self.input_kind = InputKind.NONE
            self.error_message = ""
            self.video_total_frames = 1
            self.max_video_frames_input = 1
            if self.stage == Stage.ERROR:
                self.stage = Stage.IDLE
            self._reset_result_state()
            return

        detected_kind = self._detect_input_kind(normalized_path)
        if normalized_path != self.input_path:
            self._reset_result_state()

        self.input_path = normalized_path
        if detected_kind is None:
            self.input_kind = InputKind.NONE
            self.error_message = "Unsupported file type. Use .mp4 or a supported image file."
            return

        self.input_kind = detected_kind
        self.error_message = ""
        if self.input_kind == InputKind.VIDEO:
            self._sync_video_metadata()
        else:
            self.video_total_frames = 1
            self.max_video_frames_input = 1
        if self._try_autoload_existing_output():
            return
        if self.stage == Stage.ERROR:
            self.stage = Stage.IDLE

    def _reset_result_state(self):
        self.ply_files = []
        self.frame_cache.clear()
        self.current_frame_idx = 0
        self._last_displayed_frame_idx = None
        self.is_playing = False
        self.image_complete_message = ""

    def _selected_video_frame_limit(self) -> int:
        if self.video_total_frames < 1:
            self.video_total_frames = 1
        if self.max_video_frames_input < 1:
            self.max_video_frames_input = 1
        if self.max_video_frames_input > self.video_total_frames:
            self.max_video_frames_input = self.video_total_frames
        return self.max_video_frames_input

    def _sync_video_metadata(self):
        if self.input_kind != InputKind.VIDEO or not self.input_path:
            self.video_total_frames = 1
            self.max_video_frames_input = 1
            return

        try:
            fps, total_frames = sharp_processor.probe_video_metadata(self.input_path)
            if total_frames < 1:
                raise RuntimeError("Unable to detect total frame count for this video")
            self.source_fps = fps if fps > 0 else self.source_fps
            self.video_total_frames = total_frames
            if self.max_video_frames_input < 1 or self.max_video_frames_input > total_frames:
                self.max_video_frames_input = total_frames
        except Exception as e:
            self.video_total_frames = 1
            self.max_video_frames_input = 1
            lf.log.warning(f"Could not read video metadata for slider bounds: {e}")

    def _try_autoload_existing_output(self) -> bool:
        frame_limit = self._selected_video_frame_limit() if self.input_kind == InputKind.VIDEO else None
        loaded = self._load_existing_output(frame_limit=frame_limit)
        if loaded:
            output_dir = self._expected_output_dir()
            if output_dir:
                lf.log.info(f"Auto-loaded existing output from {output_dir}")
        return loaded

    def _load_existing_output(self, frame_limit: Optional[int] = None) -> bool:
        ply_files = self._existing_output_files()
        if not ply_files:
            return False

        if self.input_kind == InputKind.VIDEO and frame_limit is not None:
            frame_limit = max(1, frame_limit)
            ply_files = ply_files[:frame_limit]

        if not ply_files:
            return False

        self.ply_files = ply_files
        self.current_frame_idx = 0
        self._last_displayed_frame_idx = 0
        self.frame_cache.clear()
        self.last_frame_time = time.time()

        if self.input_kind == InputKind.VIDEO:
            self.is_playing = len(self.ply_files) > 1
            self.stage = Stage.PLAYING if self.is_playing else Stage.IDLE
            self.image_complete_message = ""
        else:
            self.is_playing = False
            self.stage = Stage.IDLE
            self.image_complete_message = "Processing completed."

        self._update_scene_frame(0)
        threading.Thread(target=self._preload_frames, daemon=True).start()
        return True

    def _existing_output_count(self) -> int:
        return len(self._existing_output_files())

    def _existing_output_files(self) -> List[str]:
        output_dir = self._expected_output_dir()
        if output_dir is None or not output_dir.exists() or not output_dir.is_dir():
            return []

        if self.input_kind == InputKind.VIDEO:
            frame_files = sorted(output_dir.glob("frame_*.ply"))
            if frame_files:
                return [str(p) for p in frame_files]
            return [str(p) for p in sorted(output_dir.glob("*.ply"))]

        if self.input_kind == InputKind.IMAGE:
            input_file = Path(self.input_path)
            exact = output_dir / f"{input_file.stem}.ply"
            if exact.exists():
                return [str(exact)]
            all_ply = sorted(output_dir.glob("*.ply"))
            if all_ply:
                return [str(all_ply[0])]
        return []

    def _expected_output_dir(self) -> Optional[Path]:
        if self.input_kind not in {InputKind.VIDEO, InputKind.IMAGE} or not self.input_path:
            return None
        input_file = Path(self.input_path)
        return input_file.parent / f"{input_file.stem}_gaussians"

    def _stage_style(self):
        if self.stage == Stage.PROCESSING:
            return "Processing", (0.30, 0.70, 1.0, 1.0)
        if self.stage == Stage.PLAYING:
            return "Playing", (0.20, 0.80, 0.45, 1.0)
        if self.stage == Stage.ERROR:
            return "Error", (1.0, 0.25, 0.25, 1.0)
        return "Idle", (0.70, 0.70, 0.75, 1.0)

    def _preload_frames(self):
        count = 0
        for p in self.ply_files:
            if count >= self.cache_limit: break
            if p not in self.frame_cache:
                try:
                    self.frame_cache[p] = sharp_processor.load_gaussian_ply(p)
                    count += 1
                except:
                    pass


    def _update_scene_frame(self, idx, node_name=None):
        if not self.ply_files:
            return

        node_name = node_name or "Sharp4D"
        path = Path(self.ply_files[idx])

        try:
            result = lf.io.load(str(path))
            splat = result.splat_data
            if splat is None:
                raise RuntimeError("No splat data returned")
        except Exception as e:
            lf.log.error(f"Failed to load splat frame {path}: {e}")
            return

        scene = lf.get_scene()
        if scene is None:
            lf.log.error("No active scene available.")
            self.stage = Stage.ERROR
            return

        new_node_name = f"{node_name}__next"

        if self.input_kind == InputKind.VIDEO:
            lf.log.info(f"Adding frame {idx+1}/{len(self.ply_files)}: {path}")
        else:
            lf.log.info(f"Displaying processed output: {path}")

        scene.add_splat(
            name=new_node_name,
            means=splat.means_raw,
            sh0=splat.sh0_raw,
            shN=splat.shN_raw,
            scaling=splat.scaling_raw,
            rotation=splat.rotation_raw,
            opacity=splat.opacity_raw,
            sh_degree=splat.active_sh_degree,
            scene_scale=splat.scene_scale,
        )

        old_node = scene.get_node(node_name)
        if old_node:
            scene.remove_node(old_node.name)

        scene.rename_node(new_node_name, node_name)
        scene.invalidate_cache()
