"""Indigo design tokens for the TUI (dark baseline).

Single source of truth for colors used in Python code (status colors, etc.).
Textual CSS uses the same hex values written literally in each widget's
``DEFAULT_CSS`` — Textual's design-token system is version-volatile, so literal
hex keeps styling stable and inspectable.
"""

# Surfaces
BG = "#0d0d0f"
WORKSPACE = "#121214"
SURFACE = "#161619"
SURFACE_2 = "#1b1b1f"
SIDEBAR = "#0a0a0c"

# Borders
BORDER = "#26262b"
BORDER_2 = "#33333a"
BORDER_3 = "#4b4b55"

# Text
TEXT = "#e8e8ec"
TEXT_MUTED = "#a0a0aa"
TEXT_DIM = "#6b6b75"

# Accent (Indigo)
ACCENT = "#6366f1"
ACCENT_HI = "#7c7ff5"
ACCENT_LO = "#5457e0"
ACCENT_TINT = "#1e1e3a"
ACCENT_INK = "#c7c9ff"

# Semantic
SUCCESS = "#3fb950"
WARNING = "#d29922"
DANGER = "#f85149"
