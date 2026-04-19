"""Quick Camera Profile - Entry point.

One-click camera profiling for photographers.
Supports any camera RAW format, multiple colour checkers,
and outputs ICC (Capture One) or DCP (Lightroom/ACR) profiles.
"""

import os
import sys

# Ensure this directory is importable (for both dev and PyInstaller)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui import QuickProfileApp


def main():
    app = QuickProfileApp()
    app.mainloop()


if __name__ == "__main__":
    main()
