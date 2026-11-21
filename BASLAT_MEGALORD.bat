@echo off
chcp 65001 > nul
title MEGALORD 24/7 MULTI-ENGINE COMMAND CENTER (SHADOW ONLY)
color 0A

echo ===============================================================================
echo            MEGALORD META-ALLOCATOR (THE 5%%ERS CANLI BAGLANTI KESILDI)
echo ===============================================================================
echo [1] Golge Mod (Shadow Paper Tracking - Guvenli Test)
echo [2] MT5 Canli Mod [DEVRE DISI BIRAKILDI]
echo [3] Tek Dongu Analizi (--once)
echo [4] Cikis
echo ===============================================================================
set /p choice="Seciminizi yapiniz [1-4]: "

if "%choice%"=="1" (
    echo [BILGI] Golge Modda 24/7 Gozetim Baslatiliyor...
    python megalord\megalord_daemon.py --mode shadow --watch --interval-sec 15
    pause
) else if "%choice%"=="2" (
    echo [ENGEL] The 5%%ers canli emir iletimi kalici olarak devre disi birakilmistir!
    pause
) else if "%choice%"=="3" (
    echo [BILGI] Tek Dongu Calistiriliyor...
    python megalord\megalord_daemon.py --mode shadow --once
    pause
) else (
    echo Cikis yapiliyor...
)
