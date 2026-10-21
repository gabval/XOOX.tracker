@echo off
chcp 65001 >nul
title Compilador XOOX Hunter & Sniper Pro a .EXE
color 0B

echo ==============================================================================
echo           ⚡ XOOX HUNTER & SNIPER PRO - GENERADOR DE EJECUTABLE .EXE
echo ==============================================================================
echo.

:: 1. Verificar si Python está instalado en el sistema
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] No se detecto Python en el sistema o no esta en el PATH.
    echo Asegurate de tener Python 3.10 o superior instalado y marcado en 'Add to PATH'.
    echo.
    pause
    exit /b 1
)

echo [1/3] Verificando e instalando dependencias desde requirements.txt...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ADVERTENCIA] Hubo un problema instalando alguna dependencia. Continuando con PyInstaller...
)
echo.

:: 2. Configurar argumentos de PyInstaller
echo [2/3] Compilando tracker_gui.py a un unico archivo ejecutable...
echo       (Incluyendo dependencias completas y temas de CustomTkinter)...
echo.

set ICON_FLAG=
if exist icon.ico (
    set ICON_FLAG=--icon=icon.ico
    echo [*] Icono personalizado detectado: icon.ico
)

:: Comando PyInstaller optimizado:
:: --onefile: Empaquetar todo en un unico ejecutable
:: --windowed / --noconsole: Sin consola cmd negra de fondo
:: --clean: Limpiar cache previa de compilacion
:: --collect-all customtkinter: Indispensable para incluir fuentes y temas JSON de CustomTkinter
:: --name: Nombre del .exe de salida
pyinstaller --noconsole --onefile --clean --name "XOOX_Tracker" --collect-all customtkinter %ICON_FLAG% tracker_gui.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] La compilacion con PyInstaller ha fallado.
    echo Revisa el log superior para identificar librerias faltantes.
    echo.
    pause
    exit /b %errorlevel%
)

echo.
echo ==============================================================================
echo  ✅ ¡COMPILACION EXITOSA!
echo  Tu ejecutable se encuentra en la carpeta 'dist\XOOX_Tracker.exe'
echo ==============================================================================
echo.

:: 3. Copiar config.json a la carpeta dist para comodidad del usuario
if exist config.json (
    copy /Y config.json dist\config.json >nul
    echo [*] Se copio config.json junto al ejecutable en la carpeta dist\
)
if exist cookies.json (
    copy /Y cookies.json dist\cookies.json >nul
    echo [*] Se copio cookies.json junto al ejecutable en la carpeta dist\
)

echo.
echo Abriendo la carpeta del ejecutable...
explorer dist

pause
