@echo off
chcp 65001 > nul
cd /d "C:\Users\piard\Desktop\MegalordProject"
:loop
python -u -m megalord.megalord_daemon --mode shadow --watch --interval-sec 15 >> megalord_runner.log 2>&1
timeout /t 5 /nobreak > nul
goto loop
