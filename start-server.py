
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Получить переменные окружения (Render их инжектирует)
port = os.getenv("PORT", "8000")
host = os.getenv("HOST", "0.0.0.0")  # На Render нужно 0.0.0.0!

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m2.5:free")

# Проверка наличия обязательных переменных (опционально)
if not OPENROUTER_API_KEY:
    print("ВНИМАНИЕ: OPENROUTER_API_KEY не установлен!")
    print("Создайте файл .env с переменной OPENROUTER_API_KEY")
    print("Или установите её через окружение: export OPENROUTER_API_KEY=ваш_ключ")

# Запустить приложение
if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=int(port),
        reload=False,  # На Render не нужна перезагрузка
        workers=1
    )