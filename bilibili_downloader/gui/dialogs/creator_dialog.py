"""Creator index fetch, import, filter and selection dialog."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from bilibili_downloader.core.creator import (
    creator_directory_name,
    load_creator_index,
    parse_creator_mid,
    save_creator_index,
)
from bilibili_downloader.gui.threads.creator_worker import (
    CreatorIndexRunner,
    CreatorIndexWorker,
)


class CreatorDialog(QDialog):
    """Fetch or import a creator manifest before enqueueing selected videos."""

    def __init__(self, api_client, output_dir: str, parent=None):
        super().__init__(parent)
        self._api_client = api_client
        self._output_dir = Path(output_dir)
        self._index = None
        self._worker = None
        self.setWindowTitle("UP 主投稿索引")
        self.setMinimumSize(820, 560)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QLabel("UP 主全部投稿")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)

        hint = QLabel(
            "① 在下方输入 UID 或空间链接，点「开始抓取」即可拉取全部投稿；"
            "② 已有 index.json 时点「导入 index.json」直接载入，无需联网。"
        )
        hint.setObjectName("MetaLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        source_row = QHBoxLayout()
        self._source = QLineEdit()
        self._source.setPlaceholderText("输入 UID 或 space.bilibili.com 空间链接")
        self._source.returnPressed.connect(self._fetch)
        source_row.addWidget(self._source, 1)
        self._fetch_button = QPushButton("开始抓取")
        self._fetch_button.setObjectName("PrimaryButton")
        self._fetch_button.clicked.connect(self._fetch)
        source_row.addWidget(self._fetch_button)
        import_button = QPushButton("导入 index.json")
        import_button.setObjectName("SecondaryButton")
        import_button.clicked.connect(self._import_index)
        source_row.addWidget(import_button)
        layout.addLayout(source_row)

        tools = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("按标题或 BVID 筛选")
        self._filter.textChanged.connect(self._apply_filter)
        tools.addWidget(self._filter, 1)
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_visible_checked(True))
        tools.addWidget(select_all)
        select_none = QPushButton("全不选")
        select_none.clicked.connect(lambda: self._set_visible_checked(False))
        tools.addWidget(select_none)
        layout.addLayout(tools)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["选择", "BVID", "标题", "发布时间"])
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self._table, 1)

        footer = QHBoxLayout()
        self._status = QLabel("尚未载入索引")
        self._status.setObjectName("MetaLabel")
        footer.addWidget(self._status)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("所选视频加入队列")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def _fetch(self):
        source = self._source.text().strip()
        if not source:
            QMessageBox.warning(self, "UP 主索引", "请输入 UID 或空间链接")
            return
        self._fetch_button.setEnabled(False)
        self._status.setText("正在获取投稿列表...")
        resume_index = self._find_resume_index(source)
        self._worker = CreatorIndexWorker()
        runner = CreatorIndexRunner(
            self._worker,
            self._api_client,
            source,
            self._output_dir,
            resume_index,
        )
        self._worker.progress.connect(
            lambda done, total: self._status.setText(f"已获取 {done}/{total or '?'} 个投稿")
        )
        self._worker.status.connect(self._status.setText)
        self._worker.finished.connect(self._fetch_finished)
        self._worker.error.connect(self._fetch_error)
        self._runner = runner
        QThreadPool.globalInstance().start(runner)

    def _fetch_finished(self, index):
        self._fetch_button.setEnabled(True)
        self._set_index(index)
        path = (
            self._output_dir
            / creator_directory_name(index.name, index.mid)
            / "index.json"
        )
        try:
            save_creator_index(index, path)
            path.with_name("index.partial.json").unlink(missing_ok=True)
            self._status.setText(f"{len(index.videos)} 个投稿 · 索引已保存到 {path}")
        except OSError as exc:
            self._status.setText(f"{len(index.videos)} 个投稿 · 索引保存失败：{exc}")

    def _fetch_error(self, error: str):
        self._fetch_button.setEnabled(True)
        self._status.setText("索引获取失败")
        QMessageBox.critical(self, "UP 主索引", error)

    def _find_resume_index(self, source: str):
        try:
            mid = parse_creator_mid(source)
            candidates = sorted(
                self._output_dir.glob(f"*_{mid}/index.partial.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                return None
            index = load_creator_index(candidates[0])
            return index if not index.complete and index.next_cursor else None
        except (OSError, ValueError):
            return None

    def _import_index(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 UP 主索引", str(self._output_dir), "JSON (*.json)"
        )
        if not path:
            return
        try:
            self._set_index(load_creator_index(Path(path)))
            self._status.setText(f"已导入 {path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "UP 主索引", f"无法读取索引：{exc}")

    def _set_index(self, index):
        self._index = index
        self._table.setRowCount(len(index.videos))
        for row, entry in enumerate(index.videos):
            checked = QTableWidgetItem()
            checked.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            checked.setCheckState(Qt.Checked)
            self._table.setItem(row, 0, checked)
            self._table.setItem(row, 1, QTableWidgetItem(entry.bvid))
            self._table.setItem(row, 2, QTableWidgetItem(entry.title))
            published = (
                datetime.fromtimestamp(entry.published_at).strftime("%Y-%m-%d")
                if entry.published_at else ""
            )
            self._table.setItem(row, 3, QTableWidgetItem(published))
        self._apply_filter()

    def _apply_filter(self):
        needle = self._filter.text().strip().lower()
        for row in range(self._table.rowCount()):
            haystack = " ".join(
                self._table.item(row, column).text()
                for column in (1, 2)
                if self._table.item(row, column)
            ).lower()
            self._table.setRowHidden(row, bool(needle and needle not in haystack))

    def _set_visible_checked(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self._table.rowCount()):
            if not self._table.isRowHidden(row):
                self._table.item(row, 0).setCheckState(state)

    def accept(self):
        if self._index is None:
            QMessageBox.warning(self, "UP 主索引", "请先刷新或导入索引")
            return
        if not self.selected_bvids():
            QMessageBox.warning(self, "UP 主索引", "请至少选择一个视频")
            return
        super().accept()

    def selected_bvids(self) -> list[str]:
        return [
            self._table.item(row, 1).text()
            for row in range(self._table.rowCount())
            if self._table.item(row, 0).checkState() == Qt.Checked
        ]

    @property
    def creator_index(self):
        return self._index

    def reject(self):
        if self._worker is not None:
            self._worker.cancel()
        super().reject()
