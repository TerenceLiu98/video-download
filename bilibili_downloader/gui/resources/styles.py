"""Application QSS stylesheet.

Two-theme token system (dark + light) on cool-neutral surfaces with a single
Indigo accent. Surfaces are grayscale; the accent is used sparingly (primary
buttons, active nav, focus rings, checkbox checks, progress).
"""

DARK_STYLE = """
    * {
        font-family: "PingFang SC", "Microsoft YaHei", -apple-system, "Segoe UI", Arial;
        font-size: 13px;
        letter-spacing: 0px;
        color: #e8e8ec;
    }

    QMainWindow, QDialog, #AppSurface {
        background-color: #0d0d0f;
    }

    QWidget {
        background-color: transparent;
    }

    #Workspace {
        background-color: #121214;
    }

    QScrollArea#WorkspaceScroll, QScrollArea#WorkspaceScroll > QWidget > QWidget {
        background-color: #121214;
        border: none;
    }

    QScrollBar:vertical {
        width: 10px;
        margin: 4px 1px;
        background-color: transparent;
        border: none;
    }

    QScrollBar::handle:vertical {
        min-height: 32px;
        margin: 0 2px;
        background-color: #33333a;
        border: none;
        border-radius: 3px;
    }

    QScrollBar::handle:vertical:hover {
        margin: 0 1px;
        background-color: #4b4b55;
    }

    QScrollBar::handle:vertical:pressed {
        margin: 0 1px;
        background-color: #6b6b75;
    }

    QScrollBar:horizontal {
        height: 10px;
        margin: 1px 4px;
        background-color: transparent;
        border: none;
    }

    QScrollBar::handle:horizontal {
        min-width: 32px;
        margin: 2px 0;
        background-color: #33333a;
        border: none;
        border-radius: 3px;
    }

    QScrollBar::handle:horizontal:hover {
        margin: 1px 0;
        background-color: #4b4b55;
    }

    QScrollBar::handle:horizontal:pressed {
        margin: 1px 0;
        background-color: #6b6b75;
    }

    QScrollBar::add-line, QScrollBar::sub-line {
        width: 0;
        height: 0;
        background-color: transparent;
        border: none;
    }

    QScrollBar::add-page, QScrollBar::sub-page {
        background-color: transparent;
    }

    /* ---- Sidebar ---- */
    #Sidebar {
        background-color: #0a0a0c;
        border-right: 1px solid #1e1e22;
    }

    QLabel#BrandIcon {
        background-color: #161619;
        border: 1px solid #26262b;
        border-radius: 8px;
        padding: 4px;
    }

    QLabel#BrandTitle {
        color: #ffffff;
        font-size: 18px;
        font-weight: 700;
    }

    QLabel#SidebarCaption {
        color: #6b6b75;
        font-size: 11px;
    }

    QLabel#NavSection {
        color: #6b6b75;
        font-size: 10px;
        font-weight: 600;
        padding: 0 8px 4px 8px;
    }

    QPushButton#NavButton, QPushButton#NavButtonActive {
        min-height: 34px;
        border: none;
        border-left: 2px solid transparent;
        border-radius: 6px;
        padding: 0 10px;
        text-align: left;
        color: #a0a0aa;
        font-weight: 500;
        background-color: transparent;
    }

    QPushButton#NavButton:hover {
        color: #e8e8ec;
        background-color: #161619;
    }

    QPushButton#NavButtonActive {
        color: #e8e8ec;
        background-color: #1e1e3a;
        border-left: 2px solid #6366f1;
    }

    QPushButton#SidebarAction {
        min-height: 38px;
        color: #e8e8ec;
        background-color: #161619;
        border: 1px solid #26262b;
        border-radius: 6px;
        font-weight: 600;
    }

    QPushButton#SidebarAction:hover {
        background-color: #1b1b1f;
        border-color: #33333a;
    }

    /* ---- Workspace chrome ---- */
    QLabel#PageTitle {
        color: #ffffff;
        font-size: 20px;
        font-weight: 700;
    }

    QLabel#DialogTitle {
        color: #ffffff;
        font-size: 18px;
        font-weight: 700;
    }

    QLabel#DialogCaption {
        color: #a0a0aa;
        margin-bottom: 4px;
    }

    #DialogPanel {
        background-color: #161619;
        border: 1px solid #26262b;
        border-radius: 8px;
    }

    QLabel#Caption, QLabel#MetaLabel, QLabel#MutedLabel {
        color: #a0a0aa;
    }

    QPushButton#GhostButton {
        min-width: 150px;
        min-height: 32px;
        padding: 0 14px;
        color: #a0a0aa;
        background-color: #161619;
        border: 1px solid #26262b;
        border-radius: 6px;
    }

    QPushButton#GhostButton:hover {
        color: #e8e8ec;
        border-color: #33333a;
    }

    /* ---- URL command bar ---- */
    QLineEdit#UrlInput {
        min-height: 28px;
        padding: 9px 13px;
        color: #e8e8ec;
        background-color: #161619;
        border: 1px solid #33333a;
        border-radius: 6px;
        selection-background-color: #6366f1;
    }

    QLineEdit#UrlInput:hover {
        border-color: #4b4b55;
    }

    QLineEdit#UrlInput:focus {
        background-color: #1b1b1f;
        border-color: #6366f1;
    }

    QPushButton#HeroButton {
        min-width: 96px;
        min-height: 40px;
        padding: 0 16px;
        color: #ffffff;
        background-color: #6366f1;
        border: 1px solid #7c7ff5;
        border-radius: 6px;
        font-weight: 600;
    }

    QPushButton#HeroButton:hover {
        background-color: #7c7ff5;
        border-color: #9396f7;
    }

    QPushButton#HeroButton:pressed {
        background-color: #5457e0;
    }

    QPushButton#HeroButton:disabled {
        color: #6b6b75;
        background-color: #1b1b1f;
        border-color: #26262b;
    }

    /* ---- Panels ---- */
    #Panel, #ControlPanel {
        background-color: #161619;
        border: 1px solid #26262b;
        border-radius: 8px;
    }

    QLabel#SectionTitle {
        color: #ffffff;
        font-size: 14px;
        font-weight: 600;
    }

    QLabel#FieldLabel {
        color: #a0a0aa;
        font-size: 11px;
        font-weight: 500;
    }

    QLabel#VideoTitle {
        color: #ffffff;
        font-size: 16px;
        font-weight: 600;
    }

    QLabel#StatusPill, QLabel#SuccessPill {
        color: #3fb950;
        background-color: rgba(63, 185, 80, 0.12);
        border: 1px solid rgba(63, 185, 80, 0.30);
        border-radius: 6px;
        padding: 3px 9px;
        font-size: 11px;
        font-weight: 600;
    }

    QLabel#InfoChip {
        color: #a0a0aa;
        background-color: #1b1b1f;
        border: 1px solid #26262b;
        border-radius: 6px;
        padding: 5px 9px;
        font-size: 11px;
    }

    QLabel#EmptyCover {
        color: #6b6b75;
        background-color: #121214;
        border: 1px dashed #33333a;
        border-radius: 8px;
    }

    QLabel#WarningBanner {
        color: #d29922;
        background-color: #2a2417;
        border: 1px solid #5c4a1f;
        border-radius: 8px;
        padding: 9px 11px;
    }

    QLabel#LoginInstructions {
        color: #a0a0aa;
        background-color: #161619;
        border: 1px solid #26262b;
        border-radius: 6px;
        padding: 9px 11px;
    }

    QLabel#LoginInstructions:focus {
        border-color: #33333a;
    }

    /* ---- Inputs ---- */
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
        min-height: 24px;
        padding: 7px 10px;
        color: #e8e8ec;
        background-color: #161619;
        border: 1px solid #33333a;
        border-radius: 6px;
        selection-background-color: #6366f1;
        selection-color: #ffffff;
    }

    QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QSpinBox:hover {
        border-color: #4b4b55;
    }

    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
        border-color: #6366f1;
        background-color: #1b1b1f;
    }

    QLineEdit:read-only {
        color: #a0a0aa;
        background-color: #1b1b1f;
    }

    QSpinBox {
        padding: 7px 10px;
    }

    QPushButton#StepperButton {
        min-width: 36px;
        max-width: 36px;
        min-height: 36px;
        max-height: 36px;
        padding: 0;
        color: #a0a0aa;
        background-color: #161619;
        border-color: #33333a;
        font-size: 17px;
        font-weight: 500;
    }

    QPushButton#StepperButton:hover {
        color: #e8e8ec;
        background-color: #1b1b1f;
        border-color: #4b4b55;
    }

    QPushButton#StepperButton:pressed {
        background-color: #1b1b1f;
    }

    QPushButton#StepperButton:disabled {
        color: #4b4b55;
        background-color: #121214;
        border-color: #1e1e22;
    }

    QComboBox {
        min-width: 138px;
        min-height: 24px;
        padding: 7px 30px 7px 10px;
        color: #e8e8ec;
        background-color: #161619;
        border: 1px solid #33333a;
        border-radius: 6px;
    }

    QComboBox:hover, QComboBox:on {
        border-color: #4b4b55;
        background-color: #1b1b1f;
    }

    QComboBox::drop-down {
        width: 28px;
        border: none;
    }

    QComboBox QAbstractItemView {
        color: #e8e8ec;
        background-color: #161619;
        border: 1px solid #33333a;
        border-radius: 6px;
        padding: 5px 0;
        selection-background-color: #1e1e3a;
        selection-color: #c7c9ff;
    }

    /* ---- Buttons ---- */
    QPushButton {
        min-height: 32px;
        padding: 0 14px;
        color: #a0a0aa;
        background-color: #1b1b1f;
        border: 1px solid #33333a;
        border-radius: 6px;
        font-weight: 500;
    }

    QPushButton:hover {
        color: #e8e8ec;
        background-color: #26262b;
        border-color: #4b4b55;
    }

    QPushButton:focus, QComboBox:focus {
        border: 1px solid #6366f1;
    }

    QCheckBox:focus {
        color: #e8e8ec;
        border: none;
    }

    QCheckBox::indicator:focus {
        border: 2px solid #7c7ff5;
    }

    QPushButton:pressed {
        background-color: #161619;
    }

    QPushButton:disabled {
        color: #4b4b55;
        background-color: #121214;
        border-color: #1e1e22;
    }

    QPushButton#PrimaryButton, QPushButton#DownloadButton {
        color: #ffffff;
        background-color: #6366f1;
        border-color: #7c7ff5;
    }

    QPushButton#PrimaryButton:hover, QPushButton#DownloadButton:hover {
        background-color: #7c7ff5;
        border-color: #9396f7;
    }

    QPushButton#DownloadButton {
        min-height: 36px;
        font-weight: 600;
    }

    QPushButton#SecondaryButton, QPushButton#SubtleButton {
        color: #a0a0aa;
        background-color: transparent;
        border-color: #33333a;
    }

    QPushButton#SecondaryButton:hover, QPushButton#SubtleButton:hover {
        color: #e8e8ec;
        background-color: #161619;
        border-color: #4b4b55;
    }

    QPushButton#DangerButton {
        color: #f85149;
        background-color: rgba(248, 81, 73, 0.10);
        border-color: rgba(248, 81, 73, 0.40);
    }

    QPushButton#DangerButton:hover {
        background-color: rgba(248, 81, 73, 0.18);
    }

    QPushButton#SuccessButton {
        color: #3fb950;
        background-color: rgba(63, 185, 80, 0.10);
        border-color: rgba(63, 185, 80, 0.40);
    }

    QPushButton#SuccessButton:hover {
        background-color: rgba(63, 185, 80, 0.18);
    }

    /* ---- Checkbox ---- */
    QCheckBox {
        spacing: 9px;
        min-height: 24px;
        padding: 2px 0;
        color: #a0a0aa;
        background-color: transparent;
        border: none;
    }

    QCheckBox:hover {
        color: #e8e8ec;
    }

    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        background-color: #161619;
        border: 1px solid #33333a;
        border-radius: 4px;
    }

    QCheckBox::indicator:hover {
        background-color: #1b1b1f;
        border-color: #4b4b55;
    }

    QCheckBox::indicator:checked {
        background-color: #6366f1;
        border-color: #7c7ff5;
        image: url("__CHECKMARK_ICON__");
    }

    QCheckBox:disabled {
        color: #4b4b55;
    }

    QCheckBox::indicator:disabled {
        background-color: #121214;
        border-color: #1e1e22;
    }

    QCheckBox::indicator:checked:disabled {
        background-color: #2a2a3a;
        border-color: #33333a;
    }

    /* ---- Table ---- */
    QTableWidget {
        min-height: 120px;
        color: #e8e8ec;
        background-color: #161619;
        alternate-background-color: #1b1b1f;
        border: 1px solid #26262b;
        border-radius: 8px;
        gridline-color: transparent;
        selection-background-color: #1e1e3a;
    }

    QTableWidget::item {
        padding: 6px 8px;
        border-bottom: 1px solid #1e1e22;
    }

    QHeaderView::section {
        padding: 9px 8px;
        color: #6b6b75;
        background-color: #121214;
        border: none;
        border-bottom: 1px solid #26262b;
        font-size: 11px;
        font-weight: 600;
    }

    QProgressBar {
        height: 18px;
        color: #a0a0aa;
        background-color: #121214;
        border: 1px solid #26262b;
        border-radius: 6px;
        text-align: center;
        font-size: 10px;
    }

    QProgressBar::chunk {
        background-color: #3fb950;
        border-radius: 5px;
    }

    QProgressBar#ErrorProgress::chunk {
        background-color: #f85149;
    }

    QStatusBar {
        color: #6b6b75;
        background-color: #0a0a0c;
        border-top: 1px solid #1e1e22;
    }

    QTabWidget::pane, QGroupBox {
        background-color: #161619;
        border: 1px solid #26262b;
        border-radius: 8px;
    }

    QTabBar::tab {
        padding: 8px 16px;
        color: #6b6b75;
        background-color: #121214;
        border: 1px solid #26262b;
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }

    QTabBar::tab:selected {
        color: #e8e8ec;
        background-color: #161619;
        border-color: #33333a;
    }

    QMenu {
        color: #e8e8ec;
        background-color: #161619;
        border: 1px solid #33333a;
        border-radius: 8px;
        padding: 5px;
    }

    QMenu::item {
        padding: 7px 28px 7px 12px;
        border-radius: 6px;
    }

    QMenu::item:selected {
        background-color: #1e1e3a;
        color: #c7c9ff;
    }
"""


