@echo off
chcp 65001 > nul
title MEGALORD 24/7 THE 5%ERS LIVE ENGINE
color 0A

echo ===============================================================================
echo            MEGALORD META-ALLOCATOR ^& THE 5%ERS CANLI NÖBET SERVİSİ
echo            Hesap: #26735345 (FivePercentOnline-Real)
echo ===============================================================================
echo [BILGI] The 5%ers Canli Modda 7/24 Otonom Gozetim Baslatiliyor...
echo [BILGI] Kirmizi Haber Devre Kesicisi (News Guard) Aktif.
echo [BILGI] Prop-Firm Gunluk Zarar Freni (-%%2.5) Devrede.
echo ===============================================================================

cd /d "C:\Users\piard\Desktop\MegalordProject"
"C:\Users\piard\AppData\Local\Programs\Python\Python312\python.exe" -u megalord\megalord_daemon.py --mode mt5_live --watch --interval-sec 15

pause
