"""Основные экраны: сейф, папки, приманки, настройки."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..core.fsutil import display_path, human_size
from ..core.win import is_admin
from .icons import pixmap
from .screens_auth import ScrollPage
from .theme import Palette
from .widgets import (
    Card,
    DangerButton,
    Divider,
    EmptyState,
    FolderCard,
    GhostButton,
    IconButton,
    PrimaryButton,
    SectionLabel,
    StatTile,
    StatusChip,
    ToggleRow,
    add_shadow,
)


class _CardList(QWidget):
    """Вертикальный список карточек с пустым состоянием."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(12)

    def clear(self) -> None:
        while self.lay.count():
            item = self.lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # deleteLater сработает позже, а спрятать и отвязать надо сейчас,
                # иначе старые карточки дорисовываются поверх новых
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def add(self, widget: QWidget) -> None:
        self.lay.addWidget(widget)


# --------------------------------------------------------------------------- #
# Сейф (главный экран)
# --------------------------------------------------------------------------- #


class VaultPage(ScrollPage):
    unlock_all = Signal()
    lock_all = Signal()
    open_folder = Signal(str)
    toggle_folder = Signal(str)
    remove_folder = Signal(str)
    add_folder = Signal()

    def __init__(self, palette: Palette, parent=None):
        super().__init__(palette, parent)
        self.palette_ = palette

        head = QHBoxLayout()
        title = QLabel("Сейф")
        title.setObjectName("PageTitle")
        self.chip = StatusChip("Защищено", "locked")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)

        self.banner = Card(flat=True)
        self.banner_text = QLabel("")
        self.banner_text.setObjectName("CardText")
        self.banner_text.setWordWrap(True)
        self.banner.add(self.banner_text)
        self.banner.hide()

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.tile_folders = StatTile("0", "папок", palette)
        self.tile_files = StatTile("0", "файлов", palette)
        self.tile_size = StatTile("0", "объём", palette)
        for tile in (self.tile_folders, self.tile_files, self.tile_size):
            stats.addWidget(tile)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_unlock = PrimaryButton("Открыть всё", "unlock", palette)
        self.btn_lock = DangerButton("Заблокировать всё", "lock", palette)
        self.btn_unlock.clicked.connect(self.unlock_all.emit)
        self.btn_lock.clicked.connect(self.lock_all.emit)
        actions.addWidget(self.btn_unlock)
        actions.addWidget(self.btn_lock)

        self.list_label = SectionLabel("Ваши папки")
        self.list = _CardList()
        self.empty = EmptyState(
            "folder-plus",
            "Пока пусто",
            "Добавьте первую папку — и её файлы исчезнут из Windows,\nоставив после себя только приманки.",
            palette,
        )
        self.add_add_button = PrimaryButton("Добавить папку", "plus", palette)
        self.add_add_button.clicked.connect(self.add_folder.emit)

        self.layout.addLayout(head)
        self.add(self.banner)
        self.layout.addLayout(stats)
        self.layout.addLayout(actions)
        self.add(self.list_label, self.list, self.empty, self.add_add_button)
        self.add_stretch(1)

    def refresh(self, folders: list, stats: dict, decoy_mode: bool, busy: bool) -> None:  # noqa: C901
        self.list.clear()
        locked_count = sum(1 for f in folders if f.locked)
        total = len(folders)

        if decoy_mode:
            self.chip.set_kind("unlocked")
            self.chip.setText("Открыто")
            self.banner_text.setText(
                "Сеанс просмотра. Изменения в этом сеансе не сохраняются."
            )
            self.banner.show()
        else:
            self.banner.hide()
            if total == 0:
                self.chip.set_kind("neutral")
                self.chip.setText("Нет папок")
            elif locked_count == total:
                self.chip.set_kind("locked")
                self.chip.setText("Всё защищено")
            elif locked_count == 0:
                self.chip.set_kind("unlocked")
                self.chip.setText("Открыто")
            else:
                self.chip.set_kind("warn")
                self.chip.setText(f"{locked_count} из {total} защищено")

        self.tile_folders.set_value(str(stats.get("folders_total", 0)))
        self.tile_files.set_value(str(stats.get("files", 0)))
        self.tile_size.set_value(stats.get("human_size", "0 Б"))

        for f in folders:
            card = FolderCard(self.palette_)
            card.name.setText(f.name)
            card.path.setText(f.display)
            state = "спрятана" if f.locked else "открыта"
            card.meta.setText(f"{f.files} файл(ов) · {f.human_size} · {state}")
            card.set_badge("lock" if f.locked else "folder", None)
            card.chip.setText("Спрятана" if f.locked else "Открыта")
            card.chip.set_kind("locked" if f.locked else "unlocked")

            actions = [("open", "external", "Открыть в проводнике")]
            if not decoy_mode and not f.is_decoy:
                actions.append(
                    ("toggle", "unlock" if f.locked else "lock", "Открыть" if f.locked else "Спрятать")
                )
                actions.append(("remove", "trash", "Убрать из сейфа"))
            card.set_actions(actions)

            path = f.path
            fid = f.folder_id
            card.action.connect(lambda key, p=path, i=fid: self._on_action(key, p, i))
            self.list.add(card)

        has = bool(folders)
        self.empty.setVisible(not has)
        self.list_label.setVisible(has)
        self.add_add_button.setVisible(not has)
        self.btn_unlock.setVisible(has and not decoy_mode)
        self.btn_lock.setVisible(has and not decoy_mode)
        for w in (self.btn_unlock, self.btn_lock):
            w.setEnabled(not busy)

    def _on_action(self, key: str, path: str, folder_id: str | None) -> None:
        if key == "open":
            self.open_folder.emit(path)
        elif key == "toggle" and folder_id:
            self.toggle_folder.emit(folder_id)
        elif key == "remove" and folder_id:
            self.remove_folder.emit(folder_id)