LIGHT_OVERRIDES = """
    * {
        color: #1a1a1f;
    }

    QMainWindow, QDialog, #AppSurface {
        background-color: #f6f6f8;
    }

    #Workspace {
        background-color: #fbfbfc;
    }

    QScrollArea#WorkspaceScroll, QScrollArea#WorkspaceScroll > QWidget > QWidget {
        background-color: #fbfbfc;
    }

    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
        background-color: #c5c0c9;
    }

    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
        background-color: #938d99;
    }

    QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {
        background-color: #6b6b75;
    }

    #Sidebar {
        background-color: #ffffff;
        border-right: 1px solid #e4e4e9;
    }

    QLabel#BrandIcon {
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    QLabel#BrandTitle, QLabel#PageTitle, QLabel#DialogTitle,
    QLabel#SectionTitle, QLabel#VideoTitle {
        color: #1a1a1f;
    }

    QLabel#SidebarCaption, QLabel#DialogCaption,
    QLabel#Caption, QLabel#MetaLabel, QLabel#MutedLabel {
        color: #5c5c66;
    }

    QLabel#NavSection {
        color: #5c5c66;
    }

    QPushButton#NavButton, QPushButton#NavButtonActive {
        color: #5c5c66;
    }

    QPushButton#NavButton:hover {
        color: #1a1a1f;
        background-color: #f5f5f7;
    }

    QPushButton#NavButtonActive {
        color: #1a1a1f;
        background-color: #eef0fe;
        border-left-color: #5b5ee6;
    }

    #DialogPanel {
        background-color: #ffffff;
        border-color: #e4e4e9;
    }

    QPushButton#SidebarAction {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #e4e4e9;
    }

    QPushButton#SidebarAction:hover {
        background-color: #f5f5f7;
        border-color: #d0d0d8;
    }

    QPushButton#GhostButton {
        color: #5c5c66;
        background-color: #ffffff;
        border-color: #e4e4e9;
    }

    QPushButton#GhostButton:hover {
        color: #1a1a1f;
        border-color: #d0d0d8;
    }

    QLineEdit#UrlInput {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #d0d0d8;
        selection-background-color: #5b5ee6;
    }

    QLineEdit#UrlInput:hover {
        border-color: #b8b1bd;
    }

    QLineEdit#UrlInput:focus {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #5b5ee6;
    }

    QPushButton#HeroButton, QPushButton#PrimaryButton,
    QPushButton#DownloadButton {
        color: #ffffff;
        background-color: #5b5ee6;
        border-color: #6f72ec;
    }

    QPushButton#HeroButton:hover, QPushButton#PrimaryButton:hover,
    QPushButton#DownloadButton:hover {
        background-color: #6f72ec;
        border-color: #8386f0;
    }

    QPushButton#HeroButton:disabled {
        color: #8a8a93;
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    #Panel, #ControlPanel {
        background-color: #ffffff;
        border-color: #e4e4e9;
    }

    QLabel#FieldLabel {
        color: #5c5c66;
    }

    QLabel#StatusPill, QLabel#SuccessPill {
        color: #1a7f37;
        background-color: #dcfce7;
        border-color: #b4e3c4;
    }

    QLabel#InfoChip {
        color: #5c5c66;
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    QLabel#EmptyCover {
        color: #8a8a93;
        background-color: #f5f5f7;
        border-color: #d0d0d8;
    }

    QLabel#WarningBanner {
        color: #9a6700;
        background-color: #fff8c5;
        border-color: #e8c97a;
    }

    QLabel#LoginInstructions {
        color: #5c5c66;
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    QLabel#LoginInstructions:focus {
        border-color: #d0d0d8;
    }

    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #d0d0d8;
        selection-background-color: #5b5ee6;
    }

    QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QSpinBox:hover {
        border-color: #b8b1bd;
    }

    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #5b5ee6;
    }

    QLineEdit:read-only {
        color: #8a8a93;
        background-color: #f5f5f7;
    }

    QPushButton#StepperButton {
        color: #5c5c66;
        background-color: #ffffff;
        border-color: #d0d0d8;
    }

    QPushButton#StepperButton:hover {
        color: #1a1a1f;
        background-color: #f5f5f7;
        border-color: #b8b1bd;
    }

    QPushButton#StepperButton:pressed {
        background-color: #eef0fe;
    }

    QPushButton#StepperButton:disabled {
        color: #c2bdc5;
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    QComboBox {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #d0d0d8;
    }

    QComboBox:hover, QComboBox:on {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #b8b1bd;
    }

    QComboBox QAbstractItemView {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #d0d0d8;
        selection-background-color: #eef0fe;
        selection-color: #4a4dd6;
    }

    QPushButton {
        color: #5c5c66;
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    QPushButton:hover {
        color: #1a1a1f;
        background-color: #eef0fe;
        border-color: #d0d0d8;
    }

    QPushButton:focus, QComboBox:focus {
        border-color: #5b5ee6;
    }

    QCheckBox:focus {
        color: #1a1a1f;
        border: none;
    }

    QCheckBox::indicator:focus {
        border-color: #5b5ee6;
    }

    QPushButton:pressed {
        background-color: #e4e4e9;
    }

    QPushButton:disabled {
        color: #aaa4ae;
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    QPushButton#SecondaryButton, QPushButton#SubtleButton {
        color: #5c5c66;
        background-color: transparent;
        border-color: #d0d0d8;
    }

    QPushButton#SecondaryButton:hover, QPushButton#SubtleButton:hover {
        color: #1a1a1f;
        background-color: #f5f5f7;
        border-color: #b8b1bd;
    }

    QPushButton#DangerButton {
        color: #cf222e;
        background-color: #ffebe9;
        border-color: #f0a6ab;
    }

    QPushButton#DangerButton:hover {
        background-color: #ffd9d6;
    }

    QPushButton#SuccessButton {
        color: #1a7f37;
        background-color: #dcfce7;
        border-color: #9bd6b3;
    }

    QPushButton#SuccessButton:hover {
        background-color: #c6f0d4;
    }

    QCheckBox {
        color: #5c5c66;
    }

    QCheckBox::indicator {
        background-color: #ffffff;
        border-color: #d0d0d8;
    }

    QCheckBox::indicator:hover {
        background-color: #ffffff;
        border-color: #b8b1bd;
    }

    QCheckBox::indicator:checked {
        background-color: #5b5ee6;
        border-color: #5b5ee6;
    }

    QCheckBox:disabled {
        color: #aaa4ae;
    }

    QCheckBox::indicator:disabled {
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    QCheckBox::indicator:checked:disabled {
        background-color: #c5c8f5;
        border-color: #c5c8f5;
    }

    QTableWidget {
        color: #1a1a1f;
        background-color: #ffffff;
        alternate-background-color: #faf9fb;
        border-color: #e4e4e9;
        selection-background-color: #eef0fe;
    }

    QTableWidget::item {
        border-bottom-color: #eeeff2;
    }

    QHeaderView::section {
        color: #5c5c66;
        background-color: #f5f5f7;
        border-bottom-color: #e4e4e9;
    }

    QProgressBar {
        color: #5c5c66;
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    QProgressBar::chunk {
        background-color: #1a7f37;
    }

    QStatusBar {
        color: #5c5c66;
        background-color: #ffffff;
        border-top-color: #e4e4e9;
    }

    QTabWidget::pane, QGroupBox {
        background-color: #ffffff;
        border-color: #e4e4e9;
    }

    QTabBar::tab {
        color: #5c5c66;
        background-color: #f5f5f7;
        border-color: #e4e4e9;
    }

    QTabBar::tab:selected {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #d0d0d8;
    }

    QMenu {
        color: #1a1a1f;
        background-color: #ffffff;
        border-color: #d0d0d8;
    }

    QMenu::item:selected {
        color: #4a4dd6;
        background-color: #eef0fe;
    }
"""
