@echo off
cd /d "%~dp0"
echo Instalando dependencias (se necessario)...
python -m pip install -r requirements.txt -q
echo.
echo Iniciando servidor em http://127.0.0.1:8000
echo Mantenha esta janela aberta enquanto usar o sistema.
echo.
python manage.py runserver 127.0.0.1:8000
pause
