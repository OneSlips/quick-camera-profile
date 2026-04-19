"""Quick Camera Profile - GUI

Single-window CustomTkinter application for one-click camera profiling.
"""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk
from licensing import LemonLicenseManager

from engine import (
    CHARTS,
    ILLUMINANTS,
    RAW_FILTER_PAIRS,
    TARGETS,
    ProfileEngine,
    __version__,
)

# ── Constants ───────────────────────────────────────────────────────────

CANVAS_W = 920
CANVAS_H = 500
WIN_W = 980
WIN_H = 920


# ── Application ─────────────────────────────────────────────────────────


class QuickProfileApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(f"Quick Camera Profile  v{__version__}")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.minsize(800, 700)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # ── state ────────────────────────────────────────────────────
        self.raw_path: str | None = None
        self.raw_info: dict | None = None
        self._preview_photo: ImageTk.PhotoImage | None = None
        self._img_offset = (0, 0)
        self._img_display_size = (0, 0)
        self._img_full_size = (0, 0)
        self._preview_pil: Image.Image | None = None
        self._view_zoom = 1.0
        self._view_pan = (0.0, 0.0)
        self._drag_mode: str | None = None  # crop | pan | pin
        self._drag_pin_idx: int | None = None
        self._pan_start_xy: tuple[int, int] | None = None
        self._pan_start_offset: tuple[float, float] | None = None
        self._crop_start: tuple[int, int] | None = None
        self._crop_rect_id: int | None = None
        self._crop_display: tuple[int, int, int, int] | None = None
        self._processing = False
        self._preview_rgb = None          # raw preview array (for detection)
        self._chart_box: list | None = None        # 4 corners in full-res coords
        self._chart_box_preview: list | None = None  # 4 corners in preview coords
        self._chart_overlay_ids: list[int] = []    # canvas item ids
        self._licensed = False
        self._license_message = "License required"
        self._license_manager = LemonLicenseManager()

        # ── engine ───────────────────────────────────────────────────
        self.engine: ProfileEngine | None = None
        self._init_engine()

        # ── UI ───────────────────────────────────────────────────────
        self._build_ui()
        self._init_license_flow()

        # close handler
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── engine init ──────────────────────────────────────────────────

    def _init_engine(self):
        try:
            self.engine = ProfileEngine(log=self._log_safe)
        except FileNotFoundError as e:
            self.engine = None
            err_msg = str(e)
            self.after(200, lambda: self._show_tool_error(err_msg))

    def _show_tool_error(self, msg: str):
        messagebox.showwarning(
            "Tools not found",
            f"{msg}\n\nThe app will open but you cannot create profiles "
            "until the required tools are available.",
        )
        self._append_log(f"WARNING: {msg}")

    # ── logging ──────────────────────────────────────────────────────

    def _log_safe(self, msg: str):
        """Thread-safe logging — schedules update on the main thread."""
        self.after(0, self._append_log, msg)

    def _append_log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def _init_license_flow(self):
        """Validate existing activation and lock app until licensed."""
        status = self._license_manager.validate()
        self._licensed = status.licensed
        self._license_message = status.message
        self._update_license_ui()
        if not self._licensed:
            self.after(250, self._show_license_dialog)

    def _update_license_ui(self):
        if self._licensed:
            msg = "License: active"
            key = self._license_manager.current_key()
            if key:
                suffix = key[-6:] if len(key) > 6 else key
                msg = f"License: active (…{suffix})"
            self.license_status_label.configure(text=msg, text_color="#2FA572")
            if self.engine is not None:
                self.browse_btn.configure(state="normal")
        else:
            self.license_status_label.configure(text="License: required", text_color="#FFB347")
            self.browse_btn.configure(state="disabled")
            self.create_btn.configure(state="disabled")
            self.detect_btn.configure(state="disabled")

    def _show_license_dialog(self):
        """Modal license dialog (no-trial policy)."""
        win = ctk.CTkToplevel(self)
        win.title("License Activation")
        win.geometry("560x320")
        win.resizable(False, False)
        win.grab_set()

        ctk.CTkLabel(
            win,
            text="A valid Lemon Squeezy license is required to use Quick Camera Profile.",
            wraplength=520,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=20, pady=(20, 8))

        key_var = ctk.StringVar(value=self._license_manager.current_key())
        key_entry = ctk.CTkEntry(win, textvariable=key_var, placeholder_text="Enter license key")
        key_entry.pack(fill="x", padx=20, pady=(4, 8))

        status_var = ctk.StringVar(value=self._license_message)
        status_lbl = ctk.CTkLabel(win, textvariable=status_var, text_color="#A6C8FF", anchor="w")
        status_lbl.pack(fill="x", padx=20, pady=(0, 8))

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=8)

        def set_busy(on: bool):
            state = "disabled" if on else "normal"
            activate_btn.configure(state=state)
            validate_btn.configure(state=state)
            deactivate_btn.configure(state=state)
            close_btn.configure(state=state)

        def run_bg(work_fn):
            set_busy(True)

            def _worker():
                status = work_fn()

                def _done():
                    self._licensed = status.licensed
                    self._license_message = status.message
                    status_var.set(status.message)
                    status_lbl.configure(text_color="#2FA572" if status.licensed else "#FFB347")
                    self._update_license_ui()
                    if self._licensed:
                        messagebox.showinfo("License", "Activation successful.")
                        win.destroy()
                    else:
                        set_busy(False)

                self.after(0, _done)

            threading.Thread(target=_worker, daemon=True).start()

        def do_activate():
            run_bg(lambda: self._license_manager.activate(key_var.get().strip()))

        def do_validate():
            run_bg(self._license_manager.validate)

        def do_deactivate():
            run_bg(self._license_manager.deactivate)

        activate_btn = ctk.CTkButton(btn_row, text="Activate", command=do_activate, fg_color="#2FA572")
        activate_btn.pack(side="left")
        validate_btn = ctk.CTkButton(btn_row, text="Validate", command=do_validate)
        validate_btn.pack(side="left", padx=(8, 0))
        deactivate_btn = ctk.CTkButton(btn_row, text="Deactivate", command=do_deactivate, fg_color="#A14A4A")
        deactivate_btn.pack(side="left", padx=(8, 0))

        close_btn = ctk.CTkButton(btn_row, text="Close", command=win.destroy)
        close_btn.pack(side="right")

        ctk.CTkLabel(
            win,
            text=(
                "If activation fails, verify internet access and your Lemon Squeezy key.\n"
                "No trial mode is enabled in this build."
            ),
            text_color="#9099AA",
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=20, pady=(8, 0))

    # ── build UI ─────────────────────────────────────────────────────

    def _build_ui(self):
        pad = 15

        # ── file picker ──────────────────────────────────────────────
        file_frame = ctk.CTkFrame(self)
        file_frame.pack(fill="x", padx=pad, pady=(pad, 5))

        ctk.CTkLabel(
            file_frame, text="RAW File", width=70, anchor="w"
        ).pack(side="left", padx=(10, 5))

        self.file_entry = ctk.CTkEntry(
            file_frame, placeholder_text="Select a camera RAW file ..."
        )
        self.file_entry.pack(side="left", fill="x", expand=True, padx=5)

        self.browse_btn = ctk.CTkButton(
            file_frame, text="Browse", width=80, command=self._browse
        )
        self.browse_btn.pack(side="left", padx=(5, 10))

        self.license_btn = ctk.CTkButton(
            file_frame, text="License", width=90,
            command=self._show_license_dialog,
        )
        self.license_btn.pack(side="right", padx=(5, 10))

        self.license_status_label = ctk.CTkLabel(
            file_frame, text="License: required", width=260, anchor="e"
        )
        self.license_status_label.pack(side="right", padx=(5, 5))

        # ── camera info ──────────────────────────────────────────────
        cam_frame = ctk.CTkFrame(self)
        cam_frame.pack(fill="x", padx=pad, pady=5)

        ctk.CTkLabel(
            cam_frame, text="Camera", width=70, anchor="w"
        ).pack(side="left", padx=(10, 5))

        self.camera_entry = ctk.CTkEntry(
            cam_frame, placeholder_text="(auto-detected from file)"
        )
        self.camera_entry.pack(side="left", fill="x", expand=True, padx=5)

        self.dims_label = ctk.CTkLabel(cam_frame, text="", width=140)
        self.dims_label.pack(side="left", padx=(5, 10))

        # ── preview canvas ───────────────────────────────────────────
        canvas_frame = ctk.CTkFrame(self)
        canvas_frame.pack(fill="x", padx=pad, pady=5)

        self.canvas = tk.Canvas(
            canvas_frame,
            width=CANVAS_W,
            height=CANVAS_H,
            bg="#1a1a1a",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(padx=2, pady=2)

        # empty-state hint
        self.canvas.create_text(
            CANVAS_W // 2,
            CANVAS_H // 2,
            text="Load a RAW file to begin",
            fill="#555555",
            font=("Segoe UI", 16),
        )

        # canvas mouse bindings
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-2>", self._on_middle_press)
        self.canvas.bind("<B2-Motion>", self._on_middle_drag)
        self.canvas.bind("<ButtonRelease-2>", self._on_middle_release)
        self.canvas.bind("<ButtonPress-3>", self._on_right_click)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._on_mouse_wheel_linux(1, e))
        self.canvas.bind("<Button-5>", lambda e: self._on_mouse_wheel_linux(-1, e))

        # crop hint + detect button row
        hint_frame = ctk.CTkFrame(self, fg_color="transparent")
        hint_frame.pack(fill="x", padx=pad + 5, pady=(0, 5))

        self.crop_hint = ctk.CTkLabel(
            hint_frame, text="", text_color="#888888", anchor="w",
            font=ctk.CTkFont(size=12),
        )
        self.crop_hint.pack(side="left", fill="x", expand=True)

        self.detect_btn = ctk.CTkButton(
            hint_frame, text="Detect Chart", width=120,
            fg_color="#3A7EBF", hover_color="#2B5F8E",
            command=self._detect_chart,
            state="disabled",
        )
        self.detect_btn.pack(side="right", padx=(10, 0))

        self.zoom_reset_btn = ctk.CTkButton(
            hint_frame, text="1:1", width=52,
            command=self._reset_view,
        )
        self.zoom_reset_btn.pack(side="right", padx=(6, 0))

        self.zoom_out_btn = ctk.CTkButton(
            hint_frame, text="-", width=36,
            command=lambda: self._zoom_at_canvas(CANVAS_W // 2, CANVAS_H // 2, 1 / 1.2),
        )
        self.zoom_out_btn.pack(side="right", padx=(6, 0))

        self.zoom_in_btn = ctk.CTkButton(
            hint_frame, text="+", width=36,
            command=lambda: self._zoom_at_canvas(CANVAS_W // 2, CANVAS_H // 2, 1.2),
        )
        self.zoom_in_btn.pack(side="right", padx=(6, 0))

        # ── settings ─────────────────────────────────────────────────
        settings = ctk.CTkFrame(self)
        settings.pack(fill="x", padx=pad, pady=5)

        # dropdown maps
        self._chart_labels = [CHARTS[k][0] for k in CHARTS]
        self._chart_map = {CHARTS[k][0]: k for k in CHARTS}

        self._illum_labels = [label for _, label in ILLUMINANTS]
        self._illum_map = {label: code for code, label in ILLUMINANTS}

        self._target_labels = [label for _, label in TARGETS]
        self._target_map = {label: key for key, label in TARGETS}

        lbl_w = 60
        menu_w = 300

        # row 0 — chart + illuminant
        ctk.CTkLabel(
            settings, text="Chart:", width=lbl_w, anchor="e"
        ).grid(row=0, column=0, padx=(10, 5), pady=8)

        self.chart_var = ctk.StringVar(value=self._chart_labels[0])
        ctk.CTkOptionMenu(
            settings, variable=self.chart_var,
            values=self._chart_labels, width=menu_w,
        ).grid(row=0, column=1, padx=5, pady=8)

        ctk.CTkLabel(
            settings, text="Light:", width=lbl_w, anchor="e"
        ).grid(row=0, column=2, padx=(15, 5), pady=8)

        self.illum_var = ctk.StringVar(value=self._illum_labels[1])  # D55
        ctk.CTkOptionMenu(
            settings, variable=self.illum_var,
            values=self._illum_labels, width=menu_w,
        ).grid(row=0, column=3, padx=5, pady=8)

        # row 1 — target + auto-install
        ctk.CTkLabel(
            settings, text="Output:", width=lbl_w, anchor="e"
        ).grid(row=1, column=0, padx=(10, 5), pady=8)

        self.target_var = ctk.StringVar(value=self._target_labels[0])
        ctk.CTkOptionMenu(
            settings, variable=self.target_var,
            values=self._target_labels, width=menu_w,
        ).grid(row=1, column=1, padx=5, pady=8)

        self.install_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            settings,
            text="Auto-install to application",
            variable=self.install_var,
        ).grid(row=1, column=2, columnspan=2, padx=15, pady=8, sticky="w")

        # row 2 — profile name
        ctk.CTkLabel(
            settings, text="Name:", width=lbl_w, anchor="e"
        ).grid(row=2, column=0, padx=(10, 5), pady=8)

        self.profile_name_entry = ctk.CTkEntry(
            settings, placeholder_text="DCamProf  (custom profile name)",
            width=menu_w,
        )
        self.profile_name_entry.grid(row=2, column=1, columnspan=3, padx=5, pady=8, sticky="w")

        # ── create button ────────────────────────────────────────────
        self.create_btn = ctk.CTkButton(
            self,
            text="CREATE PROFILE",
            height=48,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2FA572",
            hover_color="#248A5E",
            command=self._create_profile,
            state="disabled",
        )
        self.create_btn.pack(fill="x", padx=pad, pady=10)

        # ── log area ─────────────────────────────────────────────────
        self.log_text = ctk.CTkTextbox(
            self,
            height=180,
            font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled",
            wrap="word",
        )
        self.log_text.pack(fill="both", expand=True, padx=pad, pady=(0, pad))

    # ── file selection ───────────────────────────────────────────────

    def _browse(self):
        if not self._licensed:
            self._show_license_dialog()
            return
        path = filedialog.askopenfilename(
            title="Select Camera RAW File",
            filetypes=RAW_FILTER_PAIRS,
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        if not self._licensed:
            self._show_license_dialog()
            return
        if not self.engine:
            messagebox.showerror(
                "Tools missing",
                "Required tools (dcamprof, scanin) are not available.\n"
                "Install them before loading a file.",
            )
            return

        self.raw_path = path
        self.file_entry.delete(0, "end")
        self.file_entry.insert(0, path)

        # reset crop and detection
        self._crop_display = None
        self._crop_rect_id = None
        self._chart_box = None
        self._chart_box_preview = None
        self._chart_overlay_ids = []
        self._preview_rgb = None

        # loading state
        self.canvas.delete("all")
        self.canvas.create_text(
            CANVAS_W // 2, CANVAS_H // 2,
            text="Loading preview ...",
            fill="#888888", font=("Segoe UI", 14),
        )
        self.create_btn.configure(state="disabled")
        self.crop_hint.configure(text="")

        # clear log
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        threading.Thread(
            target=self._load_preview_bg, args=(path,), daemon=True
        ).start()

    def _load_preview_bg(self, path: str):
        try:
            info = self.engine.read_raw_info(path)
            preview = self.engine.generate_preview(path)
            self.after(0, self._on_preview_ready, path, info, preview)
        except Exception as e:
            self.after(0, self._on_preview_error, str(e))

    def _on_preview_ready(self, path: str, info: dict, preview_rgb):
        self.raw_info = info
        self._preview_rgb = preview_rgb

        # camera name
        cam = f"{info['make']} {info['model']}"
        self.camera_entry.delete(0, "end")
        self.camera_entry.insert(0, cam)
        self.dims_label.configure(text=f"{info['width']} x {info['height']}")

        # show preview
        pil_img = Image.fromarray(preview_rgb)
        self._display_preview(pil_img, info["width"], info["height"])

        self.create_btn.configure(state="normal" if self._licensed else "disabled")
        self.detect_btn.configure(state="normal" if self._licensed else "disabled")
        self.crop_hint.configure(
            text=(
                "Click Detect Chart. Wheel=zoom, middle-drag=pan, drag green pins to refine."
            )
        )

        # auto-detect on load
        self._detect_chart()

    def _on_preview_error(self, error: str):
        self.canvas.delete("all")
        self.canvas.create_text(
            CANVAS_W // 2, CANVAS_H // 2,
            text=f"Could not load file:\n{error}",
            fill="#FF6B6B", font=("Segoe UI", 12), width=CANVAS_W - 80,
        )

    def _display_preview(self, pil_img: Image.Image, full_w: int, full_h: int):
        """Load preview image and render with current zoom/pan state."""
        self._img_full_size = (full_w, full_h)
        self._preview_pil = pil_img
        self._view_zoom = 1.0
        self._view_pan = (0.0, 0.0)
        self._render_preview()

    def _render_preview(self):
        """Render the preview image and overlays using current zoom/pan."""
        if self._preview_pil is None:
            return

        iw, ih = self._preview_pil.size
        fit_scale = min(CANVAS_W / iw, CANVAS_H / ih)
        scale = fit_scale * self._view_zoom
        nw = max(1, int(iw * scale))
        nh = max(1, int(ih * scale))

        px, py = self._clamp_pan(*self._view_pan, nw, nh)
        self._view_pan = (px, py)

        ox = int((CANVAS_W - nw) / 2 + px)
        oy = int((CANVAS_H - nh) / 2 + py)
        self._img_display_size = (nw, nh)
        self._img_offset = (ox, oy)

        resized = self._preview_pil.resize((nw, nh), Image.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(resized)

        self.canvas.delete("all")
        self.canvas.create_image(
            ox, oy, anchor="nw", image=self._preview_photo, tags="bg"
        )

        if self._crop_display:
            x1, y1, x2, y2 = self._crop_display
            self._crop_rect_id = self.canvas.create_rectangle(
                x1, y1, x2, y2,
                outline="#FFD700", width=2, dash=(6, 4),
            )

        if self._chart_box_preview:
            self._draw_chart_overlay(self._chart_box_preview)

    def _clamp_pan(self, pan_x: float, pan_y: float, disp_w: int, disp_h: int) -> tuple[float, float]:
        """Clamp panning so the image cannot drift far outside the canvas."""
        if disp_w <= CANVAS_W:
            pan_x = 0.0
        else:
            lim_x = (disp_w - CANVAS_W) / 2
            pan_x = max(-lim_x, min(lim_x, pan_x))

        if disp_h <= CANVAS_H:
            pan_y = 0.0
        else:
            lim_y = (disp_h - CANVAS_H) / 2
            pan_y = max(-lim_y, min(lim_y, pan_y))

        return pan_x, pan_y

    def _canvas_to_preview(self, x: float, y: float) -> tuple[float, float] | None:
        """Map canvas coordinates to preview-image pixel coordinates."""
        if self._preview_rgb is None:
            return None
        ox, oy = self._img_offset
        dw, dh = self._img_display_size
        if dw <= 0 or dh <= 0:
            return None
        px = (x - ox) * self._preview_rgb.shape[1] / dw
        py = (y - oy) * self._preview_rgb.shape[0] / dh
        return px, py

    def _preview_to_canvas(self, px: float, py: float) -> tuple[float, float]:
        """Map preview-image pixel coordinates to canvas coordinates."""
        ox, oy = self._img_offset
        dw, dh = self._img_display_size
        if self._preview_rgb is None:
            return ox, oy
        pw = self._preview_rgb.shape[1]
        ph = self._preview_rgb.shape[0]
        x = px * dw / pw + ox
        y = py * dh / ph + oy
        return x, y

    def _zoom_at_canvas(self, cx: float, cy: float, factor: float):
        """Zoom around a canvas point and preserve that point under the cursor."""
        if self._preview_pil is None:
            return
        old_zoom = self._view_zoom
        new_zoom = max(1.0, min(8.0, old_zoom * factor))
        if abs(new_zoom - old_zoom) < 1e-6:
            return

        before = self._canvas_to_preview(cx, cy)
        self._view_zoom = new_zoom
        self._render_preview()
        after = self._canvas_to_preview(cx, cy)

        if before is not None and after is not None:
            dx_px = before[0] - after[0]
            dy_px = before[1] - after[1]
            if self._preview_rgb is not None:
                dw, dh = self._img_display_size
                pw = self._preview_rgb.shape[1]
                ph = self._preview_rgb.shape[0]
                pan_dx = dx_px * dw / pw
                pan_dy = dy_px * dh / ph
                px, py = self._view_pan
                self._view_pan = (px - pan_dx, py - pan_dy)
                self._render_preview()

        if self._crop_rect_id:
            self.canvas.delete(self._crop_rect_id)
            self._crop_rect_id = None
            self._crop_display = None

    def _reset_view(self):
        if self._preview_pil is None:
            return
        self._view_zoom = 1.0
        self._view_pan = (0.0, 0.0)
        self._render_preview()

    def _on_mouse_wheel(self, event):
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        self._zoom_at_canvas(event.x, event.y, factor)

    def _on_mouse_wheel_linux(self, direction: int, event):
        factor = 1.15 if direction > 0 else 1 / 1.15
        self._zoom_at_canvas(event.x, event.y, factor)

    # ── crop interaction ─────────────────────────────────────────────

    def _on_drag(self, event):
        if self._drag_mode == "pan":
            if not self._pan_start_xy or not self._pan_start_offset:
                return
            sx, sy = self._pan_start_xy
            ox, oy = self._pan_start_offset
            self._view_pan = (ox + (event.x - sx), oy + (event.y - sy))
            self._render_preview()
            return

        if self._drag_mode == "pin":
            if (
                self._drag_pin_idx is None
                or self._chart_box_preview is None
                or len(self._chart_box_preview) == 0
                or self._preview_rgb is None
            ):
                return
            p = self._canvas_to_preview(event.x, event.y)
            if p is None:
                return
            pw = self._preview_rgb.shape[1]
            ph = self._preview_rgb.shape[0]
            px = max(0.0, min(float(pw - 1), p[0]))
            py = max(0.0, min(float(ph - 1), p[1]))
            self._chart_box_preview[self._drag_pin_idx] = [px, py]
            self._sync_chart_box_from_preview()
            self._draw_chart_overlay(self._chart_box_preview)
            return

        if self._drag_mode != "crop" or not self._crop_start:
            return
        if self._crop_rect_id:
            self.canvas.delete(self._crop_rect_id)
        x1, y1 = self._crop_start
        self._crop_rect_id = self.canvas.create_rectangle(
            x1, y1, event.x, event.y,
            outline="#FFD700", width=2, dash=(6, 4),
        )

    def _on_release(self, event):
        if self._drag_mode == "pin":
            self._drag_pin_idx = None
            self._drag_mode = None
            self.crop_hint.configure(
                text="Chart corner adjusted. Drag pins to refine, mouse wheel to zoom, middle-drag to pan.",
                text_color="#2FA572",
            )
            return

        if self._drag_mode == "pan":
            self._pan_start_xy = None
            self._pan_start_offset = None
            self._drag_mode = None
            return

        if self._drag_mode != "crop" or not self._crop_start:
            self._drag_mode = None
            return

        x1, y1 = self._crop_start
        x2, y2 = event.x, event.y
        self._crop_display = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self._crop_start = None
        self._drag_mode = None

    def _on_right_click(self, _event):
        if self._crop_rect_id:
            self.canvas.delete(self._crop_rect_id)
            self._crop_rect_id = None
            self._crop_display = None
        self._drag_mode = None
        self._drag_pin_idx = None
        self._clear_chart_overlay()
        self.crop_hint.configure(
            text="Detection cleared — click Detect Chart. Wheel=zoom, middle-drag=pan."
        )

    def _on_press(self, event):
        if not self.raw_path or self._processing:
            return
        chart_key = self._chart_map.get(self.chart_var.get(), "cc24")
        # Shift+drag acts as pan for convenience
        if event.state & 0x0001:
            self._drag_mode = "pan"
            self._pan_start_xy = (event.x, event.y)
            self._pan_start_offset = self._view_pan
            return

        # If near a chart corner pin, start pin drag
        pin_idx = self._nearest_chart_pin(event.x, event.y)
        if pin_idx is not None:
            self._drag_mode = "pin"
            self._drag_pin_idx = pin_idx
            if self._crop_rect_id:
                self.canvas.delete(self._crop_rect_id)
                self._crop_rect_id = None
                self._crop_display = None
            return

        # For CC24 we rely on auto-detection + pin refinement only.
        # Avoid switching to legacy crop mode, which can route to scanin.
        if chart_key == "cc24":
            return

        self._drag_mode = "crop"
        self._crop_start = (event.x, event.y)
        if self._crop_rect_id:
            self.canvas.delete(self._crop_rect_id)
            self._crop_rect_id = None
        # Clear chart detection when user starts a manual crop
        self._clear_chart_overlay()

    def _on_middle_press(self, event):
        if not self.raw_path or self._processing:
            return
        self._drag_mode = "pan"
        self._pan_start_xy = (event.x, event.y)
        self._pan_start_offset = self._view_pan

    def _on_middle_drag(self, event):
        self._on_drag(event)

    def _on_middle_release(self, _event):
        if self._drag_mode == "pan":
            self._drag_mode = None
            self._pan_start_xy = None
            self._pan_start_offset = None

    # ── chart detection ──────────────────────────────────────────────

    def _nearest_chart_pin(self, x: float, y: float, radius: float = 14.0) -> int | None:
        """Return index of closest corner pin near canvas point, else None."""
        if self._chart_box_preview is None or len(self._chart_box_preview) == 0:
            return None
        best_idx = None
        best_d2 = radius * radius
        for i, pt in enumerate(self._chart_box_preview):
            cx, cy = self._preview_to_canvas(float(pt[0]), float(pt[1]))
            d2 = (cx - x) ** 2 + (cy - y) ** 2
            if d2 <= best_d2:
                best_d2 = d2
                best_idx = i
        return best_idx

    def _sync_chart_box_from_preview(self):
        """Update full-resolution chart box from preview-space corner pins."""
        if (
            self._chart_box_preview is None
            or len(self._chart_box_preview) == 0
            or self._preview_rgb is None
            or self.raw_info is None
        ):
            return
        pw = self._preview_rgb.shape[1]
        ph = self._preview_rgb.shape[0]
        fw = self.raw_info["width"]
        fh = self.raw_info["height"]
        sx = fw / pw
        sy = fh / ph
        self._chart_box = [[pt[0] * sx, pt[1] * sy] for pt in self._chart_box_preview]

    def _detect_chart(self):
        """Run OpenCV MCC chart detection on the preview."""
        if self._preview_rgb is None or self.raw_info is None:
            return
        chart = self._chart_map.get(self.chart_var.get(), "cc24")
        result = ProfileEngine.detect_chart(
            self._preview_rgb,
            self.raw_info["width"],
            self.raw_info["height"],
            chart=chart,
        )
        if result is None:
            self._chart_box = None
            self._chart_box_preview = None
            self._clear_chart_overlay()
            self.crop_hint.configure(
                text="Chart not detected — adjust framing and try Detect Chart again.",
                text_color="#FF6B6B",
            )
            return

        self._chart_box = result["box"]
        self._chart_box_preview = result["box_preview"]
        # clear any manual crop
        if self._crop_rect_id:
            self.canvas.delete(self._crop_rect_id)
            self._crop_rect_id = None
            self._crop_display = None

        self._draw_chart_overlay(result["box_preview"])
        angle = result["angle"]
        self.crop_hint.configure(
            text=(
                f"Chart detected (angle {angle:.1f}°). Drag green pins to refine, "
                "wheel=zoom, middle-drag=pan, right-click=clear."
            ),
            text_color="#2FA572",
        )

    def _clear_chart_overlay(self):
        """Remove chart detection overlay from the canvas."""
        for item_id in self._chart_overlay_ids:
            self.canvas.delete(item_id)
        self._chart_overlay_ids = []
        self._chart_box = None
        self._chart_box_preview = None

    def _draw_chart_overlay(self, box_preview: list):
        """Draw the detected chart outline and patch grid on the canvas."""
        # Clear only the visual overlay, not the detection state
        for item_id in self._chart_overlay_ids:
            self.canvas.delete(item_id)
        self._chart_overlay_ids = []

        ox, oy = self._img_offset
        dw, dh = self._img_display_size

        if self._preview_rgb is None:
            return
        ph, pw = self._preview_rgb.shape[:2]
        # Scale from preview-pixel coords to display-pixel coords
        sx = dw / pw
        sy = dh / ph

        # Convert box corners to display coords
        pts = [(pt[0] * sx + ox, pt[1] * sy + oy) for pt in box_preview]

        # Draw the chart outline polygon
        flat = []
        for x, y in pts:
            flat.extend([x, y])
        item = self.canvas.create_polygon(
            *flat,
            outline="#00FF88", fill="", width=2,
        )
        self._chart_overlay_ids.append(item)

        # Draw corner markers
        for x, y in pts:
            r = 5
            item = self.canvas.create_oval(
                x - r, y - r, x + r, y + r,
                outline="#00FF88", fill="#00FF88", width=1,
            )
            self._chart_overlay_ids.append(item)

        # Draw a 6x4 patch grid inside the polygon
        import numpy as np
        corners = np.array(pts, dtype=np.float64)
        # corners order from MCC: TL(0), BL(1), BR(2), TR(3)
        # For the grid: top-left, top-right, bottom-right, bottom-left
        tl, bl, br, tr = corners[0], corners[1], corners[2], corners[3]

        rows, cols = 4, 6
        for r in range(1, rows):
            t = r / rows
            left = tl + t * (bl - tl)
            right = tr + t * (br - tr)
            item = self.canvas.create_line(
                left[0], left[1], right[0], right[1],
                fill="#00FF88", width=1, dash=(3, 3),
            )
            self._chart_overlay_ids.append(item)

        for c in range(1, cols):
            t = c / cols
            top = tl + t * (tr - tl)
            bottom = bl + t * (br - bl)
            item = self.canvas.create_line(
                top[0], top[1], bottom[0], bottom[1],
                fill="#00FF88", width=1, dash=(3, 3),
            )
            self._chart_overlay_ids.append(item)

    def _get_crop(self) -> tuple[int, int, int, int] | None:
        """Map display-space crop rectangle to full-resolution coords."""
        if not self._crop_display or not self._img_full_size:
            return None

        x1, y1, x2, y2 = self._crop_display
        ox, oy = self._img_offset
        dw, dh = self._img_display_size
        fw, fh = self._img_full_size

        # subtract image offset
        x1 -= ox
        y1 -= oy
        x2 -= ox
        y2 -= oy

        # clamp to displayed image bounds
        x1 = max(0, min(x1, dw))
        y1 = max(0, min(y1, dh))
        x2 = max(0, min(x2, dw))
        y2 = max(0, min(y2, dh))

        if x2 - x1 < 20 or y2 - y1 < 20:
            return None  # too small

        # scale to full resolution
        return (
            int(x1 * fw / dw),
            int(y1 * fh / dh),
            int(x2 * fw / dw),
            int(y2 * fh / dh),
        )

    # ── profile creation ─────────────────────────────────────────────

    def _create_profile(self):
        if not self._licensed:
            self._show_license_dialog()
            return
        if not self.raw_path or not self.engine or self._processing:
            return

        self._processing = True
        self.create_btn.configure(state="disabled", text="Working ...")

        # clear log
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        # gather settings
        chart = self._chart_map[self.chart_var.get()]
        illuminant = self._illum_map[self.illum_var.get()]
        target = self._target_map[self.target_var.get()]
        camera_name = self.camera_entry.get().strip()
        profile_name = self.profile_name_entry.get().strip()
        crop = self._get_crop()
        chart_box = self._chart_box  # from auto-detection

        # CC24 workflow: force detection path and do not fall back to crop/scanin.
        if chart == "cc24":
            if chart_box is None:
                self._detect_chart()
                chart_box = self._chart_box
            if chart_box is None:
                messagebox.showerror(
                    "Chart not detected",
                    "Could not detect the ColorChecker automatically.\n\n"
                    "Use a tighter framing and click Detect Chart again.\n"
                    "Then adjust the green corner pins if needed.",
                )
                return
            crop = None
        do_install = self.install_var.get()

        threading.Thread(
            target=self._run_pipeline_bg,
            args=(chart, illuminant, target, camera_name, profile_name,
                  crop, chart_box, do_install),
            daemon=True,
        ).start()

    def _run_pipeline_bg(self, chart, illuminant, target, camera_name,
                         profile_name, crop, chart_box, install):
        result = self.engine.run(
            raw_path=self.raw_path,
            chart=chart,
            illuminant=illuminant,
            target=target,
            crop=crop,
            chart_box=chart_box,
            camera_name=camera_name,
            profile_name=profile_name,
            install=install,
        )
        self.after(0, self._on_pipeline_done, result)

    def _on_pipeline_done(self, result):
        self._processing = False
        self.create_btn.configure(state="normal", text="CREATE PROFILE")

        if result.success:
            parts = []
            if result.icc_path:
                parts.append(f"ICC: {Path(result.icc_path).name}")
            if result.dcp_path:
                parts.append(f"DCP: {Path(result.dcp_path).name}")
            messagebox.showinfo(
                "Profile created",
                f"Successfully created camera profile!\n\n"
                + "\n".join(parts)
                + ("\n\nRestart your editing application to use the new profile."
                   if result.installed else ""),
            )

    # ── window close ─────────────────────────────────────────────────

    def _on_close(self):
        if self._processing and self.engine:
            self.engine.cancel()
        self.destroy()
