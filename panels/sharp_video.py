# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sharp 4D Video Panel."""

import ssl

ssl._create_default_https_context = ssl._create_unverified_context

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional

import lichtfeld as lf

from .. import sharp_processor
try:
    from lfs_plugins import ScrubFieldController, ScrubFieldSpec
except ImportError:
    from lfs_plugins.scrub_fields import ScrubFieldController, ScrubFieldSpec


SCRUB_FIELD_SPECS = {
    "max_video_frames_input": ScrubFieldSpec(
        min_value=1.0,
        max_value=1.0,
        step=1.0,
        fmt="%d",
        data_type=int,
    ),
    "playback_fps": ScrubFieldSpec(
        min_value=1.0,
        max_value=120.0,
        step=1.0,
        fmt="%.1f",
        data_type=float,
    ),
    "current_frame_idx": ScrubFieldSpec(
        min_value=0.0,
        max_value=0.0,
        step=1.0,
        fmt="%d",
        data_type=int,
    ),
}


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
        self.result: Optional[ProcessResult] = None
        self._thread: Optional[threading.Thread] = None
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

                video_path = Path(self.input_path)
                out_dir = video_path.parent / f"{video_path.stem}_gaussians"
                files, fps = processor.process_video(
                    self.input_path,
                    str(out_dir),
                    prog_cb,
                    max_frames=self.max_video_frames,
                )
                del processor
            elif self.input_kind == InputKind.IMAGE:
                processor = sharp_processor.SharpProcessor()

                def prog_cb(i, total, msg):
                    with self._lock:
                        self.status = msg
                        if total > 0:
                            self.progress = (i / total) * 100

                image_path = Path(self.input_path)
                out_dir = image_path.parent / f"{image_path.stem}_gaussians"
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
        except Exception as exc:
            logging.error("Processing failed: %s", exc)
            with self._lock:
                self.result = ProcessResult(False, error=str(exc))
                self.status = f"Error: {exc}"
            callback(self.result)


