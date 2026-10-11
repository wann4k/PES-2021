@echo off
REM Build PES2021_Adboard_Studio.exe di Windows (butuh Python 3.10+ di PATH)
cd /d "%~dp0"
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :err
pyinstaller --noconfirm PES2021_Adboard_Studio.spec || goto :err
echo.
echo Selesai! File exe ada di: dist\PES2021_Adboard_Studio.exe
pause
exit /b 0
:err
echo Build gagal.
pause
exit /b 1