# --------------------------------------------------------------------------- #
# Папки
# --------------------------------------------------------------------------- #


class FoldersPage(ScrollPage):
    add_requested = Signal(str)
    remove_requested = Signal(str)
    open_requested = Signal(str)

    def __init__(self, palette: Palette, parent=None):
        super().__init__(palette, parent)
        self.palette_ = palette

        title = QLabel("Папки")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Выберите папки, которые должны исчезнуть из Windows без пароля.")
        subtitle.setObjectName("CardText")
        subtitle.setWordWrap(True)

        self.btn_add = PrimaryButton("Добавить папку", "folder-plus", palette)
        self.btn_add.setMinimumHeight(56)
        self.btn_add.clicked.connect(self._pick)

        hint = Card(flat=True)
        hint_text = QLabel(
            "Что происходит при добавлении:\n"
            "• файлы шифруются в контейнер и проверяются по sha256;\n"
            "• оригиналы стираются только после проверки;\n"
            "• в папке остаются приманки;\n"
            "• следы в «Недавних» и быстром доступе удаляются."
        )
        hint_text.setObjectName("CardText")
        hint_text.setWordWrap(True)
        hint.add(hint_text)

        self.list = _CardList()
        self.empty = EmptyState(
            "folder",
            "Папок пока нет",
            "Нажмите «Добавить папку» — она исчезнет из проводника,\nбраузеров и мессенджеров.",
            palette,
        )

        self.add(title, subtitle, self.btn_add, hint, self.list, self.empty)
        self.add_stretch(1)

    def _pick(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Выберите папку для защиты")
        if chosen:
            self.add_requested.emit(chosen)

    def refresh(self, folders: list, decoy_mode: bool, busy: bool) -> None:
        self.list.clear()
        self.btn_add.setEnabled(not decoy_mode and not busy)
        for f in folders:
            card = FolderCard(self.palette_)
            card.name.setText(f.name)
            card.path.setText(f.display)
            card.meta.setText(f"{f.files} файл(ов) · {f.human_size}")
            card.set_badge("lock" if f.locked else "folder", None)
            card.chip.setText("Спрятана" if f.locked else "Открыта")
            card.chip.set_kind("locked" if f.locked else "unlocked")
            if decoy_mode:
                card.set_actions([("open", "external", "Открыть")])
            else:
                card.set_actions(
                    [
                        ("open", "external", "Открыть в проводнике"),
                        ("remove", "trash", "Убрать из сейфа"),
                    ]
                )
            path, fid = f.path, f.folder_id
            card.action.connect(
                lambda key, p=path, i=fid: (
                    self.open_requested.emit(p)
                    if key == "open"
                    else self.remove_requested.emit(i) if key == "remove" and i else None
                )
            )
            self.list.add(card)
        self.empty.setVisible(not folders)


# --------------------------------------------------------------------------- #
# Приманки
# --------------------------------------------------------------------------- #


class DecoysPage(ScrollPage):
    add_file = Signal(str)
    add_dir = Signal(str)
    remove_item = Signal(str)
    seed = Signal()
    reapply = Signal()

    def __init__(self, palette: Palette, parent=None):
        super().__init__(palette, parent)
        self.palette_ = palette

        title = QLabel("Приманки")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Эти файлы видны, если ввести неверный пароль. Папка выглядит "
            "открытой, но внутри — только то, что вы положили сюда сами."
        )
        subtitle.setObjectName("CardText")
        subtitle.setWordWrap(True)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self.btn_file = PrimaryButton("Добавить файл", "file", palette)
        self.btn_dir = GhostButton("Добавить папку", "folder", palette)
        self.btn_file.clicked.connect(self._pick_file)
        self.btn_dir.clicked.connect(self._pick_dir)
        row1.addWidget(self.btn_file)
        row1.addWidget(self.btn_dir)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.btn_seed = GhostButton("Набор по умолчанию", "decoy", palette)
        self.btn_reapply = GhostButton("Обновить в папках", "refresh", palette)
        self.btn_seed.clicked.connect(self.seed.emit)
        self.btn_reapply.clicked.connect(self.reapply.emit)
        row2.addWidget(self.btn_seed)
        row2.addWidget(self.btn_reapply)

        tip = Card(flat=True)
        tip_text = QLabel(
            "Совет: положите сюда пару правдоподобных документов или фото. "
            "Приманки не шифруются — они и должны быть видны. Настоящие файлы "
            "в этот момент лежат только в зашифрованном контейнере."
        )
        tip_text.setObjectName("CardText")
        tip_text.setWordWrap(True)
        tip.add(tip_text)

        self.list_label = SectionLabel("На складе")
        self.list = _CardList()
        self.empty = EmptyState(
            "decoy",
            "Приманок нет",
            "Добавьте файлы-обманки или нажмите «Набор по умолчанию».",
            palette,
        )

        self.add(title, subtitle)
        self.layout.addLayout(row1)
        self.layout.addLayout(row2)
        self.add(tip, self.list_label, self.list, self.empty)
        self.add_stretch(1)

    def _pick_file(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Файлы-приманки")
        for f in files:
            self.add_file.emit(f)

    def _pick_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Папка-приманка")
        if chosen:
            self.add_dir.emit(chosen)

    def refresh(self, items: list, decoy_mode: bool, busy: bool) -> None:
        self.list.clear()
        for item in items:
            card = Card(hoverable=True)
            row = QHBoxLayout()
            row.setSpacing(12)
            badge = QLabel()
            badge.setFixedSize(40, 40)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"background: {self.palette_.accent_soft}; border-radius: 14px;")
            badge.setPixmap(pixmap("folder" if item.is_dir else "file", self.palette_.accent, 20))

            texts = QVBoxLayout()
            texts.setSpacing(2)
            name = QLabel(item.name)
            name.setObjectName("CardTitle")
            meta = QLabel(f"{'папка' if item.is_dir else 'файл'} · {item.human_size}")
            meta.setObjectName("CardText")
            texts.addWidget(name)
            texts.addWidget(meta)

            trash = IconButton("trash", self.palette_.muted, "Убрать приманку", 18)
            nm = item.name
            trash.clicked.connect(lambda _=False, n=nm: self.remove_item.emit(n))
            trash.setEnabled(not decoy_mode and not busy)

            row.addWidget(badge)
            row.addLayout(texts, 1)
            row.addWidget(trash)
            card.layout_.addLayout(row)
            self.list.add(card)

        self.empty.setVisible(not items)
        self.list_label.setVisible(bool(items))
        for b in (self.btn_file, self.btn_dir, self.btn_seed, self.btn_reapply):
            b.setEnabled(not decoy_mode and not busy)


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #


