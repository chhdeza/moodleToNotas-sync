@echo off
REM ====================================================================
REM  mnsync - Instalador para Windows
REM  Se puede ejecutar las veces que haga falta: no borra tu .env
REM  ni tu courses.yml si ya existen.
REM ====================================================================
setlocal
cd /d "%~dp0"

echo.
echo ======================================================================
echo   Instalando mnsync
echo ======================================================================
echo.

echo [1/5] Buscando Python 3.10 o superior...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: no se encontro Python.
    echo.
    echo   Instalalo desde https://www.python.org/downloads/
    echo   IMPORTANTE: marca la casilla "Add Python to PATH".
    echo.
    pause
    exit /b 1
)
python --version

echo.
echo [2/5] Trayendo el script de Notas Parciales...
if not exist "vendor\grade-uploader\notasparciales_upload.py" (
    git submodule update --init --recursive
    if errorlevel 1 (
        echo.
        echo   ERROR: no se pudo traer el submodulo.
        echo   Revisa que tengas conexion a internet y que git este instalado.
        echo.
        pause
        exit /b 1
    )
)
echo   Listo.

echo.
echo [3/5] Creando el entorno virtual...
if not exist ".venv" python -m venv .venv
echo   Listo.

echo.
echo [4/5] Instalando...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -e .
if errorlevel 1 (
    echo.
    echo   ERROR: no se pudieron instalar las dependencias.
    echo   Reintentando para mostrar el error real...
    echo.
    .venv\Scripts\python.exe -m pip install -e .
    pause
    exit /b 1
)
echo   Listo.

echo.
echo [5/5] Creando los archivos de configuracion...
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo   Se creo .env  ^(hay que completarlo^)
) else (
    echo   .env ya existia: no se toco.
)
if not exist "courses.yml" (
    copy /y "courses.example.yml" "courses.yml" >nul
    echo   Se creo courses.yml  ^(hay que editarlo^)
) else (
    echo   courses.yml ya existia: no se toco.
)

echo.
echo ======================================================================
echo   Instalacion terminada
echo ======================================================================
echo.
echo   SIGUIENTE PASO:
echo.
echo     1. Abri el archivo .env con el Bloc de notas y poné tus
echo        usuarios y contrasenas.
echo.
echo     2. Ejecuta esto para comprobar que todo quedo bien:
echo.
echo        .venv\Scripts\mnsync.exe doctor
echo.
pause
