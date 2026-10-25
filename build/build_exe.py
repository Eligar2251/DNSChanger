"""Сборка единого VaultKeeper.exe (запускать В WINDOWS).

    python build/make_icon.py      # сначала иконка (нужна Pillow)
    pip install pyinstaller
    python build/build_exe.py

Результат: dist/VaultKeeper.exe — один файл, без консоли, с иконкой.
На Linux/macOS собрать Windows-.exe нельзя: PyInstaller не кросс-компилирует.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "build" / "app_icon.ico"


def main() -> int:
    if sys.platform != "win32":
        print("Собирать .exe нужно в Windows (PyInstaller не кросс-компилирует).")
        print("На этой машине можно запускать интерфейс из исходников:")
        print("    pip install -r requirements.txt && python -m vaultkeeper")
        return 1

    if not ICON.exists():
        print("Нет иконки — генерирую…")
        subprocess.run([sys.executable, str(ROOT / "build" / "make_icon.py")], check=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "VaultKeeper",
        "--icon",
        str(ICON),
        # всё содержимое пакета попадёт в бандл
        "--collect-submodules",
        "vaultkeeper",
        # PySide6 тянет много лишнего — отсекаем неиспользуемые модули
        "--exclude-module",
        "PySide6.QtQml",
        "--exclude-module",
        "PySide6.QtQuick",
        "--exclude-module",
        "PySide6.QtWebEngine",
        "--exclude-module",
        "PySide6.Qt3D",
        "--exclude-module",
        "PySide6.QtMultimedia",
        "--exclude-module",
        "PySide6.QtSql",
        "--exclude-module",
        "PySide6.QtTest",
        "--exclude-module",
        "matplotlib",
        "--exclude-module",
        "pytest",
        str(ROOT / "vaultkeeper" / "__main__.py"),
    ]
    print("Выполняю:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode == 0:
        exe = ROOT / "dist" / "VaultKeeper.exe"
        size_mb = exe.stat().st_size / 1e6 if exe.exists() else 0
        print(f"\nГотово: {exe} ({size_mb:.1f} МБ)")
        print("Запуск: VaultKeeper.exe   (или VaultKeeper.exe --minimized в трей)")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
