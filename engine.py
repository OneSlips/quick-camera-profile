"""Quick Camera Profile - Engine

Automated camera profiling pipeline:
  RAW -> linear TIFF -> Argyll scanin -> dcamprof make-profile -> ICC / DCP

Supports every camera RAW format handled by LibRaw (via rawpy):
  DNG, ARW, CR2, CR3, NEF, RAF, ORF, RW2, PEF, 3FR, IIQ, and more.
"""

import gc
import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import rawpy
import tifffile

__version__ = "1.0.0"

# ── Supported RAW extensions (LibRaw) ───────────────────────────────────

RAW_EXTENSIONS = (
    ".3fr", ".arw", ".cr2", ".cr3", ".crw", ".dcr", ".dng", ".erf",
    ".fff", ".iiq", ".kdc", ".mef", ".mos", ".nef", ".nrw", ".orf",
    ".pef", ".raf", ".raw", ".rw2", ".rwl", ".sr2", ".srf", ".srw",
    ".x3f",
)

RAW_FILTER_PAIRS = [
    ("Camera RAW files", " ".join(f"*{e}" for e in RAW_EXTENSIONS)),
    ("All files", "*.*"),
]

# ── Chart definitions ───────────────────────────────────────────────────
#  key -> (display_label, .cht file, .cie file or None)

CHARTS = {
    "cc24":  ("ColorChecker Classic 24-patch", "ColorChecker.cht", "ColorChecker.cie"),
    "ccsg":  ("ColorChecker SG 140-patch",     "ColorCheckerSG.cht", None),
}

# ── Illuminants ─────────────────────────────────────────────────────────
#  (code, display_label)  — order is what appears in the dropdown

ILLUMINANTS = [
    ("D50",  "D50  -  Noon daylight (5003 K)"),
    ("D55",  "D55  -  Daylight (5503 K)"),
    ("D65",  "D65  -  Overcast / shade (6504 K)"),
    ("D75",  "D75  -  North sky (7504 K)"),
    ("StdA", "Tungsten / incandescent (2856 K)"),
    ("F11",  "Fluorescent TL84 (4000 K)"),
]

# ── Output targets ──────────────────────────────────────────────────────

TARGETS = [
    ("capture_one", "Capture One  (ICC profile)"),
    ("lightroom",   "Adobe Lightroom / ACR  (DCP profile)"),
    ("both",        "Both  (ICC + DCP)"),
]

# ── Result dataclass ────────────────────────────────────────────────────

@dataclass
class ProfileResult:
    success: bool = False
    icc_path: Optional[str] = None
    dcp_path: Optional[str] = None
    camera_name: str = ""
    error: str = ""
    installed: list = field(default_factory=list)


# ── CC24 Lab reference values (Argyll/X-Rite) ──────────────────────────
# Patch order matches OpenCV MCC detector output
_CC24_LAB = [
    (37.99,  13.56,  14.06),   # A01 Dark Skin
    (65.71,  18.13,  17.81),   # A02 Light Skin
    (49.93,  -4.88, -21.93),   # A03 Blue Sky
    (43.14, -13.10,  21.91),   # A04 Foliage
    (55.11,   8.84, -25.40),   # A05 Blue Flower
    (70.72, -33.40,  -0.20),   # A06 Bluish Green
    (62.66,  36.07,  57.10),   # B01 Orange
    (40.02,  10.41, -45.96),   # B02 Purplish Blue
    (51.12,  48.24,  16.25),   # B03 Moderate Red
    (30.33,  22.98, -21.59),   # B04 Purple
    (72.53, -23.71,  57.26),   # B05 Yellow Green
    (71.94,  19.36,  67.86),   # B06 Orange Yellow
    (28.78,  14.18, -50.30),   # C01 Blue
    (55.26, -38.34,  31.37),   # C02 Green
    (42.10,  53.38,  28.19),   # C03 Red
    (81.73,   4.04,  79.82),   # C04 Yellow
    (51.94,  49.99, -14.57),   # C05 Magenta
    (51.04, -28.63, -28.64),   # C06 Cyan
    (96.54,  -0.43,   1.19),   # D01 White
    (81.26,  -0.64,  -0.34),   # D02 Neutral 8
    (66.77,  -0.73,  -0.50),   # D03 Neutral 6.5
    (50.87,  -0.15,  -0.27),   # D04 Neutral 5
    (35.66,  -0.42,  -1.23),   # D05 Neutral 3.5
    (20.46,  -0.08,  -0.97),   # D06 Black
]

_CC24_IDS = [
    "A01", "A02", "A03", "A04", "A05", "A06",
    "B01", "B02", "B03", "B04", "B05", "B06",
    "C01", "C02", "C03", "C04", "C05", "C06",
    "D01", "D02", "D03", "D04", "D05", "D06",
]


