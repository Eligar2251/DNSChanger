"""Визуальная тема: палитра, QSS, шрифты.

Дизайн намеренно «мобильный»: тёмный градиент, крупные скруглённые карточки,
нижняя навигация, большие кнопки-пилюли, много воздуха.
"""

from __future__ import annotations

from dataclasses import dataclass

FONT_FAMILY = "Segoe UI"
FONT_FALLBACKS = ("Segoe UI Variable", "Inter", "Roboto", "Noto Sans", "DejaVu Sans", "Arial")

RADIUS_WINDOW = 28
RADIUS_CARD = 20
RADIUS_BUTTON = 16
RADIUS_FIELD = 14


@dataclass(frozen=True)
class Palette:
    name: str
    bg_top: str
    bg_bottom: str
    surface: str
    surface_alt: str
    surface_hover: str
    text: str
    muted: str
    accent: str
    accent_soft: str
    accent2: str
    danger: str
    danger_soft: str
    success: str
    warning: str
    border: str
    shadow: str
    nav_bg: str
    field_bg: str

    def gradient_css(self, angle: int = 160) -> str:
        return f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {self.bg_top}, stop:1 {self.bg_bottom})"


NIGHT = Palette(
    name="night",
    bg_top="#0C1128",
    bg_bottom="#171E3D",
    surface="#1B2348",
    surface_alt="#222B57",
    surface_hover="#2A356A",
    text="#F2F5FF",
    muted="#8E97C4",
    accent="#7C6CFF",
    accent_soft="#2E2A63",
    accent2="#4FD1C5",
    danger="#FF6B7A",
    danger_soft="#3A2130",
    success="#4ADE80",
    warning="#FBBF24",
    border="#2C366B",
    shadow="#05070F",
    nav_bg="#131A38",
    field_bg="#141B3A",
)

LIGHT = Palette(
    name="light",
    bg_top="#EEF1FB",
    bg_bottom="#E2E7F7",
    surface="#FFFFFF",
    surface_alt="#F5F7FE",
    surface_hover="#EAEFFC",
    text="#151A32",
    muted="#6B7392",
    accent="#5B4BE0",
    accent_soft="#E4E0FF",
    accent2="#0FA396",
    danger="#E0455A",
    danger_soft="#FDE7EA",
    success="#12A150",
    warning="#D98A00",
    border="#DDE3F5",
    shadow="#B9C2DE",
    nav_bg="#FFFFFF",
    field_bg="#F3F5FD",
)

THEMES = {"night": NIGHT, "light": LIGHT}


def get_palette(name: str) -> Palette:
    return THEMES.get(name, NIGHT)


