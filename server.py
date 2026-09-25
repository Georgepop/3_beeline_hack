# Установите зависимости: pip install fastapi uvicorn pymongo pydantic
import urllib.parse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pydantic import BaseModel
from typing import Optional, List
from fastapi import Query


app = FastAPI()

# Разрешаем запросы с вашего HTML-файла (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # В продакшене замените "*" на ваш домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === ВАШИ ДАННЫЕ ПОДКЛЮЧЕНИЯ ===
username = urllib.parse.quote_plus("admin")
password = urllib.parse.quote_plus("9!qUq9dq")
host = "falupodikop.beget.app"

# Подключение к MongoDB (добавлен ?authSource=admin, что часто требуется на хостингах)
mongo_uri = f"mongodb://{username}:{password}@{host}/rote?authSource=admin"
client = MongoClient(mongo_uri)
db = client['rote']
collection = db['all']

class PointData(BaseModel):
    request_id: str                   # Заявка (номер)
    request_type_bk: str              # Тип заявки BK
    status_bk: str                    # Статус BK
    request_type_hd: str              # Тип заявки HD
    start_time: str                   # Начало
    end_time: str                     # Окончание
    district: str                     # Район
    address: str                      # Адрес
    brigade: str                      # Бригада
    gigabit_connection: bool          # Гигабитное подключение (True/False)
    train: str                        # train (например, номер поезда или линии)
    okrug: str                        # округ
    lat: str
    lon: str

@app.post("/api/points")
async def save_points(points: List[PointData]):
    try:
        # Очищаем старую коллекцию и добавляем новые данные (или используйте update)
        collection.delete_many({})
        if points:
            collection.insert_many([p.dict() for p in points])
        return {"message": f"Успешно сохранено {len(points)} точек", "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/points")
async def get_points(okrug: Optional[str] = Query(None)):
    mongo_query ={}
    if okrug:
        mongo_query={"okrug": {'$eq':okrug}}
    try:
        points = list(collection.find(mongo_query, {"_id": 0}))
        return {"points": points, "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
@app.get("/api/eng")
async def get_points():
    collection = db['eng']
    try:
        points = list(collection.find({}, {"_id": 0}))
        return {"points": points, "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)