def _lab_to_xyz(L: float, a: float, b: float) -> tuple[float, float, float]:
    """CIE Lab → XYZ (D50 illuminant, 2° observer)."""
    # D50 reference white
    Xn, Yn, Zn = 96.422, 100.0, 82.521

    fy = (L + 16.0) / 116.0
    fx = a / 500.0 + fy
    fz = fy - b / 200.0

    eps = 216.0 / 24389.0
    kappa = 24389.0 / 27.0

    xr = fx ** 3 if fx ** 3 > eps else (116.0 * fx - 16.0) / kappa
    yr = ((L + 16.0) / 116.0) ** 3 if L > kappa * eps else L / kappa
    zr = fz ** 3 if fz ** 3 > eps else (116.0 * fz - 16.0) / kappa

    return xr * Xn, yr * Yn, zr * Zn


# ── Helpers ─────────────────────────────────────────────────────────────

def _bundle_root() -> Path:
    """Root dir — PyInstaller _MEIPASS when frozen, else project root."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


# ── Engine ──────────────────────────────────────────────────────────────

class ProfileEngine:
    """Full profiling pipeline with automatic tool discovery."""

    def __init__(
        self,
        dcamprof: str = None,
        scanin: str = None,
        argyll_ref: str = None,
        log: Callable[[str], None] = None,
    ):
        self._log_fn = log or print
        self._cancel = False
        self.dcamprof = dcamprof or self._find_dcamprof()
        self.scanin = scanin or self._find("scanin")
        self.argyll_ref = argyll_ref or self._find_ref()

    # ── tool discovery ────────────────────────────────────────────────

    @staticmethod
    def _find_dcamprof() -> str:
        root = _bundle_root()
        exe = "dcamprof.exe" if os.name == "nt" else "dcamprof"
        p = root / "bin" / exe
        if p.is_file():
            return str(p)
        found = shutil.which("dcamprof")
        if found:
            return found
        raise FileNotFoundError(
            "dcamprof not found.\n"
            "Place dcamprof.exe in the bin/ folder or add it to PATH."
        )

    @staticmethod
    def _find(name: str) -> str:
        root = _bundle_root()
        exe = f"{name}.exe" if os.name == "nt" else name
        p = root / "argyll" / exe
        if p.is_file():
            return str(p)
        found = shutil.which(name)
        if found:
            return found
        # WinGet-installed Argyll CMS on Windows
        if os.name == "nt":
            local = os.environ.get("LOCALAPPDATA", "")
            if local:
                pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
                if pkgs.is_dir():
                    for d in pkgs.glob("GraemeGill.ArgyllCMS*"):
                        for hit in d.rglob(exe):
                            return str(hit)
        raise FileNotFoundError(
            f"'{name}' not found.\n"
            "Install Argyll CMS (https://argyllcms.com) and add to PATH."
        )

    @staticmethod
    def _find_ref() -> str:
        # bundled
        p = _bundle_root() / "argyll" / "ref"
        if p.is_dir() and (p / "ColorChecker.cht").is_file():
            return str(p)
        # winget on Windows
        if os.name == "nt":
            local = os.environ.get("LOCALAPPDATA", "")
            if local:
                pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
                if pkgs.is_dir():
                    for d in pkgs.glob("GraemeGill.ArgyllCMS*"):
                        for r in d.rglob("ref"):
                            if (r / "ColorChecker.cht").is_file():
                                return str(r)
        # relative to scanin binary
        s = shutil.which("scanin")
        if s:
            r = Path(s).resolve().parent.parent / "ref"
            if (r / "ColorChecker.cht").is_file():
                return str(r)
        raise FileNotFoundError(
            "Argyll CMS reference files not found.\n"
            "Install Argyll CMS so that the ref/ directory is available."
        )

    # ── Capture One camera-ID resolution ─────────────────────────────

    @staticmethod
    def _c1_camera_id(camera_name: str) -> str | None:
        """Derive Capture One's camera ID from built-in profile filenames.

        C1 stores its ICC profiles as ``{CameraId}-{Style}.icm`` in the
        DSLR subfolder.  We search for a profile whose CameraId matches
        the EXIF make/model (e.g. "SONY ILCE-7M5" -> "SonyA7M5").
        Returns None when C1 is not installed or the camera is unknown.
        """
        if os.name == "nt":
            dslr = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) \
                   / "Capture One" / "Capture One" / "Color Profiles" / "DSLR"
        else:
            dslr = Path("/Applications/Capture One.app/Contents/"
                        "Resources/Color Profiles/DSLR")
        if not dslr.is_dir():
            return None

        # Build search tokens from the camera name  e.g. "SONY ILCE-7M5"
        # -> ["sony", "ilce", "7m5"]  -> also try without hyphens -> "7m5"
        parts = re.split(r"[\s\-/]+", camera_name.lower())
        # Remove common prefixes/words that don't appear in C1 camera IDs
        skip = {"ilce", "dsc", "dslr", "corporation", "company", "optical",
                "imaging", "corp", "inc", "ltd", "co"}
        model_parts = [p for p in parts if p not in skip]
        # Deduplicate while preserving order
        seen = set()
        model_parts = [p for p in model_parts if not (p in seen or seen.add(p))]

        # Avoid ambiguous one-letter token matches (e.g. "c" in "canon").
        # If model ends with a single-letter suffix, merge it into previous
        # token so "r5 c" also tries "r5c".
        model_tokens = [p for p in model_parts if len(p) >= 2]
        if len(model_parts) >= 2 and len(model_parts[-1]) == 1:
            merged = f"{model_parts[-2]}{model_parts[-1]}"
            model_tokens.append(merged)
        # Also match against a compact, separator-free camera string.
        compact = "".join(model_parts)
        if len(compact) >= 4:
            model_tokens.append(compact)
        # Deduplicate while preserving order
        seen_tokens = set()
        model_tokens = [t for t in model_tokens if not (t in seen_tokens or seen_tokens.add(t))]

        if not model_tokens:
            return None

        best_id = None
        best_score = 0
        total_chars = sum(len(t) for t in model_tokens)

        for f in dslr.iterdir():
            if not f.suffix.lower() == ".icm":
                continue
            stem = f.stem  # e.g. "SonyA7M5-ProStandard"
            cam_id = stem.split("-")[0]  # "SonyA7M5"
            cam_lower = cam_id.lower()  # "sonya7m5"
            # Score: sum of longest prefix (or exact) match for each token
            score = 0
            matched = True
            for tok in model_tokens:
                if tok in cam_lower:
                    score += len(tok)
                    continue
                # Try progressively shorter prefixes (min 3 chars)
                found = False
                for n in range(len(tok) - 1, 2, -1):
                    if tok[:n] in cam_lower:
                        score += n
                        found = True
                        break
                if not found:
                    matched = False
                    break
            if matched and score > best_score:
                best_score = score
                best_id = cam_id

        # Require matching at least 60% of total token characters
        if best_id and best_score >= total_chars * 0.6:
            return best_id
        return None

    # ── logging / cancel ─────────────────────────────────────────────

    def log(self, msg: str):
        self._log_fn(msg)

    def cancel(self):
        self._cancel = True

    # ── subprocess helper ────────────────────────────────────────────

    def _run(self, cmd: list, cwd: str = None) -> subprocess.CompletedProcess:
        """Run an external tool, ensuring its co-located DLLs are found."""
        kwargs: dict = dict(capture_output=True, text=True)
        if cwd:
            kwargs["cwd"] = cwd
        if os.name == "nt":
            # Add the executable's directory to PATH so the Windows loader
            # finds co-located DLLs (libgomp, liblcms2, libtiff, etc.)
            env = os.environ.copy()
            exe_dir = os.path.dirname(os.path.abspath(cmd[0]))
            env["PATH"] = exe_dir + ";" + env.get("PATH", "")
            kwargs["env"] = env
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.run(cmd, **kwargs)

    # ── reading RAW metadata ─────────────────────────────────────────

    def read_raw_info(self, path: str) -> dict:
        """Return camera make, model, and image dimensions."""
        # get dimensions from rawpy
        raw = rawpy.imread(path)
        w, h = raw.sizes.width, raw.sizes.height
        raw.close()
        # get make/model from EXIF
        make, model = "Unknown", "Unknown"
        # Try exifread first (fast, handles TIFF-based RAWs: ARW, CR2, NEF, DNG …)
        try:
            import exifread
            with open(path, "rb") as f:
                tags = exifread.process_file(f, stop_tag="UNDEF", details=False)
            if tags:
                make = str(tags.get("Image Make", "Unknown")).strip()
                model = str(tags.get("Image Model", "Unknown")).strip()
        except Exception:
            pass
        # Fallback to pyexiv2 for formats exifread can't parse (CR3, HEIF …)
        if make == "Unknown" or model == "Unknown":
            try:
                import pyexiv2
                img = pyexiv2.Image(path)
                exif = img.read_exif()
                if make == "Unknown":
                    make = exif.get("Exif.Image.Make", "Unknown").strip()
                if model == "Unknown":
                    model = exif.get("Exif.Image.Model", "Unknown").strip()
                img.close()
            except Exception:
                pass
        # Strip make from model if duplicated (e.g. Canon "Canon EOS R5 C")
        if make != "Unknown" and model.lower().startswith(make.lower()):
            model = model[len(make):].strip()
        return {"make": make, "model": model, "width": w, "height": h}

    def generate_preview(self, path: str, max_px: int = 1400) -> np.ndarray:
        """Fast 8-bit sRGB preview for the crop canvas."""
        raw = rawpy.imread(path)
        rgb = raw.postprocess(
            use_camera_wb=True,
            half_size=True,
            output_bps=8,
            no_auto_bright=False,
        )
        raw.close()
        h, w = rgb.shape[:2]
        if max(w, h) > max_px:
            from PIL import Image
            s = max_px / max(w, h)
            rgb = np.array(
                Image.fromarray(rgb).resize((int(w * s), int(h * s)), Image.LANCZOS)
            )
        return rgb

    # ── chart detection ──────────────────────────────────────────────

    @staticmethod
    def detect_chart(
        preview_rgb: np.ndarray,
        full_w: int,
        full_h: int,
        chart: str = "cc24",
    ) -> dict | None:
        """Detect a colour checker in the preview image.

        Returns a dict with:
          - ``box``: 4 corners of the chart in full-resolution coords
                     [[x,y], ...] ordered TL, BL, BR, TR.
          - ``box_preview``: same corners in preview pixel coords.
          - ``angle``: rotation angle in degrees.
        Returns None if detection fails.
        """
        try:
            import cv2
        except ImportError:
            return None

        chart_type = cv2.mcc.MCC24 if chart == "cc24" else cv2.mcc.MCC24
        bgr = cv2.cvtColor(preview_rgb, cv2.COLOR_RGB2BGR)
        detector = cv2.mcc.CCheckerDetector.create()
        if not detector.process(bgr, chart_type):
            return None

        checkers = detector.getListColorChecker()
        if not checkers:
            return None

        checker = checkers[0]
        box_preview = checker.getBox().tolist()  # 4 corners in preview coords

        # Scale preview coords to full resolution
        ph, pw = preview_rgb.shape[:2]
        sx = full_w / pw
        sy = full_h / ph
        box_full = [[pt[0] * sx, pt[1] * sy] for pt in box_preview]

        # Compute rotation angle from the top edge
        dx = box_preview[1][0] - box_preview[0][0]
        dy = box_preview[1][1] - box_preview[0][1]
        angle = np.degrees(np.arctan2(dy, dx))

        return {
            "box": box_full,
            "box_preview": box_preview,
            "angle": angle,
        }

    # ── pipeline steps ───────────────────────────────────────────────

    def _deskew_tiff(self, src: str, dst: str, box: list[list[float]]):
        """Perspective-warp the chart region to an upright rectangle.

        *box* is the 4 corners in full-res coordinates as returned by
        ``detect_chart()['box']``.  Order from MCC detector is
        TL, BL, BR, TR when the chart is roughly upright.
        """
        import cv2
        self.log("  Deskewing detected chart region ...")
        img = tifffile.imread(src)

        src_pts = np.array(box, dtype=np.float32)

        # Compute output dimensions from the longest edges
        w1 = np.linalg.norm(src_pts[3] - src_pts[0])  # top edge
        w2 = np.linalg.norm(src_pts[2] - src_pts[1])  # bottom edge
        h1 = np.linalg.norm(src_pts[1] - src_pts[0])  # left edge
        h2 = np.linalg.norm(src_pts[2] - src_pts[3])  # right edge
        out_w = int(max(w1, w2))
        out_h = int(max(h1, h2))

        # Add ~15% padding around the chart for scanin detection
        pad_x = int(out_w * 0.15)
        pad_y = int(out_h * 0.15)
        dst_w = out_w + 2 * pad_x
        dst_h = out_h + 2 * pad_y

        dst_pts = np.array([
            [pad_x,         pad_y],           # TL
            [pad_x,         pad_y + out_h],   # BL
            [pad_x + out_w, pad_y + out_h],   # BR
            [pad_x + out_w, pad_y],           # TR
        ], dtype=np.float32)

        M = cv2.getPerspectiveTransform(src_pts, dst_pts)
        warped = cv2.warpPerspective(
            img, M, (dst_w, dst_h),
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )

        # CC24 is landscape (6×4); rotate 90° if the chart came out portrait
        if warped.shape[0] > warped.shape[1]:
            warped = np.rot90(warped, k=3)  # 270° CW = 90° CCW
            self.log("  Rotated to landscape orientation")

        tifffile.imwrite(dst, warped)
        self.log(f"  {warped.shape[1]}x{warped.shape[0]} -> {Path(dst).name}")
        del img, warped
        gc.collect()

    def _to_linear_tiff(self, raw_path: str, out: str):
        self.log("Step 1/4: Converting RAW to linear TIFF ...")
        raw = rawpy.imread(raw_path)
        rgb = raw.postprocess(
            use_camera_wb=True,
            output_color=rawpy.ColorSpace.raw,
            output_bps=16,
            no_auto_bright=True,
            gamma=(1, 1),
        )
        raw.close()
        tifffile.imwrite(out, rgb)
        self.log(f"  {rgb.shape[1]}x{rgb.shape[0]} -> {Path(out).name}")
        del rgb
        gc.collect()

    def _crop_tiff(self, src: str, dst: str, x1: int, y1: int, x2: int, y2: int):
        self.log(f"  Cropping to [{x1}:{x2}, {y1}:{y2}] ...")
        img = tifffile.imread(src)
        crop = img[y1:y2, x1:x2, :]
        tifffile.imwrite(dst, crop)
        self.log(f"  {crop.shape[1]}x{crop.shape[0]} -> {Path(dst).name}")
        del img, crop
        gc.collect()

    def _scanin(self, tiff: str, chart: str, out_ti3: str):
        self.log("Step 2/4: Detecting colour patches ...")
        _label, cht_name, cie_name = CHARTS[chart]
        cht = str(Path(self.argyll_ref) / cht_name)
        cmd = [self.scanin, "-v", "-a", "-G", "1.0", "-dipn", tiff, cht]
        if cie_name:
            cmd.append(str(Path(self.argyll_ref) / cie_name))
        r = self._run(cmd, cwd=str(Path(tiff).parent))
        if r.returncode != 0:
            msg = r.stderr.strip()
            if "Pattern match" in msg or "good enough" in msg:
                raise RuntimeError(
                    "Could not detect the colour checker in the image.\n"
                    "Ensure the chart is well-lit, flat, and clearly visible.\n"
                    "If the chart is small in the frame, draw a crop around it."
                )
            raise RuntimeError(f"scanin failed:\n{msg[-500:]}")
        produced = Path(tiff).parent / f"{Path(tiff).stem}.ti3"
        if not produced.is_file():
            raise FileNotFoundError("scanin produced no .ti3 output")
        shutil.move(str(produced), out_ti3)
        self._validate_ti3(out_ti3)
        self.log(f"  Patches -> {Path(out_ti3).name}")

    def _validate_ti3(self, ti3_path: str):
        """Check that scanin produced sane patch readings.

        When auto-detection locks onto the wrong area, the RGB values
        are nearly uniform and the per-patch standard deviation often
        exceeds the patch mean.  Catch this early so the user gets a
        clear error instead of a silently bad profile.
        """
        with open(ti3_path) as f:
            lines = f.read().splitlines()
        in_data = False
        bad = 0
        total = 0
        patches: dict[str, list[float]] = {}  # ID -> [R, G, B]
        for line in lines:
            stripped = line.strip()
            if stripped == "BEGIN_DATA":
                in_data = True
                continue
            if stripped == "END_DATA":
                break
            if not in_data or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 10:
                continue
            # columns: ID XYZ_X XYZ_Y XYZ_Z RGB_R RGB_G RGB_B STDEV_R STDEV_G STDEV_B
            try:
                patch_id = parts[0]
                rgb = [float(parts[4]), float(parts[5]), float(parts[6])]
                std = [float(parts[7]), float(parts[8]), float(parts[9])]
            except (IndexError, ValueError):
                continue
            patches[patch_id] = rgb
            total += 1
            mean_rgb = sum(rgb) / 3.0
            mean_std = sum(std) / 3.0
            if mean_rgb > 0 and mean_std > mean_rgb:
                bad += 1
        if total > 0 and bad > total * 0.25:
            raise RuntimeError(
                f"Patch detection looks unreliable ({bad}/{total} patches "
                "have excessive noise).\n"
                "The chart may be too small in the frame or misdetected.\n"
                "Please draw a tight crop around the colour checker and retry."
            )

        # ── CC24 grayscale ramp check ──
        # For a ColorChecker 24-patch, the D row (D01–D06) is a neutral
        # grayscale ramp from white to black.  Their average RGB should
        # be strictly decreasing.  If the chart is misdetected or
        # rotated, this ordering breaks.
        d_patches = [(k, patches[k]) for k in sorted(patches)
                     if k.startswith("D") and k[1:].isdigit()]
        if len(d_patches) >= 4:
            d_lum = [sum(rgb) / 3.0 for _, rgb in d_patches]
            # Check monotonically decreasing (allow tiny tolerance)
            inversions = sum(1 for i in range(len(d_lum) - 1)
                            if d_lum[i] < d_lum[i + 1] * 0.95)
            if inversions > 1:
                ramp = ", ".join(f"{v:.1f}" for v in d_lum)
                raise RuntimeError(
                    f"Grayscale ramp is not monotonic: [{ramp}].\n"
                    "The chart may be rotated or incorrectly detected.\n"
                    "Draw a tight crop around the colour checker and retry."
                )
            # Prefer a relative check so underexposed captures can still pass.
            # Reject only if the brightest neutral patch is nearly as dark as
            # the black patch, which indicates misdetection.
            if d_lum and (d_lum[0] < 5.0 or d_lum[0] < d_lum[-1] * 2.2):
                raise RuntimeError(
                    f"Neutral ramp contrast is too low (D01={d_lum[0]:.1f}, "
                    f"D06={d_lum[-1]:.1f}).\n"
                    "The chart may be misdetected or severely underexposed.\n"
                    "Draw a tight crop around the colour checker and retry."
                )

    def _extract_patches_mcc(self, raw_path: str, out_ti3: str, chart_box: list):
        """Detect patches with OpenCV MCC on the linear TIFF and write a TI3.

        This bypasses scanin entirely: the MCC detector locates each
        patch, we sample the camera-space RGB from the linear TIFF,
        and produce the TI3 that dcamprof needs.
        """
        import cv2

        self.log("Step 2/4: Detecting colour patches (MCC) ...")

        # Read the linear TIFF
        img = tifffile.imread(raw_path)  # (H, W, 3) uint16
        h, w = img.shape[:2]

        # Build a warped, patch-sized view for MCC re-detection
        # Use the chart_box to extract the region, deskew it, then
        # run MCC on the deskewed 8-bit version for precise patch positions.
        src_pts = np.array(chart_box, dtype=np.float32)

        # Output dimensions from edge lengths
        w1 = np.linalg.norm(src_pts[3] - src_pts[0])
        w2 = np.linalg.norm(src_pts[2] - src_pts[1])
        h1 = np.linalg.norm(src_pts[1] - src_pts[0])
        h2 = np.linalg.norm(src_pts[2] - src_pts[3])
        out_w = int(max(w1, w2))
        out_h = int(max(h1, h2))

        # CC24 is landscape (6×4) — if portrait, swap axes
        landscape = out_w >= out_h
        if not landscape:
            out_w, out_h = out_h, out_w

        pad = int(min(out_w, out_h) * 0.10)
        dw = out_w + 2 * pad
        dh = out_h + 2 * pad

        # Build destination points — landscape orientation
        if landscape:
            dst_pts = np.array([
                [pad,         pad],
                [pad,         pad + out_h],
                [pad + out_w, pad + out_h],
                [pad + out_w, pad],
            ], dtype=np.float32)
        else:
            # Rotate: remap src corners for landscape output
            # MCC corners: TL(0), BL(1), BR(2), TR(3)
            # After 90° CW: TL→TR, BL→TL, BR→BL, TR→BR
            dst_pts = np.array([
                [pad + out_w, pad],          # src TL → dst TR
                [pad,         pad],          # src BL → dst TL
                [pad,         pad + out_h],  # src BR → dst BL
                [pad + out_w, pad + out_h],  # src TR → dst BR
            ], dtype=np.float32)

        M = cv2.getPerspectiveTransform(src_pts, dst_pts)

        # Warp the 16-bit linear image
        warped16 = cv2.warpPerspective(
            img, M, (dw, dh),
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        self.log(f"  Deskewed chart: {dw}x{dh}")

        # Convert to linear 8-bit for MCC detection. Keep linearity so the
        # extracted means can be scaled back to TI3 RGB values.
        warped8 = np.clip((warped16.astype(np.float32) / 65535.0) * 255.0, 0, 255).astype(np.uint8)

        # Run MCC on the deskewed 8-bit image
        bgr8 = cv2.cvtColor(warped8, cv2.COLOR_RGB2BGR)
        detector = cv2.mcc.CCheckerDetector.create()
        if not detector.process(bgr8, cv2.mcc.MCC24):
            raise RuntimeError(
                "MCC detector could not find patches in the deskewed chart.\n"
                "Ensure the chart is clearly visible and well-lit."
            )

        checker = detector.getListColorChecker()[0]
        charts_rgb = checker.getChartsRGB()

        # charts_rgb shape: (72, 5) = 24 patches * 3 channels (R,G,B)
        # Each row: [pixel_count, mean, stdev, min, max]
        if charts_rgb.shape[0] != 72:
            raise RuntimeError(
                f"Expected 72 channel rows (24×3), got {charts_rgb.shape[0]}"
            )

        # OpenCV MCC returns channels in standard CC24 patch order:
        # 72 rows = 24 patches * 3 channels (R, G, B), each row is
        # [pixel_count, mean, stdev, min, max].
        means_8 = charts_rgb[:, 1].reshape(24, 3).astype(np.float64)
        stds_8 = charts_rgb[:, 2].reshape(24, 3).astype(np.float64)

        # TI3 RGB values are conventionally in 0..100. Since warped8 is linear
        # 0..255 derived from the 16-bit linear TIFF, this keeps proportions.
        scale = 100.0 / 255.0
        patch_rgb = (means_8 * scale).tolist()
        patch_std = (stds_8 * scale).tolist()

        del img, warped16, warped8
        gc.collect()

        # Build TI3 file
        from datetime import datetime
        now = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
        lines = [
            "CTI3   ",
            "",
            'DESCRIPTOR "Argyll Calibration Target chart information 3"',
            'ORIGINATOR "Quick Camera Profile (MCC)"',
            f'CREATED "{now}"',
            'DEVICE_CLASS "INPUT"',
            'COLOR_REP "XYZ_RGB"',
            "",
            "NUMBER_OF_FIELDS 10",
            "BEGIN_DATA_FORMAT",
            "SAMPLE_ID XYZ_X XYZ_Y XYZ_Z RGB_R RGB_G RGB_B STDEV_R STDEV_G STDEV_B ",
            "END_DATA_FORMAT",
            "",
            "NUMBER_OF_SETS 24",
            "BEGIN_DATA",
        ]

        for i in range(24):
            L, a, b = _CC24_LAB[i]
            X, Y, Z = _lab_to_xyz(L, a, b)
            R, G, B = patch_rgb[i]
            sR, sG, sB = patch_std[i]
            lines.append(
                f"{_CC24_IDS[i]} {X:.5f} {Y:.5f} {Z:.5f} "
                f"{R:.6f} {G:.6f} {B:.6f} "
                f"{sR:.6f} {sG:.6f} {sB:.6f} "
            )

        lines.append("END_DATA")
        lines.append("")

        with open(out_ti3, "w") as f:
            f.write("\n".join(lines))

        self._validate_ti3(out_ti3)
        self.log(f"  24 patches -> {Path(out_ti3).name}")

    def _make_profile(self, ti3: str, out: str, illum: str):
        self.log("Step 3/4: Computing camera profile ...")
        work_dir = str(Path(ti3).parent)
        cmd = [self.dcamprof, "make-profile", "-i", illum, ti3, out]

        gc.collect()

        # Safety retry — Windows process creation can rarely fail transiently
        for attempt in range(2):
            r = self._run(cmd, cwd=work_dir)
            if r.returncode == 0:
                break
            crash_code = r.returncode & 0xFFFFFFFF
            if attempt == 0 and crash_code >= 0xC0000000:
                self.log(f"  dcamprof exited 0x{crash_code:08X} — retrying ...")
                gc.collect()
                continue
            combined = (r.stdout + "\n" + r.stderr).strip()
            if not combined:
                combined = f"(process exited with code {r.returncode} / 0x{crash_code:08X})"
            raise RuntimeError(f"make-profile failed:\n{combined[-800:]}")
        self.log(f"  Profile -> {Path(out).name}")
        self._validate_profile(out, r.stdout + "\n" + r.stderr)

    def _validate_profile(self, json_path: str, dcamprof_output: str):
        """Validate the profile JSON and dcamprof output for sanity."""
        # ── Parse max DE from dcamprof stdout ──
        # Look for the ForwardMatrix section's "max DE" line.
        # The output contains multiple "max DE" lines (for ColorMatrix,
        # LUTMatrix, ForwardMatrix, native LUT).  We want the
        # ForwardMatrix one — it appears after "ForwardMatrix1".
        fm_section = False
        max_de = None
        for line in dcamprof_output.splitlines():
            if "ForwardMatrix1" in line:
                fm_section = True
            if fm_section:
                m = re.search(r"max DE\s+([\d.]+)", line)
                if m:
                    max_de = float(m.group(1))
                    break

        if max_de is not None:
            self.log(f"  Max DE: {max_de:.2f}")
            if max_de > 12.0:
                raise RuntimeError(
                    f"Profile colour accuracy is very poor (max DE {max_de:.1f}).\n"
                    "This usually means the chart was misdetected.\n"
                    "Draw a tighter crop around the colour checker and retry."
                )

        # ── Validate ForwardMatrix from the JSON ──
        try:
            import json as _json
            with open(json_path) as f:
                prof = _json.load(f)
            fm = prof.get("ForwardMatrix1")
            if fm:
                diag = [fm[0][0], fm[1][1], fm[2][2]]
                self.log(f"  FM diag: [{diag[0]:.4f}, {diag[1]:.4f}, {diag[2]:.4f}]")
                if any(d < 0.3 for d in diag):
                    raise RuntimeError(
                        "ForwardMatrix diagonal is unusually small — the "
                        "profile is unreliable.\nDraw a tighter crop around "
                        "the colour checker and retry."
                    )
        except RuntimeError:
            raise
        except Exception:
            pass

    def _make_icc(self, json_path: str, out: str, name: str):
        self.log("  Building ICC profile ...")
        # Capture One always applies an internal gamma ~1.8 encoding to
        # the raw data before the ICC profile.  We pass a matching
        # transfer function (-f) so the CLUT compensates for it.
        tf = str(Path(__file__).resolve().parent / "data" / "c1_transfer_array.json")
        r = self._run(
            [self.dcamprof, "make-icc",
             "-f", tf,
             "-n", name,
             "-c", f"DCamProf - {name}",
             json_path, out],
            cwd=str(Path(json_path).parent),
        )
        if r.returncode != 0:
            combined = (r.stdout + "\n" + r.stderr).strip()
            raise RuntimeError(f"make-icc failed:\n{combined[-800:]}")
        self.log(f"  -> {Path(out).name}")

    def _make_dcp(self, json_path: str, out: str, cam_name: str, disp_name: str):
        self.log("  Building DCP profile ...")
        r = self._run(
            [self.dcamprof, "make-dcp",
             "-n", cam_name,
             "-d", disp_name,
             "-c", f"DCamProf - {cam_name}",
             json_path, out],
            cwd=str(Path(json_path).parent),
        )
        if r.returncode != 0:
            combined = (r.stdout + "\n" + r.stderr).strip()
            raise RuntimeError(f"make-dcp failed:\n{combined[-800:]}")
        self.log(f"  -> {Path(out).name}")

    # ── install helpers ──────────────────────────────────────────────

    @staticmethod
    def install_dir(target: str) -> Path:
        """Standard profile directory for the target application."""
        if os.name == "nt":
            if target == "capture_one":
                return Path(os.environ["LOCALAPPDATA"]) / "CaptureOne" / "Color Profiles"
            return Path(os.environ["APPDATA"]) / "Adobe" / "CameraRaw" / "CameraProfiles"
        # macOS
        if target == "capture_one":
            return Path.home() / "Library" / "ColorSync" / "Profiles"
        return (
            Path.home() / "Library" / "Application Support"
            / "Adobe" / "CameraRaw" / "CameraProfiles"
        )

    def _install(self, src: str, target: str) -> str:
        d = self.install_dir(target)
        d.mkdir(parents=True, exist_ok=True)
        dst = d / Path(src).name
        shutil.copy2(src, str(dst))
        self.log(f"  Installed -> {dst}")
        return str(dst)

    # ── main pipeline ────────────────────────────────────────────────

    def run(
        self,
        raw_path: str,
        chart: str = "cc24",
        illuminant: str = "D50",
        target: str = "capture_one",
        crop: tuple[int, int, int, int] | None = None,
        chart_box: list[list[float]] | None = None,
        camera_name: str = "",
        profile_name: str = "",
        install: bool = True,
        output_dir: str | None = None,
    ) -> ProfileResult:
        """Run the complete profiling pipeline.

        *chart_box* is the 4 corners of the detected chart in full-res
        coordinates (from ``detect_chart()``). When provided, the pipeline
        perspective-warps the chart region instead of using *crop*.

        Returns a ProfileResult with paths to generated profiles.
        """
        self._cancel = False
        raw_path = os.path.abspath(raw_path)
        out_dir = Path(output_dir or Path(raw_path).parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(raw_path).stem

        # camera name
        if not camera_name:
            info = self.read_raw_info(raw_path)
            camera_name = f"{info['make']} {info['model']}"
        safe = camera_name.replace(" ", "_").replace("/", "-")

        self.log(f"Camera:  {camera_name}")
        self.log(f"Chart:   {CHARTS[chart][0]}")
        self.log(f"Light:   {illuminant}")
        tgt_label = dict(TARGETS).get(target, target)
        self.log(f"Output:  {tgt_label}")
        self.log("-" * 45)

        # Resolve Capture One camera ID (e.g. "SONY ILCE-7M5" -> "SonyA7M5")
        c1_id = self._c1_camera_id(camera_name) if target in ("capture_one", "both") else None
        if c1_id:
            self.log(f"C1 ID:   {c1_id}")

        # Use a temp directory with a short clean path for intermediates
        # (avoids dcamprof crashes on long/special-char paths)
        label = profile_name.strip() if profile_name else "DCamProf"
        style = f"{label} {illuminant}"
        if c1_id:
            icc_name = f"{c1_id}-{style}.icm"
        else:
            icc_name = f"{safe}_{illuminant}.icc"
        dcp_name = f"{safe}_{illuminant}.dcp"

        res = ProfileResult(camera_name=camera_name)

        tmpdir = tempfile.mkdtemp(prefix="qcp_")
        work = Path(tmpdir)
        tiff   = str(work / f"{stem}_linear.tiff")
        crop_f = str(work / f"{stem}_crop.tiff") if crop else None
        ti3    = str(work / f"{stem}.ti3")
        prof   = str(work / f"{stem}_profile.json")
        icc_tmp = str(work / icc_name)
        dcp_tmp = str(work / dcp_name)

        try:
            # 1 — RAW -> linear TIFF
            self._to_linear_tiff(raw_path, tiff)
            if self._cancel:
                return res

            # 1b — optional crop (manual, only when not using MCC)
            scan_input = tiff
            if not chart_box and crop:
                self._crop_tiff(tiff, crop_f, *crop)
                scan_input = crop_f
            if self._cancel:
                return res

            # 2 — patch extraction
            if chart_box:
                self._extract_patches_mcc(tiff, ti3, chart_box)
            else:
                self._scanin(scan_input, chart, ti3)
            if self._cancel:
                return res

            # 3 — make-profile
            self._make_profile(ti3, prof, illuminant)
            if self._cancel:
                return res

            # 4 — output profiles
            self.log("Step 4/4: Building output profile(s) ...")
            if target in ("capture_one", "both"):
                # Use C1-compatible desc so the profile appears under the camera
                icc_desc = f"{c1_id}-{style}" if c1_id else camera_name
                self._make_icc(prof, icc_tmp, icc_desc)
                icc_final = str(out_dir / icc_name)
                shutil.copy2(icc_tmp, icc_final)
                res.icc_path = icc_final
            if target in ("lightroom", "both"):
                disp = f"{camera_name} - {illuminant}"
                self._make_dcp(prof, dcp_tmp, camera_name, disp)
                dcp_final = str(out_dir / dcp_name)
                shutil.copy2(dcp_tmp, dcp_final)
                res.dcp_path = dcp_final

            # 5 — install
            if install:
                if res.icc_path:
                    res.installed.append(
                        self._install(res.icc_path, "capture_one")
                    )
                if res.dcp_path:
                    res.installed.append(
                        self._install(res.dcp_path, "lightroom")
                    )

            res.success = True
            self.log(f"\n{'='*45}")
            self.log("  DONE!")
            if res.icc_path:
                self.log(f"  ICC: {Path(res.icc_path).name}")
            if res.dcp_path:
                self.log(f"  DCP: {Path(res.dcp_path).name}")
            for p in res.installed:
                self.log(f"  -> {p}")
            self.log(f"{'='*45}")
            if res.installed:
                if target in ("capture_one", "both"):
                    self.log("\n  Restart Capture One to see the new profile.")
                if target in ("lightroom", "both"):
                    self.log("  Restart Lightroom — the profile will appear")
                    self.log(f"  for '{camera_name}' images in the Profile browser.")

        except Exception as e:
            res.error = str(e)
            self.log(f"\nERROR: {e}")
            self.log(f"  (temp files kept at: {tmpdir})")

        else:
            # Save diagnostic files alongside the output before cleanup
            try:
                diag_dir = out_dir / f"{stem}_diag"
                diag_dir.mkdir(exist_ok=True)
                for src in [ti3, prof]:
                    if os.path.isfile(src):
                        shutil.copy2(src, str(diag_dir / Path(src).name))
            except Exception:
                pass
            # Only clean up on success
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except OSError:
                pass

        return res