def build_qss(p: Palette) -> str:
    """QSS всей оболочки. Пишется одним куском, чтобы не было рассинхрона стилей."""
    families = ", ".join(f'"{f}"' for f in (FONT_FAMILY, *FONT_FALLBACKS))
    return f"""
* {{
    font-family: {families};
    outline: none;
}}

QWidget#Root {{
    background: {p.gradient_css()};
    border-radius: {RADIUS_WINDOW}px;
}}

QWidget#LockRoot {{
    background: {p.gradient_css()};
    border-radius: {RADIUS_WINDOW}px;
}}

QWidget#PageHost, QWidget#ContentHost {{
    background: transparent;
}}

/* ---------- шапка ---------- */
QLabel#AppTitle {{
    color: {p.text};
    font-size: 19px;
    font-weight: 700;
    letter-spacing: 0.2px;
}}
QLabel#AppSubtitle {{
    color: {p.muted};
    font-size: 12px;
}}
QLabel#PageTitle {{
    color: {p.text};
    font-size: 24px;
    font-weight: 700;
}}
QLabel#SectionTitle {{
    color: {p.muted};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.1px;
}}

/* ---------- карточки ---------- */
QFrame#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#CardFlat {{
    background: {p.surface_alt};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#Card[hoverable="true"]:hover {{
    background: {p.surface_hover};
}}
QFrame#Divider {{
    background: {p.border};
    max-height: 1px;
    border: none;
}}
QLabel#CardTitle {{
    color: {p.text};
    font-size: 15px;
    font-weight: 650;
}}
QLabel#CardText {{
    color: {p.muted};
    font-size: 12px;
}}
QLabel#CardValue {{
    color: {p.text};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#BigNumber {{
    color: {p.text};
    font-size: 26px;
    font-weight: 800;
}}

/* ---------- статусы ---------- */
QLabel[chip="locked"] {{
    background: {p.danger_soft};
    color: {p.danger};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel[chip="unlocked"] {{
    background: rgba(74, 222, 128, 0.14);
    color: {p.success};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel[chip="neutral"] {{
    background: {p.surface_alt};
    color: {p.muted};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel[chip="warn"] {{
    background: rgba(251, 191, 36, 0.14);
    color: {p.warning};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}

/* ---------- кнопки ---------- */
QPushButton {{
    background: {p.surface_alt};
    color: {p.text};
    border: none;
    border-radius: {RADIUS_BUTTON}px;
    padding: 12px 18px;
    font-size: 14px;
    font-weight: 650;
}}
QPushButton:hover {{ background: {p.surface_hover}; }}
QPushButton:pressed {{ background: {p.accent_soft}; }}
QPushButton:disabled {{ color: {p.muted}; background: {p.surface}; }}

QPushButton[style="primary"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {p.accent}, stop:1 {p.accent2});
    color: #FFFFFF;
    border-radius: 18px;
    padding: 15px 20px;
    font-size: 15px;
    font-weight: 700;
}}
QPushButton[style="primary"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {p.accent2}, stop:1 {p.accent});
}}
QPushButton[style="primary"]:pressed {{ background: {p.accent}; }}
QPushButton[style="primary"]:disabled {{ background: {p.surface}; color: {p.muted}; }}

QPushButton[style="danger"] {{
    background: {p.danger_soft};
    color: {p.danger};
    border-radius: 18px;
    padding: 14px 20px;
    font-weight: 700;
}}
QPushButton[style="danger"]:hover {{ background: {p.danger}; color: #FFFFFF; }}

QPushButton[style="ghost"] {{
    background: transparent;
    color: {p.muted};
    border: 1px solid {p.border};
    border-radius: 18px;
    padding: 13px 18px;
}}
QPushButton[style="ghost"]:hover {{ background: {p.surface_alt}; color: {p.text}; }}

QPushButton[style="icon"] {{
    background: transparent;
    border-radius: 12px;
    padding: 6px;
    min-width: 20px;
}}
QPushButton[style="icon"]:hover {{ background: {p.surface_hover}; }}

QPushButton[style="winbtn"] {{
    background: transparent;
    border: none;
    border-radius: 10px;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
}}
QPushButton[style="winbtn"]:hover {{ background: {p.surface_hover}; }}
QPushButton#CloseButton:hover {{ background: {p.danger}; }}

/* ---------- поля ввода ---------- */
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background: {p.field_bg};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {RADIUS_FIELD}px;
    padding: 12px 14px;
    font-size: 14px;
    selection-background-color: {p.accent};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {p.accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {p.muted}; }}
QLineEdit#PasswordField {{
    font-size: 17px;
    padding: 16px 18px;
    border-radius: 18px;
    letter-spacing: 1px;
}}
QLineEdit[invalid="true"] {{ border: 1px solid {p.danger}; }}

QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 12px;
    selection-background-color: {p.accent};
    padding: 6px;
}}

/* ---------- списки ---------- */
QScrollArea {{ border: none; background: transparent; }}
QListWidget {{
    background: transparent;
    border: none;
    color: {p.text};
    font-size: 14px;
}}
QListWidget::item {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS_CARD}px;
    padding: 14px;
    margin-bottom: 10px;
    color: {p.text};
}}
QListWidget::item:selected {{ background: {p.surface_hover}; border: 1px solid {p.accent}; }}
QListWidget::item:hover {{ background: {p.surface_hover}; }}

/* ---------- прогресс ---------- */
QProgressBar {{
    background: {p.field_bg};
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {p.accent}, stop:1 {p.accent2});
}}

QSlider::groove:horizontal {{
    height: 6px;
    background: {p.field_bg};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {p.accent};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {p.text};
    width: 20px;
    height: 20px;
    margin: -7px 0;
    border-radius: 10px;
}}

/* ---------- переключатели (чекбоксы рисуем сами) ---------- */
QCheckBox {{
    background: transparent;
    color: {p.text};
    spacing: 12px;
    font-size: 14px;
}}
QCheckBox::indicator {{
    width: 46px;
    height: 26px;
    border-radius: 13px;
    background: {p.field_bg};
    border: 1px solid {p.border};
}}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border: 1px solid {p.accent};
}}

/* ---------- нижняя навигация ---------- */
QFrame#NavBar {{
    background: {p.nav_bg};
    border-top: 1px solid {p.border};
    border-bottom-left-radius: {RADIUS_WINDOW}px;
    border-bottom-right-radius: {RADIUS_WINDOW}px;
}}
QPushButton[nav="true"] {{
    background: transparent;
    color: {p.muted};
    border: none;
    border-radius: 16px;
    padding: 6px 2px 4px 2px;
    font-size: 10px;
    font-weight: 600;
}}
QPushButton[nav="true"][active="true"] {{
    color: {p.accent};
    background: {p.accent_soft};
}}
QFrame#NavIndicator {{
    background: {p.accent};
    border-radius: 3px;
    max-height: 6px;
    min-height: 6px;
}}

/* ---------- всплывающие сообщения ---------- */
QFrame#Toast {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: 16px;
}}
QLabel#ToastText {{ color: {p.text}; font-size: 13px; }}

QFrame#Sheet {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-top-left-radius: 26px;
    border-top-right-radius: 26px;
}}
QFrame#SheetDim {{ background: rgba(4, 6, 16, 0.55); }}
QFrame#SheetHandle {{ background: {p.border}; border-radius: 3px; min-height: 6px; max-height: 6px; }}

/* ---------- экран блокировки ---------- */
QLabel#LockClock {{
    color: {p.text};
    font-size: 46px;
    font-weight: 800;
    letter-spacing: -1px;
}}
QLabel#LockDate {{ color: {p.muted}; font-size: 13px; letter-spacing: 0.4px; }}
QLabel#LockHint {{ color: {p.muted}; font-size: 12px; }}
QLabel#LockStatus {{ color: {p.text}; font-size: 13px; font-weight: 600; }}

/* ---------- прочее ---------- */
QToolTip {{
    background: {p.surface_alt};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px 8px;
}}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QMenu {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 14px;
    padding: 6px;
}}
QMenu::item {{ padding: 8px 16px; border-radius: 10px; }}
QMenu::item:selected {{ background: {p.accent_soft}; }}
"""