class SettingsPage(ScrollPage):
    settings_changed = Signal(dict)
    purge_now = Signal()
    verify_now = Signal()
    self_test = Signal()
    change_password = Signal()
    show_recovery = Signal()
    rotate_recovery = Signal()
    restart_explorer = Signal()
    open_vault_dir = Signal()
    unlock_everything = Signal()

    def __init__(self, palette: Palette, settings: Settings, parent=None):
        super().__init__(palette, parent)
        self.palette_ = palette
        self.settings = settings

        title = QLabel("Настройки")
        title.setObjectName("PageTitle")
        self.add(title)

        # ---- Защита ---- #
        self.add(SectionLabel("Защита"))

        self.row_autolock = Card()
        auto_head = QHBoxLayout()
        auto_title = QLabel("Автоблокировка")
        auto_title.setObjectName("CardTitle")
        auto_title.setWordWrap(True)
        auto_head.addWidget(auto_title)
        auto_head.addStretch(1)
        self.spin_autolock = QSpinBox()
        self.spin_autolock.setRange(0, 240)
        self.spin_autolock.setSuffix(" мин")
        self.spin_autolock.setValue(settings.auto_lock_minutes)
        self.spin_autolock.setFixedWidth(104)
        self.spin_autolock.valueChanged.connect(self._emit)
        auto_head.addWidget(self.spin_autolock)
        auto_hint = QLabel("0 — не блокировать автоматически. Отсчёт идёт от последнего действия.")
        auto_hint.setObjectName("CardText")
        auto_hint.setWordWrap(True)
        self.row_autolock.add(auto_hint)
        self.row_autolock.layout_.insertLayout(0, auto_head)
        self.add(self.row_autolock)

        self.t_lock_close = self._toggle(
            "Блокировать при закрытии окна",
            "«Крестик» прячет файлы и сворачивает приложение в трей.",
            settings.lock_on_window_close,
        )
        self.t_deep = self._toggle(
            "Проверять каждый файл после шифрования",
            "Медленнее, но гарантирует, что копия читается, прежде чем оригинал будет стёрт.",
            settings.deep_verify,
        )
        self.t_hide = self._toggle(
            "Скрывать служебные файлы хранилища",
            "Атрибуты «скрытый» и «системный» на контейнере с данными.",
            settings.hide_vault_files,
        )

        wipe_card = Card()
        wipe_head = QHBoxLayout()
        wipe_title = QLabel("Проходов при стирании оригиналов")
        wipe_title.setObjectName("CardTitle")
        wipe_title.setWordWrap(True)
        wipe_head.addWidget(wipe_title)
        wipe_head.addStretch(1)
        self.spin_wipe = QSpinBox()
        self.spin_wipe.setRange(0, 7)
        self.spin_wipe.setValue(settings.wipe_passes)
        self.spin_wipe.setFixedWidth(80)
        self.spin_wipe.valueChanged.connect(self._emit)
        wipe_head.addWidget(self.spin_wipe)
        wipe_hint = QLabel(
            "На SSD полная гарантия стирания недостижима без шифрования всего диска — "
            "поэтому главная защита здесь в том, что единственная копия лежит в контейнере."
        )
        wipe_hint.setObjectName("CardText")
        wipe_hint.setWordWrap(True)
        wipe_card.add(wipe_hint)
        wipe_card.layout_.insertLayout(0, wipe_head)
        self.add(wipe_card)

        self.t_hide_folders = self._toggle(
            "Полностью убирать папки, когда всё заблокировано",
            "Папки не будет ни в проводнике, ни в диалоге «добавить файл» браузера — "
            "даже с показом скрытых элементов. Приманки появляются только если "
            "кто-то введёт пароль (сеанс обманки).",
            settings.hide_folders_when_locked,
        )
        self.t_instant = self._toggle(
            "Мгновенно прятать новые файлы",
            "Пока приложение работает в трее, брошенный в защищённую папку файл "
            "исчезает через пару секунд. Если выключить — файл уносится в скрытый "
            "буфер и шифруется при следующем открытии сейфа.",
            settings.instant_protection,
        )

        # ---- Следы ---- #
        self.add(SectionLabel("Следы в Windows"))
        self.t_traces = self._toggle(
            "Чистить следы при каждой блокировке",
            "Ярлыки «Недавних», прыг-списки быстрого доступа, записи MRU в реестре.",
            settings.purge_traces,
        )
        self.t_quick = self._toggle(
            "Отключить «Недавние» и «Частые папки»",
            "Проводник перестанет показывать недавние файлы и часто используемые папки.",
            settings.disable_quick_access_lists,
        )
        self.t_thumbs = self._toggle(
            "Удалять кэш эскизов",
            "Иначе проводник может показать миниатюры спрятанных фото.",
            settings.clean_thumbnails,
        )
        self.t_mru = self._toggle(
            "Чистить MRU в реестре",
            "Списки последних открытых документов (Office, блокнот и т. п.).",
            settings.purge_recent_mru,
        )
        self.t_index = self._toggle(
            "Перестраивать индекс поиска",
            "Требует прав администратора. Полностью убирает старые имена из поиска Windows.",
            settings.rebuild_search_index,
        )

        trace_btns = QHBoxLayout()
        trace_btns.setSpacing(10)
        self.btn_purge = GhostButton("Зачистить сейчас", "broom", palette)
        self.btn_explorer = GhostButton("Перезапустить проводник", "refresh", palette)
        self.btn_purge.clicked.connect(self.purge_now.emit)
        self.btn_explorer.clicked.connect(self.restart_explorer.emit)
        trace_btns.addWidget(self.btn_purge)
        trace_btns.addWidget(self.btn_explorer)
        self.layout.addLayout(trace_btns)

        # ---- Аварийный доступ ---- #
        self.add(SectionLabel("Аварийный доступ"))
        crash_card = Card()
        crash_head = QHBoxLayout()
        crash_title = QLabel("Если приложение убить через Диспетчер задач")
        crash_title.setObjectName("CardTitle")
        crash_title.setWordWrap(True)
        crash_head.addWidget(crash_title)
        crash_head.addStretch(1)
        self.combo_crash = QComboBox()
        self.combo_crash.addItems(["Вернуть всё как было", "Спросить при запуске", "Оставить заблокированным"])
        self.combo_crash.setCurrentIndex({"auto": 0, "ask": 1, "never": 2}.get(settings.crash_recovery_mode, 0))
        self.combo_crash.setFixedWidth(190)
        self.combo_crash.currentIndexChanged.connect(self._emit)
        crash_head.addWidget(self.combo_crash)
        crash_hint = QLabel(
            "«Вернуть как было» использует аварийный ключ, привязанный к вашей учётной "
            "записи Windows: пароль не нужен, но и на чужом ПК он не сработает. "
            "Выберите «Оставить заблокированным» для максимальной защиты."
        )
        crash_hint.setObjectName("CardText")
        crash_hint.setWordWrap(True)
        self.crash_status = QLabel("")
        self.crash_status.setObjectName("CardText")
        self.crash_status.setWordWrap(True)
        crash_card.add(crash_hint, self.crash_status)
        crash_card.layout_.insertLayout(0, crash_head)
        self.add(crash_card)

        rec_btns = QHBoxLayout()
        rec_btns.setSpacing(10)
        self.btn_recovery = GhostButton("Код восстановления", "key", palette)
        self.btn_rotate = GhostButton("Создать новый код", "refresh", palette)
        self.btn_recovery.clicked.connect(self.show_recovery.emit)
        self.btn_rotate.clicked.connect(self.rotate_recovery.emit)
        rec_btns.addWidget(self.btn_recovery)
        rec_btns.addWidget(self.btn_rotate)
        self.layout.addLayout(rec_btns)

        # ---- Пароль и проверка ---- #
        self.add(SectionLabel("Пароль и проверка"))
        pw_btns = QHBoxLayout()
        pw_btns.setSpacing(10)
        self.btn_pw = PrimaryButton("Сменить пароль", "lock", palette)
        self.btn_verify = GhostButton("Проверить целостность", "shield-check", palette)
        self.btn_pw.clicked.connect(self.change_password.emit)
        self.btn_verify.clicked.connect(self.verify_now.emit)
        pw_btns.addWidget(self.btn_pw)
        pw_btns.addWidget(self.btn_verify)
        self.layout.addLayout(pw_btns)

        self.btn_selftest = GhostButton("Самопроверка шифрования", "check", palette)
        self.btn_selftest.clicked.connect(self.self_test.emit)
        self.add(self.btn_selftest)

        # ---- Хранилище ---- #
        self.add(SectionLabel("Хранилище"))
        info = Card(flat=True)
        self.info_path = QLabel("")
        self.info_path.setObjectName("CardText")
        self.info_path.setWordWrap(True)
        self.info_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info_size = QLabel("")
        self.info_size.setObjectName("CardText")
        self.info_size.setWordWrap(True)
        self.btn_open_vault = GhostButton("Открыть папку хранилища", "external", palette)
        self.btn_open_vault.clicked.connect(self.open_vault_dir.emit)
        info.add(self.info_path, self.info_size, self.btn_open_vault)
        self.add(info)

        # ---- Опасная зона ---- #
        self.add(SectionLabel("Опасная зона"))
        self.btn_unlock_all_forever = DangerButton("Снять защиту со всех папок", "unlock", palette)
        self.btn_unlock_all_forever.clicked.connect(self.unlock_everything.emit)
        self.add(self.btn_unlock_all_forever)
        danger_hint = QLabel(
            "Файлы вернутся в свои папки и останутся там открытыми. "
            "Хранилище и пароль при этом сохраняются — папки можно защитить снова."
        )
        danger_hint.setObjectName("CardText")
        danger_hint.setWordWrap(True)
        self.add(danger_hint)
        self.add_stretch(1)

    # -- помощники -------------------------------------------------------- #
    def _toggle(self, title: str, description: str, value: bool) -> ToggleRow:
        row = ToggleRow(title, description, value, self.palette_)
        row.toggled.connect(self._emit)
        self.add(row)
        return row

    def _emit(self, *_args) -> None:
        self.settings_changed.emit(self.collect())

    def collect(self) -> dict:
        return {
            "auto_lock_minutes": int(self.spin_autolock.value()),
            "wipe_passes": int(self.spin_wipe.value()),
            "lock_on_window_close": self.t_lock_close.isChecked(),
            "deep_verify": self.t_deep.isChecked(),
            "hide_vault_files": self.t_hide.isChecked(),
            "hide_folders_when_locked": self.t_hide_folders.isChecked(),
            "instant_protection": self.t_instant.isChecked(),
            "purge_traces": self.t_traces.isChecked(),
            "disable_quick_access_lists": self.t_quick.isChecked(),
            "clean_thumbnails": self.t_thumbs.isChecked(),
            "purge_recent_mru": self.t_mru.isChecked(),
            "rebuild_search_index": self.t_index.isChecked(),
            "crash_recovery_mode": ("auto", "ask", "never")[max(0, self.combo_crash.currentIndex())],
        }

    def set_admin_hint(self, admin: bool) -> None:
        if not admin:
            self.t_index.setEnabled(True)

    def refresh(self, stats: dict, settings: Settings, decoy_mode: bool, busy: bool) -> None:
        self.settings = settings
        vault_dir = Path(settings.vault_path)
        self.info_path.setText(f"Хранилище: {display_path(vault_dir)}")
        self.info_size.setText(
            f"Занято контейнером: {stats.get('human_vault', '—')} · "
            f"файлов внутри: {stats.get('files', 0)} · "
            f"приманок: {stats.get('decoys', 0)}"
        )
        emergency = stats.get("emergency", False)
        self.crash_status.setText(
            "Аварийный ключ: сохранён (работает в вашей учётной записи Windows)."
            if emergency
            else "Аварийный ключ: не сохранён. Доступен только код восстановления."
        )
        admin = is_admin()
        self.t_index.setEnabled(admin)
        if not admin:
            self.t_index.switch.setChecked(settings.rebuild_search_index, animate=False)
        for w in (
            self.btn_purge,
            self.btn_explorer,
            self.btn_recovery,
            self.btn_rotate,
            self.btn_pw,
            self.btn_verify,
            self.btn_selftest,
            self.btn_unlock_all_forever,
            self.btn_open_vault,
        ):
            w.setEnabled(not busy and not decoy_mode)
        self.spin_autolock.setEnabled(not decoy_mode)
        self.spin_wipe.setEnabled(not decoy_mode)
        self.combo_crash.setEnabled(not decoy_mode)
