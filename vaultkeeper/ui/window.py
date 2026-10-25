"""Главное окно: телефонный форм-фактор, нижняя навигация, «простыни», тосты.

Окно без рамок, со скруглениями и мягкой тенью — как экран мобильного
приложения. Все долгоиграющие операции показывают прогресс в нижней «простыне».
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..config import Settings, default_vault_dir
from .controller import (
    STATE_DECOY,
    STATE_LOCKED,
    STATE_NO_VAULT,
    STATE_UNLOCKED,
    VaultController,
)
from .icons import icon, pixmap
from .screens_auth import CrashPage, LockPage, OnboardingPage
from .screens_main import DecoysPage, FoldersPage, SettingsPage, VaultPage
from .theme import Palette, RADIUS_WINDOW, build_qss, get_palette
from .widgets import (
    AnimatedStack,
    Card,
    ElidedLabel,
    GhostButton,
    NavBar,
    PasswordField,
    PrimaryButton,
    ProgressSheet,
    Sheet,
    StatusChip,
    Toast,
    WindowButton,
    add_shadow,
)

WINDOW_W = 420
WINDOW_H = 880
MIN_H = 640

TABS = [("Сейф", "shield-check"), ("Папки", "folder"), ("Приманки", "decoy"), ("Настройки", "gear")]
MAIN_PAGES = {"vault": 0, "folders": 1, "decoys": 2, "settings": 3}


class TitleBar(QFrame):
    """Верхняя полоса: название, статус, кнопки окна. За неё же перетаскиваем."""

    minimize_requested = Signal()
    close_requested = Signal()

    def __init__(self, palette: Palette, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(56)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")
        self._palette = palette
        self._drag_pos: QPoint | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 8, 12, 8)
        lay.setSpacing(10)

        logo = QLabel()
        logo.setFixedSize(26, 26)
        logo.setPixmap(pixmap("shield", palette.accent, 24, 2.0))

        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.title = ElidedLabel(APP_NAME)
        self.title.setObjectName("AppTitle")
        self.subtitle = ElidedLabel("папки под защитой")
        self.subtitle.setObjectName("AppSubtitle")
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)

        self.chip = StatusChip("закрыто", "locked")

        btn_min = WindowButton("minimize", palette.muted)
        btn_close = WindowButton("close", palette.muted, "CloseButton")
        btn_min.clicked.connect(self.minimize_requested.emit)
        btn_close.clicked.connect(self.close_requested.emit)

        lay.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(texts)
        lay.addStretch(1)
        lay.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(btn_min)
        lay.addWidget(btn_close)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            self._drag_pos = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            window = self.window()
            window.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_pos = None
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()


class MainWindow(QWidget):
    """Окно приложения."""

    def __init__(self, controller: VaultController, settings: Settings, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.settings = settings
        self.palette_ = get_palette(settings.theme)
        self._busy = False
        self._quitting = False

        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(WINDOW_W - 40, MIN_H)
        self.resize(WINDOW_W, WINDOW_H)
        self.setStyleSheet(build_qss(self.palette_))

        self._build_ui()
        self._connect_controller()

        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon("shield-check", self.palette_.accent, 32, 1.8))

    # ------------------------------------------------------------------ #
    # Сборка интерфейса
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.root = QFrame(self)
        self.root.setObjectName("Root")
        self.root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        add_shadow(self.root, self.palette_.shadow, blur=34, offset=8, alpha=0.55)
        outer.addWidget(self.root)

        root_lay = QVBoxLayout(self.root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        self.title_bar = TitleBar(self.palette_, self.root)
        self.title_bar.minimize_requested.connect(self._minimize)
        self.title_bar.close_requested.connect(self._on_close_clicked)
        root_lay.addWidget(self.title_bar)

        self.stack = AnimatedStack(self.root)
        root_lay.addWidget(self.stack, 1)

        self.onboarding = OnboardingPage(self.palette_, str(default_vault_dir()))
        self.lock_page = LockPage(self.palette_)
        self.crash_page = CrashPage(self.palette_)
        self.vault_page = VaultPage(self.palette_)
        self.folders_page = FoldersPage(self.palette_)
        self.decoys_page = DecoysPage(self.palette_)
        self.settings_page = SettingsPage(self.palette_, self.settings)

        for page in (
            self.onboarding,
            self.lock_page,
            self.crash_page,
            self.vault_page,
            self.folders_page,
            self.decoys_page,
            self.settings_page,
        ):
            self.stack.addWidget(page)

        self.nav = NavBar(TABS, self.palette_, self.root)
        root_lay.addWidget(self.nav)

        # ---- оверлеи ---- #
        self.toast = Toast(self.root, self.palette_)
        self.progress = ProgressSheet(self.root, self.palette_, "Работаем…")
        self._sheets: dict[str, Sheet] = {}
        self._build_sheets()

        # ---- связи страниц ---- #
        self.onboarding.create.connect(self._on_create_vault)
        self.lock_page.submit.connect(self.controller.submit_password)
        self.lock_page.recover_requested.connect(self._on_emergency)
        self.lock_page.code_requested.connect(lambda: self._open_sheet("code"))
        self.crash_page.restore.connect(self.controller.restore_after_crash)
        self.crash_page.keep_locked.connect(self.controller.keep_locked_after_crash)

        self.vault_page.unlock_all.connect(self.controller.unlock_all)
        self.vault_page.lock_all.connect(lambda: self.controller.lock_now())
        self.vault_page.open_folder.connect(self.controller.open_in_explorer)
        self.vault_page.toggle_folder.connect(self.controller.toggle_folder)
        self.vault_page.remove_folder.connect(self._ask_remove_folder)
        self.vault_page.add_folder.connect(lambda: self._select_tab(1))

        self.folders_page.add_requested.connect(self._on_add_folder)
        self.folders_page.remove_requested.connect(self._ask_remove_folder)
        self.folders_page.open_requested.connect(self.controller.open_in_explorer)

        self.decoys_page.add_file.connect(self.controller.add_decoy)
        self.decoys_page.add_dir.connect(self.controller.add_decoy)
        self.decoys_page.remove_item.connect(self.controller.remove_decoy)
        self.decoys_page.seed.connect(self.controller.seed_decoys)
        self.decoys_page.reapply.connect(self.controller.reapply_decoys)

        self.settings_page.settings_changed.connect(self._on_settings_changed)
        self.settings_page.purge_now.connect(self.controller.purge_traces)
        self.settings_page.verify_now.connect(lambda: self.controller.verify_integrity(deep=True))
        self.settings_page.self_test.connect(self.controller.self_test)
        self.settings_page.change_password.connect(lambda: self._open_sheet("password"))
        self.settings_page.show_recovery.connect(lambda: self._open_sheet("recovery"))
        self.settings_page.rotate_recovery.connect(self.controller.rotate_recovery_code)
        self.settings_page.restart_explorer.connect(self._restart_explorer)
        self.settings_page.open_vault_dir.connect(self._open_vault_dir)
        self.settings_page.unlock_everything.connect(self._unlock_everything)

        self.nav.tab_selected.connect(self._on_tab)

    def _build_sheets(self) -> None:
        p = self.palette_

        # смена пароля
        sheet = Sheet(self.root, p, "Смена пароля")
        self.pw_old = PasswordField("Текущий пароль", p)
        self.pw_new = PasswordField("Новый пароль", p)
        self.pw_new2 = PasswordField("Повторите новый пароль", p)
        pw_hint = QLabel("Данные не перешифровываются — смена пароля занимает секунды.")
        pw_hint.setObjectName("CardText")
        pw_hint.setWordWrap(True)
        pw_btn = PrimaryButton("Сохранить новый пароль", "check", p)
        pw_btn.clicked.connect(self._do_change_password)
        for w in (self.pw_old, self.pw_new, self.pw_new2, pw_hint, pw_btn):
            sheet.add_widget(w)
        self._sheets["password"] = sheet

        # код восстановления — показать
        sheet = Sheet(self.root, p, "Код восстановления")
        self.recovery_view = QLineEdit()
        self.recovery_view.setReadOnly(True)
        self.recovery_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recovery_view.setStyleSheet("font-size: 18px; letter-spacing: 2px; font-weight: 700;")
        recovery_text = QLabel(
            "Этот код открывает сейф, если пароль забыт. Он показывается один раз — "
            "перепишите его на бумагу и храните отдельно от компьютера."
        )
        recovery_text.setObjectName("CardText")
        recovery_text.setWordWrap(True)
        row = QHBoxLayout()
        copy_btn = GhostButton("Скопировать", "decoy", p)
        copy_btn.clicked.connect(self._copy_recovery)
        rotate_btn = GhostButton("Создать новый", "refresh", p)
        rotate_btn.clicked.connect(self.controller.rotate_recovery_code)
        row.addWidget(copy_btn)
        row.addWidget(rotate_btn)
        sheet.add_widget(self.recovery_view)
        sheet.add_widget(recovery_text)
        holder = QWidget()
        holder.setLayout(row)
        sheet.add_widget(holder)
        self._sheets["recovery"] = sheet

        # код восстановления — ввести
        sheet = Sheet(self.root, p, "Вход по коду восстановления")
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        self.code_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code_text = QLabel("Код заменяет пароль: введите его, чтобы открыть сейф.")
        code_text.setObjectName("CardText")
        code_text.setWordWrap(True)
        code_btn = PrimaryButton("Открыть сейф", "key", p)
        code_btn.clicked.connect(self._do_recovery_code)
        sheet.add_widget(self.code_input)
        sheet.add_widget(code_text)
        sheet.add_widget(code_btn)
        self._sheets["code"] = sheet

        # подтверждение удаления папки
        sheet = Sheet(self.root, p, "Убрать папку из сейфа?")
        self.remove_text = QLabel("")
        self.remove_text.setObjectName("CardText")
        self.remove_text.setWordWrap(True)
        remove_note = QLabel(
            "Файлы вернутся в папку и останутся открытыми. Приманки будут убраны."
        )
        remove_note.setObjectName("CardText")
        remove_note.setWordWrap(True)
        self.remove_btn = PrimaryButton("Да, вернуть файлы", "unlock", p)
        self.remove_btn.clicked.connect(self._do_remove_folder)
        cancel_btn = GhostButton("Отмена", None, p)
        cancel_btn.clicked.connect(sheet.close_sheet)
        sheet.add_widget(self.remove_text)
        sheet.add_widget(remove_note)
        sheet.add_widget(self.remove_btn)
        sheet.add_widget(cancel_btn)
        self._sheets["remove"] = sheet

    # ------------------------------------------------------------------ #
    # Связь с контроллером
    # ------------------------------------------------------------------ #

    def _connect_controller(self) -> None:
        c = self.controller
        c.state_changed.connect(self._on_state)
        c.folders_changed.connect(self.refresh_all)
        c.busy_changed.connect(self._on_busy)
        c.progress_signal.connect(self._on_progress)
        c.toast.connect(self.show_toast)
        c.crash_detected.connect(self._on_crash_detected)
        c.settings_changed.connect(self.refresh_all)

    def start(self) -> None:
        state = self.controller.bootstrap()
        self._on_state(state)
        self.refresh_all()

    def _on_state(self, state: str) -> None:
        index = {
            STATE_NO_VAULT: self.stack.indexOf(self.onboarding),
            STATE_LOCKED: self.stack.indexOf(self.lock_page),
            STATE_DECOY: self.stack.indexOf(self.vault_page),
            STATE_UNLOCKED: self.stack.indexOf(self.vault_page),
        }.get(state, self.stack.indexOf(self.lock_page))
        self.stack.go_to(index)

        main = state in (STATE_UNLOCKED, STATE_DECOY)
        self.nav.setVisible(main)
        if main:
            self.nav.select(MAIN_PAGES["vault"], emit=False)

        chip_kind = {
            STATE_NO_VAULT: ("нет сейфа", "neutral"),
            STATE_LOCKED: ("закрыто", "locked"),
            STATE_DECOY: ("открыто", "unlocked"),
            STATE_UNLOCKED: ("открыто", "unlocked"),
        }.get(state, ("закрыто", "locked"))
        self.title_bar.chip.setText(chip_kind[0])
        self.title_bar.chip.set_kind(chip_kind[1])
        self.title_bar.subtitle.setText(
            "сеанс просмотра" if state == STATE_DECOY else "папки под защитой"
        )
        self.lock_page.set_emergency_visible(self.controller.vault is not None and self.controller.vault.emergency_available)
        if state == STATE_LOCKED and self.controller.vault is not None:
            locked = self.controller.vault.read_state().get("phase") == "locked"
            self.lock_page.set_info(
                "Все папки зашифрованы" if locked else "Часть файлов сейчас открыта"
            )
        else:
            self.lock_page.set_info("")

    def _on_crash_detected(self, crashed: bool) -> None:
        if not crashed:
            return
        if self.settings.crash_recovery_mode == "ask":
            self.crash_page.set_emergency_available(
                self.controller.vault is not None and self.controller.vault.emergency_available
            )
            self.stack.go_to(self.stack.indexOf(self.crash_page))
            self.nav.setVisible(False)
        elif self.settings.crash_recovery_mode == "auto":
            self.show_toast("Прошлый сеанс оборвался — возвращаем файлы", "warn")

    def _on_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.progress.set_busy("Работаем…")
            self.progress.open_sheet()
        else:
            self.progress.set_finished()
            QTimer.singleShot(160, self.progress.close_sheet)
        self.refresh_all()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total <= 0:
            self.progress.set_busy(message or "Работаем…")
        else:
            self.progress.set_progress(done, total, message)

    def show_toast(self, text: str, kind: str = "info") -> None:
        self.toast.show_message(text, kind)

    def refresh_all(self) -> None:
        folders = self.controller.folders()
        stats = self.controller.stats()
        decoy = self.controller.decoy_mode
        busy = self.controller.busy
        self.vault_page.refresh(folders, stats, decoy, busy)
        self.folders_page.refresh(folders, decoy, busy)
        self.decoys_page.refresh(self.controller.decoy_items(), decoy, busy)
        self.settings_page.refresh(stats, self.settings, decoy, busy)

    # ------------------------------------------------------------------ #
    # Навигация
    # ------------------------------------------------------------------ #

    def _on_tab(self, index: int) -> None:
        self.controller.touch()
        pages = [self.vault_page, self.folders_page, self.decoys_page, self.settings_page]
        if 0 <= index < len(pages):
            self.stack.go_to(self.stack.indexOf(pages[index]))

    def _select_tab(self, index: int) -> None:
        self.nav.select(index)

    # ------------------------------------------------------------------ #
    # Действия
    # ------------------------------------------------------------------ #

    def _on_create_vault(self, password: str, path: str) -> None:
        self.controller.create_vault(password, path)
        self.onboarding.reset()

    def _on_add_folder(self, path: str) -> None:
        plan = self.controller.plan_folder(path)
        if plan is None:
            return
        if not plan.ok:
            problems = plan.problems + [f"{rel}: {why}" for rel, why in plan.skipped[:4]]
            self.show_toast("; ".join(problems), "error")
            return
        self.controller.add_folder(path)

    def _ask_remove_folder(self, folder_id: str) -> None:
        folder = next((f for f in self.controller.folders() if f.folder_id == folder_id), None)
        if folder is None:
            return
        self._pending_remove = folder_id
        self.remove_text.setText(f"Папка: {folder.name}\n{folder.display}")
        self._open_sheet("remove")

    def _do_remove_folder(self) -> None:
        folder_id = getattr(self, "_pending_remove", None)
        self._sheets["remove"].close_sheet()
        if folder_id:
            self._pending_remove = None
            self.controller.remove_folder(folder_id)

    def _do_change_password(self) -> None:
        old = self.pw_old.text()
        new = self.pw_new.text()
        new2 = self.pw_new2.text()
        if not old:
            self.show_toast("Введите текущий пароль", "warn")
            return
        if len(new) < 4:
            self.show_toast("Новый пароль слишком короткий", "warn")
            return
        if new != new2:
            self.show_toast("Новые пароли не совпадают", "warn")
            return
        self.pw_old.clear()
        self.pw_new.clear()
        self.pw_new2.clear()
        self._sheets["password"].close_sheet()
        self.controller.change_password(old, new)

    def _do_recovery_code(self) -> None:
        code = self.code_input.text().strip()
        if not code:
            self.show_toast("Введите код восстановления", "warn")
            return
        self.code_input.clear()
        self._sheets["code"].close_sheet()
        self.controller.recover_with_code(code)

    def _copy_recovery(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.recovery_view.text())
            self.show_toast("Код скопирован в буфер обмена", "ok")

    def _on_emergency(self) -> None:
        if self.controller.vault is None or not self.controller.vault.emergency_available:
            self.show_toast("Аварийный ключ недоступен", "warn")
            return
        self.controller.recover_emergency()

    def _restart_explorer(self) -> None:
        from ..core.traces import restart_explorer_now

        if restart_explorer_now():
            self.show_toast("Проводник перезапущен, кэш эскизов очищен", "ok")
        else:
            self.show_toast("Не удалось перезапустить проводник", "error")

    def _open_vault_dir(self) -> None:
        path = Path(self.settings.vault_path)
        if path.exists():
            self.controller.open_in_explorer(str(path))
        else:
            self.show_toast("Папка хранилища ещё не создана", "warn")

    def _unlock_everything(self) -> None:
        self.show_toast("Возвращаем файлы в папки…", "info")
        self.controller.unlock_all()

    def _on_settings_changed(self, changes: dict) -> None:
        self.controller.update_settings(**changes)
        self.settings = self.controller.settings

    # ------------------------------------------------------------------ #
    # Окно
    # ------------------------------------------------------------------ #

    def _open_sheet(self, name: str) -> None:
        sheet = self._sheets.get(name)
        if sheet is None:
            return
        if name == "recovery":
            code = getattr(self.controller, "recovery_code", None)
            if code:
                self.recovery_view.setText(code)
                self.recovery_view.setEnabled(True)
            else:
                self.recovery_view.setText("код показывается один раз")
                self.recovery_view.setEnabled(False)
        sheet.open_sheet()

    def _minimize(self) -> None:
        self.controller.touch()
        self.showMinimized()

    def _on_close_clicked(self) -> None:
        """«Крестик»: блокируем файлы и уходим в трей (или выходим)."""
        if self.settings.lock_on_window_close:
            if self.controller.state in (STATE_UNLOCKED, STATE_DECOY):
                self.controller.lock_now(silent=True)
            self.hide()
            self.show_toast("VaultKeeper свёрнут в трей", "info")
            return
        self.hide()

    def quit_app(self) -> None:
        self._quitting = True
        self.controller.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._quitting:
            self.controller.shutdown()
            event.accept()
            return
        # по умолчанию — свернуться в трей, не оставляя файлы открытыми
        self._on_close_clicked()
        event.ignore()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            for sheet in self._sheets.values():
                if sheet.isVisible():
                    sheet.close_sheet()
                    return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.controller.touch()
        super().mousePressEvent(event)
