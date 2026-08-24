@echo off
REM ====================================================================
REM  mnsync - Abre la ventana del programa
REM
REM  Esto es lo unico que hace falta usar todas las semanas.
REM  Doble clic y listo.
REM ====================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\mnsync-app.exe" (
    echo.
    echo   Todavia no esta instalado.
    echo.
    echo   Hace doble clic en   instalar.bat
    echo   y despues volve a abrir este archivo.
    echo.
    pause
    exit /b 1
)

echo.
echo   Abriendo mnsync...
echo.
echo   Esta ventana negra queda abierta a proposito: si algo falla,
echo   el motivo aparece aca. Podes minimizarla. Se cierra sola
echo   cuando cerras el programa.
echo.

.venv\Scripts\mnsync-app.exe
if errorlevel 1 (
    echo.
    echo   ======================================================
    echo    El programa termino con un error.
    echo    Copia el texto de arriba y mandaselo a quien te paso
    echo    este programa: ahi dice que paso.
    echo   ======================================================
    echo.
    pause
)
