@echo off
echo Запуск NTЕK Schedule Bot...
echo.

REM Активация виртуального окружения
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo Виртуальное окружение не найдено. Создайте его: python -m venv venv
    pause
    exit /b 1
)

REM Проверка config.py
if not exist "config.py" (
    echo Файл config.py не найден!
    echo Скопируйте config.example.py в config.py и заполните настройки
    pause
    exit /b 1
)

REM Запуск бота
python main.py

pause