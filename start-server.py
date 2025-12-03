
import os
import json
from pathlib import Path

# Получить переменные окружения (Render их инжектирует)
port = os.getenv("PORT", "8000")
host = os.getenv("HOST", "0.0.0.0")  # На Render нужно 0.0.0.0!

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