class SharpVideoPanel(lf.ui.Panel):
    id = "sharp_4d.video_panel"
    label = "Sharp 4D Video"
    space = lf.ui.PanelSpace.MAIN_PANEL_TAB
    order = 10000
    template = str(Path(__file__).resolve().with_name("sharp_video.rml"))
    height_mode = lf.ui.PanelHeightMode.CONTENT
    update_interval_ms = 33

    VIDEO_EXTENSIONS = {".mp4"}
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".heic"}

    def __init__(self):
        self.input_path = ""
        self.input_kind = InputKind.NONE

        self.job: Optional[ProcessingJob] = None
        self.ply_files: List[str] = []
        self.playback_fps = 30.0
        self.current_frame_idx = 0
        self.last_frame_time = 0.0
        self.is_playing = False
        self._last_displayed_frame_idx: Optional[int] = None

        self.frame_cache = {}
        self.cache_limit = 150

        self.stage = Stage.IDLE
        self.error_message = ""
        self.source_fps = 30.0
        self.video_total_frames = 1
        self.image_complete_message = ""
        self.max_video_frames_input = 1
        self.cached_output_count = 0

        self._handle = None
        self._doc = None
        self._last_ui_signature = None
        self._pending_result: Optional[ProcessResult] = None
        self._pending_lock = threading.Lock()
        self._scrub_fields = ScrubFieldController(
            specs=SCRUB_FIELD_SPECS,
            get_value=self._get_scrub_field_value,
            set_value=self._set_scrub_field_value,
        )

    def draw(self, ui):
        del ui

    def on_mount(self, doc):
        self._doc = doc
        self._scrub_fields.mount(doc)

    def on_bind_model(self, ctx):
        model = ctx.create_data_model("sharp_video")
        if model is None:
            return

        model.bind_func("is_stage_idle", lambda: self.stage == Stage.IDLE)
        model.bind_func("is_stage_processing", lambda: self.stage == Stage.PROCESSING)
        model.bind_func("is_stage_playing", lambda: self.stage == Stage.PLAYING)
        model.bind_func("is_stage_error", lambda: self.stage == Stage.ERROR)

        model.bind_func("detected_type_text", self._detected_type_text)
        model.bind_func("input_path_text", lambda: self.input_path or "No media file selected")
        model.bind_func("show_video_config", lambda: self.input_kind == InputKind.VIDEO)
        model.bind_func("show_image_hint", lambda: self.input_kind == InputKind.IMAGE)
        model.bind_func("show_unsupported_hint", lambda: bool(self.input_path) and self.input_kind == InputKind.NONE)

        model.bind(
            "max_video_frames_input",
            lambda: str(self._selected_video_frame_limit()),
            self._set_max_video_frames_input,
        )
        model.bind_func("video_total_frames_max", lambda: str(max(1, self.video_total_frames)))
        model.bind_func("frame_limit_text", self._frame_limit_text)
        model.bind(
            "playback_fps",
            lambda: f"{self.playback_fps:.1f}",
            self._set_playback_fps,
        )
        model.bind_func("source_fps_text", lambda: f"Detected Source FPS: {self.source_fps:.2f}")
        model.bind_func("video_total_frames_text", lambda: f"Total Frames in Video: {self.video_total_frames}")

        model.bind_func("show_processing", lambda: self.stage == Stage.PROCESSING)
        model.bind_func("processing_status_text", self._job_status_text)
        model.bind_func("processing_progress_value", self._job_progress_value)
        model.bind_func("processing_progress_pct", self._job_progress_pct)

        model.bind_func("show_actions", lambda: self.stage in {Stage.IDLE, Stage.ERROR})
        model.bind_func("show_load_cached", lambda: self.input_kind == InputKind.VIDEO and self.cached_output_count > 0)
        model.bind_func("load_cached_label", self._load_cached_label)
        model.bind_func("process_button_label", self._process_button_label)
        model.bind_func("show_error", lambda: self.stage == Stage.ERROR and bool(self.error_message))
        model.bind_func("error_text", lambda: self.error_message)

        model.bind_func("show_video_result", lambda: bool(self.ply_files) and self.input_kind == InputKind.VIDEO)
        model.bind_func("result_frames_text", lambda: f"Frames: {len(self.ply_files)}")
        model.bind_func("play_button_label", lambda: "Pause" if self.is_playing else "Play")
        model.bind_func("show_play_controls", lambda: len(self.ply_files) > 1)
        model.bind(
            "current_frame_idx",
            lambda: str(self.current_frame_idx if self.ply_files else 0),
            self._set_current_frame_idx,
        )
        model.bind_func("playback_frame_slider_max", lambda: str(max(0, len(self.ply_files) - 1)))
        model.bind_func("current_frame_label", self._current_frame_label)

        model.bind_func("show_image_result", lambda: bool(self.ply_files) and self.input_kind == InputKind.IMAGE)
        model.bind_func("image_complete_message", lambda: self.image_complete_message)

        model.bind_event("select_video", self._on_select_video)
        model.bind_event("select_image", self._on_select_image)
        model.bind_event("clear_input", self._on_clear_input)
        model.bind_event("load_cached_output", self._on_load_cached_output)
        model.bind_event("do_process_media", self._on_process_media)
        model.bind_event("toggle_playback", self._on_toggle_playback)
        model.bind_event("reset_frame", self._on_reset_frame)

        self._handle = model.get_handle()

    def on_update(self, doc):
        del doc
        dirty = self._consume_pending_result()
        if self._tick_playback():
            dirty = True
        if self._sync_scrub_specs():
            dirty = True
        if self._scrub_fields.sync_all():
            dirty = True

        signature = self._ui_signature()
        if signature != self._last_ui_signature:
            self._last_ui_signature = signature
            dirty = True

        if dirty:
            self._dirty()
        return dirty

    def on_unmount(self, doc):
        if doc is not None:
            doc.remove_data_model("sharp_video")
        self._scrub_fields.unmount()
        self._handle = None
        self._doc = None
        self._last_ui_signature = None

    def _dirty(self, *fields):
        if self._handle is None:
            return
        if not fields:
            self._handle.dirty_all()
            return
        for field in fields:
            self._handle.dirty(field)

    def _detected_type_text(self) -> str:
        if self.input_kind == InputKind.NONE:
            return "not detected"
        return self.input_kind.value

    def _get_scrub_field_value(self, prop: str) -> float:
        if prop == "max_video_frames_input":
            return float(self._selected_video_frame_limit())
        if prop == "playback_fps":
            return float(self.playback_fps)
        if prop == "current_frame_idx":
            return float(self.current_frame_idx)
        raise KeyError(prop)

    def _set_scrub_field_value(self, prop: str, value: float) -> None:
        if prop == "max_video_frames_input":
            self._set_max_video_frames_input(value)
            return
        if prop == "playback_fps":
            self._set_playback_fps(value)
            return
        if prop == "current_frame_idx":
            self._set_current_frame_idx(value)
            return
        raise KeyError(prop)

    def _sync_scrub_specs(self) -> bool:
        changed = False

        changed |= self._update_scrub_spec(
            "max_video_frames_input",
            max_value=float(max(1, self.video_total_frames)),
        )
        changed |= self._update_scrub_spec(
            "current_frame_idx",
            max_value=float(max(0, len(self.ply_files) - 1)),
        )

        return changed

    def _update_scrub_spec(self, prop: str, *, max_value: float) -> bool:
        current_spec = self._scrub_fields._specs[prop]
        if abs(current_spec.max_value - max_value) <= 1.0e-9:
            return False

        next_spec = ScrubFieldSpec(
            min_value=current_spec.min_value,
            max_value=max_value,
            step=current_spec.step,
            fmt=current_spec.fmt,
            data_type=current_spec.data_type,
            pixels_per_step=current_spec.pixels_per_step,
        )
        self._scrub_fields._specs[prop] = next_spec

        state = self._scrub_fields._fields.get(prop)
        if state is not None:
            state.spec = next_spec

        return True

    def _frame_limit_text(self) -> str:
        return f"Frame Limit: {self._selected_video_frame_limit()}/{self.video_total_frames} frames will be converted."

    def _job_status_text(self) -> str:
        if self.job is None:
            return "Initializing..."
        with self.job._lock:
            return self.job.status or "Initializing..."

    def _job_progress_value(self) -> str:
        if self.job is None:
            return "0"
        with self.job._lock:
            progress = max(0.0, min(100.0, self.job.progress))
        return f"{progress / 100.0:.4f}"

    def _job_progress_pct(self) -> str:
        if self.job is None:
            return "0.0%"
        with self.job._lock:
            progress = max(0.0, min(100.0, self.job.progress))
        return f"{progress:.1f}%"

    def _load_cached_label(self) -> str:
        load_count = min(self._selected_video_frame_limit(), self.cached_output_count)
        return f"Load {load_count} Frames From Disk"

    def _process_button_label(self) -> str:
        if self.input_kind == InputKind.VIDEO:
            return f"Process {self._selected_video_frame_limit()} Frames"
        if self.input_kind == InputKind.IMAGE:
            return "Process Image"
        return "Process Media"

    def _current_frame_label(self) -> str:
        if not self.ply_files:
            return "Frame"
        return f"Frame {self.current_frame_idx + 1}/{len(self.ply_files)}"

    def _ui_signature(self):
        return (
            self.stage.value,
            self.input_path,
            self.input_kind.value,
            self.error_message,
            self.image_complete_message,
            self.max_video_frames_input,
            self.video_total_frames,
            round(self.playback_fps, 3),
            round(self.source_fps, 3),
            len(self.ply_files),
            self.current_frame_idx,
            self.is_playing,
            self.cached_output_count,
            self._job_signature(),
        )

    def _job_signature(self):
        if self.job is None:
            return None
        with self.job._lock:
            return (
                round(self.job.progress, 2),
                self.job.status,
                self.job.result.success if self.job.result is not None else None,
            )

    def _consume_pending_result(self) -> bool:
        with self._pending_lock:
            result = self._pending_result
            self._pending_result = None
        if result is None:
            return False
        self._apply_result(result)
        return True

    def _apply_result(self, result: ProcessResult):
        self.job = None
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
            self._refresh_cached_output_state()
            if self.ply_files:
                self._update_scene_frame(0)

            threading.Thread(target=self._preload_frames, daemon=True).start()
        else:
            self.error_message = result.error or "Processing failed"
            self.stage = Stage.ERROR
            self._refresh_cached_output_state()

    def _tick_playback(self) -> bool:
        if not self.is_playing or not self.ply_files:
            return False

        frame_duration = 1.0 / max(1.0, self.playback_fps)
        now = time.time()
        if now - self.last_frame_time < frame_duration:
            return False

        elapsed = max(0.0, now - self.last_frame_time)
        steps = max(1, int(elapsed / frame_duration))
        self.current_frame_idx = (self.current_frame_idx + steps) % len(self.ply_files)
        self._update_scene_frame(self.current_frame_idx)
        self._last_displayed_frame_idx = self.current_frame_idx
        self.last_frame_time = now
        return True

    def _on_select_video(self, handle, event, args):
        del handle, event, args
        selected = lf.ui.open_video_file_dialog()
        if selected:
            self._set_input_path(selected)
            self._dirty()

    def _on_select_image(self, handle, event, args):
        del handle, event, args
        selected = lf.ui.open_image_dialog(self._input_start_dir())
        if selected:
            self._set_input_path(selected)
            self._dirty()

    def _on_clear_input(self, handle, event, args):
        del handle, event, args
        self._set_input_path("")
        self._dirty()

    def _on_load_cached_output(self, handle, event, args):
        del handle, event, args
        loaded = self._load_existing_output(frame_limit=self._selected_video_frame_limit())
        if not loaded:
            self.error_message = "Could not load cached frames from disk."
            self.stage = Stage.ERROR
        self._dirty()

    def _on_process_media(self, handle, event, args):
        del handle, event, args
        self._start_processing()
        self._dirty()

    def _on_toggle_playback(self, handle, event, args):
        del handle, event, args
        if len(self.ply_files) <= 1 or self.input_kind != InputKind.VIDEO:
            return
        self.is_playing = not self.is_playing
        self.stage = Stage.PLAYING if self.is_playing else Stage.IDLE
        self.last_frame_time = time.time()
        self._dirty()

    def _on_reset_frame(self, handle, event, args):
        del handle, event, args
        if not self.ply_files:
            return
        self.current_frame_idx = 0
        self._update_scene_frame(0)
        self._last_displayed_frame_idx = 0
        self.last_frame_time = time.time()
        self._dirty()

    def _set_max_video_frames_input(self, value):
        try:
            frame_limit = int(float(value))
        except (TypeError, ValueError):
            return
        frame_limit = max(1, min(frame_limit, max(1, self.video_total_frames)))
        if frame_limit == self.max_video_frames_input:
            return
        self.max_video_frames_input = frame_limit
        self._dirty("max_video_frames_input", "frame_limit_text")

    def _set_playback_fps(self, value):
        try:
            fps = float(value)
        except (TypeError, ValueError):
            return
        fps = max(1.0, min(fps, 120.0))
        if abs(fps - self.playback_fps) < 1e-6:
            return
        self.playback_fps = fps
        self.last_frame_time = time.time()
        self._dirty("playback_fps")

    def _set_current_frame_idx(self, value):
        if not self.ply_files:
            return
        try:
            idx = int(float(value))
        except (TypeError, ValueError):
            return
        idx = max(0, min(idx, len(self.ply_files) - 1))
        if idx == self.current_frame_idx and idx == self._last_displayed_frame_idx:
            return
        self.current_frame_idx = idx
        self._update_scene_frame(idx)
        self._last_displayed_frame_idx = idx
        self.last_frame_time = time.time()
        self._dirty("current_frame_idx", "current_frame_label")

    def _start_processing(self):
        self.error_message = ""
        self.image_complete_message = ""
        with self._pending_lock:
            self._pending_result = None

        if not self.input_path.strip():
            self.error_message = "Please select a video or image file."
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
        self.job.start(self._on_job_complete)

    def _on_job_complete(self, result: ProcessResult):
        with self._pending_lock:
            self._pending_result = result

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
            self.cached_output_count = 0
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
            self.cached_output_count = 0
            return

        self.input_kind = detected_kind
        self.error_message = ""
        if self.input_kind == InputKind.VIDEO:
            self._sync_video_metadata()
        else:
            self.video_total_frames = 1
            self.max_video_frames_input = 1
        self._refresh_cached_output_state()
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
        except Exception as exc:
            self.video_total_frames = 1
            self.max_video_frames_input = 1
            lf.log.warning(f"Could not read video metadata for slider bounds: {exc}")

    def _refresh_cached_output_state(self):
        self.cached_output_count = self._existing_output_count()

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

        self._refresh_cached_output_state()
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
                return [str(path) for path in frame_files]
            return [str(path) for path in sorted(output_dir.glob("*.ply"))]

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

    def _preload_frames(self):
        count = 0
        for ply_path in self.ply_files:
            if count >= self.cache_limit:
                break
            if ply_path in self.frame_cache:
                continue
            try:
                self.frame_cache[ply_path] = sharp_processor.load_gaussian_ply(ply_path)
                count += 1
            except Exception:
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
        except Exception as exc:
            lf.log.error(f"Failed to load splat frame {path}: {exc}")
            self.error_message = f"Failed to load frame: {path.name}"
            self.stage = Stage.ERROR
            self.is_playing = False
            return

        scene = lf.get_scene()
        if scene is None:
            lf.log.error("No active scene available.")
            self.error_message = "No active scene available."
            self.stage = Stage.ERROR
            self.is_playing = False
            return

        new_node_name = f"{node_name}__next"

        if self.input_kind == InputKind.VIDEO:
            lf.log.info(f"Adding frame {idx + 1}/{len(self.ply_files)}: {path}")
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
