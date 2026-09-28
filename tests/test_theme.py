"""Tests for day and night stylesheet generation."""

from bilibili_downloader.gui.resources import load_stylesheet


def test_dark_and_light_styles_are_distinct():
    dark = load_stylesheet(True)
    light = load_stylesheet(False)

    assert dark != light
    assert "background-color: #0d0d0f" in dark
    assert "background-color: #f6f6f8" in light


def test_light_theme_overrides_core_surfaces():
    light = load_stylesheet(False)

    assert "#Sidebar" in light
    assert "QTableWidget" in light
    assert "QDialog" in light
    assert "background-color: #ffffff" in light


def test_styles_use_compact_scrollbars_with_interaction_states():
    dark = load_stylesheet(True)
    light = load_stylesheet(False)

    assert "QScrollBar::handle:vertical" in dark
    assert "width: 10px" in dark
    assert "min-height: 32px" in dark
    assert "QScrollBar::handle:vertical:hover" in dark
    assert "background-color: #c5c0c9" in light


def test_spinbox_uses_segmented_stepper_controls():
    dark = load_stylesheet(True)
    light = load_stylesheet(False)

    assert "QPushButton#StepperButton" in dark
    assert "min-width: 36px" in dark
    assert "background-color: #161619" in dark
    assert "background-color: #ffffff" in light


def test_login_instructions_use_a_non_scrolling_information_surface():
    dark = load_stylesheet(True)
    light = load_stylesheet(False)

    assert "QLabel#LoginInstructions" in dark
    assert "background-color: #161619" in dark
    assert "background-color: #f5f5f7" in light


def test_checkbox_focus_is_local_and_uses_product_accent():
    dark = load_stylesheet(True)
    light = load_stylesheet(False)

    assert "QCheckBox::indicator:focus" in dark
    assert "QCheckBox:focus::indicator" not in dark
    # Focus ring uses the Indigo product accent, not a default neutral.
    assert "border: 2px solid #7c7ff5" in dark
    assert "QCheckBox::indicator:disabled" in light
    assert "__CHECKMARK_ICON__" not in dark
    assert "checkmark.svg" in dark
    # The old neon accent must not leak into the new palette.
    assert "#62d5ff" not in dark
    assert "#ff5fa2" not in dark
