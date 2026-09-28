import math
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
from pymongo import MongoClient
import urllib.parse
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
username = urllib.parse.quote_plus("admin")             # Логин админстратора
password = urllib.parse.quote_plus("9!qUq9dq")          # Пароль администратора
from mapb import *

host = "falupodikop.beget.app"  # Адрес сервера / доменное имя

client = MongoClient(f"mongodb://{username}:{password}@{host}")
db = client['rote']
print(f"mongodb://{username}:{password}@{host}")



import requests
import itertools
def get_loc_matrix( location):
        res_dis_all = location[['lon', 'lat']].astype(str).apply(lambda x: ','.join(x), axis=1)
        res_dis = list(res_dis_all.unique())
        fin = np.arange(len(res_dis))
        d_matrix = pd.DataFrame(columns=res_dis, index=res_dis)
        res_dis_str = ';'.join(res_dis)
        url = 'http://router.project-osrm.org/table/v1/driving/' + res_dis_str + '?annotations=distance'
        lrin = []
        for i in range(fin.max() // 100 + 1):
            lrin += [list(fin[i * 100:i * 100 + 100])]
        lrin = list(itertools.product(lrin, lrin))
        for k, i in enumerate(lrin):
            r = requests.post(url + '&sources=' + ';'.join([str(ii) for ii in i[0]]) + '&destinations=' + ';'.join([str(ii) for ii in i[1]]))
            print(r, int(k * 100 / len(lrin)))
            ind = [res_dis[ii] for ii in i[0]]
            col = [res_dis[ii] for ii in i[1]]
            d_matrix.loc[ind, col] = r.json()['distances']
        def foo_bar(x):
            if x is None or x < 0:
                x = 0
            return x
        d_matrix = d_matrix.map(foo_bar)
        d_matrix = d_matrix.loc[res_dis_all, res_dis_all]
        return d_matrix



Equip_dick={'Подключение': 90,
'Дозаказ': 40,
'Локальная заявка':50,
'Глобальная проблема':100 }

prior={'Подключение': 5,
'Дозаказ': 1,
'Локальная заявка':1,
'Глобальная проблема':1000 }

def fill(x):
   x= list(x)
   if x[0]=='nan':
      return [0,1440]
   times=[]
   for i in list(x):

     Tst= i.split(' ')[1]
     t=int(Tst.split(':')[0])*60+int(Tst.split(':')[1])
     times +=[int(t)]
   return times
   



'''
if mask.sum()==0:
list(db['all'].find({'okrug':{'$eq':name}}))
'''

df= pd.DataFrame(db.all.find({}, {'_id':0}))
df=df.loc[df.okrug=='Югоцентр']
df=df.astype(str)

name= 'Югоцентр'

data =df.to_dict('records')

   
def calcUa(data):

    ENGINEERS=list(db['eng'].find())

    df= pd.DataFrame(data)
    column_mapping = {
    'Заявка': 'request_id',
    'Тип заявки BK': 'request_type_bk',
    'Статус BK': 'status_bk',
    'Тип заявки HD': 'request_type_hd',
    'Начало': 'start_time',
    'Окончание': 'end_time',
    'Район': 'district',
    'Адрес': 'address',
    'Бригада': 'brigade',
    'Гигабитное подключение': 'gigabit_connection',
    'train': 'train',
    'округ': 'okrug'
        }
    df = df.rename(columns=column_mapping)
    df[['lat','lon']]=df[['lat','lon']].astype(float)

    if df.lat.isna().sum() or df.lon.isna().sum():
       df=df.drop(['lat','lon'], axis=1)
       coords = df.apply(lambda x: get_coords(x) , axis=1)
       df.index= range(len(df))
       df = df.join(pd.DataFrame(coords.to_list(), columns=['lat','lon']))

    
    mask = df['request_id'].str.lower().str.contains('офис', na=False)
    df = df.loc[mask.sort_values(ascending=False).index].reset_index(drop=True)
    df_initial_columns= df.columns

    name= 'name'

    d_matrix= get_loc_matrix(df)

    df['duration']=df.request_type_bk.map(Equip_dick)
    df["req_equip"]= df.request_type_bk
    df['priority']=df.request_type_bk.map(prior)



    df['tw'] = df[[ 'start_time', 'end_time']] .apply ( lambda x: fill(x), axis=1)

    features=['tw', 'duration', 'req_equip','priority']
    JOBS= df[features].to_dict('records')


    # Справочник оборудования
    EQUIPMENT = ['Подключение','Дозаказ','Локальная заявка','Глобальная проблема']
    # Заявки: (индекс локации, временное окно [мин], длительность работ [мин], требуемое оборудование)

    BASE_TIME_MATRIX = d_matrix.values.tolist()


    num_locations = len(JOBS)
    num_vehicles = len(ENGINEERS)
    depot = 0

    # Менеджер индексов
    manager = pywrapcp.RoutingIndexManager(num_locations, num_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)

    # --- Ограничение 1: Совместимость "Работа -> Оборудование" ---
    # Запрещаем инженеру посещать заявку, если у него нет нужного оборудования
    for job_idx, job in enumerate(JOBS):
        if job_idx==0 : continue
        node_idx = job_idx  # 0 - это депо, заявки начинаются с 1
        allowed_vehicles = []
        for v_idx, eng in enumerate(ENGINEERS):
            # Проверяем, есть ли у инженера ВСЕ требуемые для заявки инструменты
            if any([eq == job["req_equip"] for eq in eng["equip"]]):
                allowed_vehicles.append(v_idx)
        node_idx_routing = manager.NodeToIndex(node_idx)
        allowed_vehicles = [int(v) for v in allowed_vehicles]  # явный int
        if allowed_vehicles:  # защита от пустого списка
          vehicle_var = routing.VehicleVar(node_idx_routing)
          vehicle_var.SetValues([-1] + allowed_vehicles)  #
          routing.AddDisjunction([node_idx_routing], int(job["priority"] * 100000))



        else:
         routing.AddDisjunction([node_idx_routing], 0)

    # --- Коллбэк времени (Дорога + Длительность работ) ---
    def create_time_callback(vehicle_id):

      def callback(from_index, to_index):

        from_index = int(from_index)
        to_index = int(to_index)

        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)

        speed = ENGINEERS[vehicle_id]["speed"]

        travel_time = int(
            BASE_TIME_MATRIX[from_node][to_node] * 60
            / (1000 * speed)
        )

        service_time = 0

        if from_node != 0:
            service_time = int(JOBS[from_node]["duration"])

        return int(travel_time + service_time)

      return callback

    transit_callbacks = []
    for v in range(num_vehicles):
      callback = create_time_callback(v)

      transit_idx = routing.RegisterTransitCallback(callback)

      transit_callbacks.append(transit_idx)

      routing.SetArcCostEvaluatorOfVehicle(
        transit_idx,
        v
    )

    # --- Ограничение 2: Временные окна и График инженеров ---
    time = "Time"
    # Максимальное время (с запасом), параметр: имя, макс время, учет времени в депо
    routing.AddDimensionWithVehicleTransitAndCapacity(
        transit_callbacks,
        1440, # Максимальное ожидание (slack)
        [1440]*num_vehicles, # Максимальное время на машину (глобальный лимит)
        False, # Не форсировать начало с нуля (мы зададим это вручную)
        time,
    )
    time_dimension = routing.GetDimensionOrDie(time)

    # Применяем временные окна заявок
    for job_idx, job in enumerate(JOBS):
        if job_idx==0 : continue
        index = manager.NodeToIndex(job_idx)
        # CumulVar - это время прибытия/начала в узле
        time_dimension.CumulVar(index).SetRange(job["tw"][0], job["tw"][1])

        time_dimension.SetCumulVarSoftUpperBound(
         index,
         job["tw"][0],
         int(job["priority"] * 100000000)
         )

    # Применяем графики работы инженеров (смены)
    for v_idx, eng in enumerate(ENGINEERS):
        start_index = routing.Start(v_idx)
        end_index = routing.End(v_idx)

        # Инженер выезжает ровно в начале своей смены
        time_dimension.CumulVar(start_index).SetRange(eng["shift"][0], eng["shift"][0])
        # Инженер должен вернуться до конца смены
        time_dimension.CumulVar(end_index).SetRange(0, eng["shift"][1])

    # ==========================================
    # 4. ПАРАМЕТРЫ ПОИСКА И РЕШЕНИЕ
    # ==========================================
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_parameters.time_limit.FromSeconds(5)

    # Решение
    solution = routing.SolveWithParameters(search_parameters)

    # ==========================================
    # 5. ВЫВОД РЕЗУЛЬТАТА
    # ==========================================
    if solution:
        print("=== МАРШРУТЫ ИНЖЕНЕРОВ ===")
        total_time = 0
        plan_output1=[]
        for vehicle_id in range(num_vehicles):
            index = routing.Start(vehicle_id)
            route_str = f"Инженер {vehicle_id + 1} (Смена: {ENGINEERS[vehicle_id]['shift']}, Инстр: {ENGINEERS[vehicle_id]['equip']}):\n"
            route_time = 0
            plan_output = ""

            while not routing.IsEnd(index):
                node_index = manager.IndexToNode(index)
                time_var = time_dimension.CumulVar(index)
                arr_time = solution.Min(time_var)

                if node_index == 0:
                    plan_output += f"  [Депо] Выезд в {arr_time} мин.\n"
                else:
                    job = JOBS[node_index]
                    plan_output += f"  -> Заявка {node_index} (Оборудование: {job['req_equip']})\n"
                    plan_output += f"     Прибытие: {arr_time} мин. | Окно: {job['tw']} | Длительность: {job['duration']} мин.\n"


                plan_output1 +=[[vehicle_id ,node_index, arr_time ]]
                index = solution.Value(routing.NextVar(index))

            # Финальная точка (возврат в депо)
            arr_time = solution.Min(time_dimension.CumulVar(routing.End(vehicle_id)))
            plan_output += f"  [Депо] Возврат в {arr_time} мин.\n"
            plan_output1 +=[[vehicle_id ,0, arr_time ]]
            route_time = arr_time - ENGINEERS[vehicle_id]["shift"][0]
            total_time += route_time

            # Печатаем только если инженер был задействован
            if route_time>0:print(route_str + plan_output + f"  Итого в пути/работе: {route_time} мин.\n")

        print(f"Общее время работы всех инженеров: {total_time} мин.")
    else:
        print("Решение не найдено!")
    df['1']=df.index

    plan_output1 = pd.DataFrame(plan_output1, columns=['veh','1','time_plan']).merge(df, on='1', how='left').astype(str)
    plan_output1.columns = plan_output1.columns.astype(str)
    plan_output1['rank']=plan_output1.groupby('veh').cumcount()
    plan_output1=plan_output1.loc[ plan_output1.groupby('veh')['rank'].transform('max')>1]
    
    plan_output1 =plan_output1[['veh','rank','request_id','req_equip','start_time','end_time','lat', 'lon']]
    
    return plan_output1.to_dict('records')

if __name__ == '__main__':
     print(calcUa(data))
