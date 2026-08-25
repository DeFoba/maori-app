from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api import api_router

app = FastAPI(title="Telegram Core Web")

# 1. Подключаем API роуты из api.py
app.include_router(api_router)

# 2. Подключаем папку static (CSS / JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# 3. Главная страница
@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")
