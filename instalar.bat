@echo off
REM ====================================================================
REM  mnsync - Instalador para Windows
REM  Se puede ejecutar las veces que haga falta: no borra nada tuyo.
REM ====================================================================
setlocal
cd /d "%~dp0"

echo.
echo ======================================================================
echo   Instalando mnsync
echo ======================================================================
echo.

echo [1/4] Buscando Python 3.10 o superior...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: no se encontro Python.
    echo.
    echo   Instalalo desde https://www.python.org/downloads/
    echo   IMPORTANTE: marca la casilla "Add Python to PATH".
    echo.
    echo   Despues de instalarlo, cerra esta ventana y volve a
    echo   hacer doble clic en instalar.bat
    echo.
    pause
    exit /b 1
)
python --version

echo.
echo [2/4] Trayendo el script de Notas Parciales...
if not exist "vendor\grade-uploader\notasparciales_upload.py" (
    git --version >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   ERROR: no se encontro git, y falta una pieza del programa.
        echo.
        echo   Instalalo desde https://git-scm.com/download/win
        echo   Despues volve a hacer doble clic en instalar.bat
        echo.
        pause
        exit /b 1
    )
    git submodule update --init --recursive
    if errorlevel 1 (
        echo.
        echo   ERROR: no se pudo traer esa pieza.
        echo   Revisa que tengas conexion a internet.
        echo.
        pause
        exit /b 1
    )
)
echo   Listo.

echo.
echo [3/4] Creando el entorno...
if not exist ".venv" python -m venv .venv
echo   Listo.

echo.
echo [4/4] Instalando el programa y su ventana...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -e ".[gui]"
if errorlevel 1 (
    echo.
    echo   ERROR: no se pudieron instalar las dependencias.
    echo   Reintentando para mostrar el error real...
    echo.
    .venv\Scripts\python.exe -m pip install -e ".[gui]"
    pause
    exit /b 1
)
echo   Listo.

REM  A proposito NO se crean .env ni courses.yml.
REM
REM  El asistente escribe courses.yml solo, y guarda las contrasenas en el
REM  Administrador de credenciales de Windows. Copiar los archivos de ejemplo
REM  dejaria valores de mentira que el programa tomaria por buenos: los datos
REM  del .env le ganan a los del Administrador de credenciales, asi que un
REM  .env de ejemplo taparia las credenciales de verdad y nada funcionaria,
REM  sin decir por que.

echo.
echo ======================================================================
echo   Listo. Ya podes usarlo.
echo ======================================================================
echo.
echo   SIGUIENTE PASO:
echo.
echo     Hace doble clic en   abrir.bat
echo.
echo   La primera vez te va a pedir tus dos contrasenas y los datos
echo   de tu curso. Despues de eso, solo abris y sincronizas.
echo.
pause
