@echo off
rem === Сборка VaultKeeper.exe в Windows ===
rem Нужен Python 3.10+ в PATH.
setlocal
cd /d "%~dp0.."

where python >nul 2>nul || (echo Python не найден в PATH & exit /b 1)

echo [1/4] Виртуальное окружение...
if not exist ".venv" python -m venv .venv
call ".venv\Scripts\activate.bat"

echo [2/4] Зависимости...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller pillow || exit /b 1

echo [3/4] Иконка...
python build\make_icon.py || exit /b 1

echo [4/4] Сборка .exe...
python build\build_exe.py || exit /b 1

echo.
echo Готово: dist\VaultKeeper.exe
endlocal
