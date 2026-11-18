@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
if not exist .env copy .env.example .env
 echo.
 echo افتح ملف .env وضع مفتاح API-Football الخاص بك، ثم شغل START_WINDOWS.bat مرة أخرى.
 findstr /C:"ضع_مفتاحك_هنا" .env >nul
 if not errorlevel 1 (
   start notepad .env
   pause
   exit /b
 )
start http://127.0.0.1:5000
python app.py
pause
