# Установите зависимости: pip install fastapi uvicorn pymongo pydantic
import urllib.parse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pydantic import BaseModel,Field
from typing import Optional, List
from fastapi import Query
import traceback

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

from routine import *

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


class OptimizedPoint(BaseModel):
    veh_id: str
    rank: str
    request_id: str                   # Заявка (номер)
    lat: str
    lon: str


class OptimizeRequest(BaseModel):
    points: List[PointData]
    name: str




class AlgorithmResponse(BaseModel):
    success: bool
    reso: List[OptimizedPoint]
    
# 
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
        
import traceback
import numpy as np
import pandas as pd


from fastapi.encoders import jsonable_encoder

# ... ваш код ...
import copy
# === ВАШ ЭНДПОИНТ ===
@app.post("/api/vega")
async def post_algo(request: OptimizeRequest):
    try:
        print("1. Подготовка данных...")
        points = [p.model_dump() if hasattr(p, 'model_dump') else p.dict() for p in request.points]
        
        print("2. Запуск алгоритма...")
        resolution = calcUa(points)
        
         
        # 5. Сохранение в MongoDB
        print("5. Сохранение в collection 'ex'...")
        collection_ex = db['ex']
        collection_ex.delete_many({})
        if points:
            collection_ex.insert_many(points)
        
        print("6. Сохранение в collection 'rout'...")
        collection_rout = db['rout']
        collection_rout.delete_many({})
        collection_rout.insert_many(copy.deepcopy(resolution))
         
        print("7. Успешно!")
        
        # 6. Возврат ответа (FastAPI теперь легко превратит это в JSON)
        return jsonable_encoder({"reso": resolution, "success": True})


    except Exception as e:
        print("\n" + "="*60)
        print("❌ КРИТИЧЕСКАЯ ОШИБКА:")
        print(f"Тип ошибки: {type(e).__name__}")
        print(f"Сообщение: {str(e)}")
        print("Полный стек:")
        traceback.print_exc()
        print("="*60 + "\n")
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)