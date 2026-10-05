# Контракт программного интерфейса

Версия 1.0. JSON передаётся в UTF-8. Авторизация — подписанная cookie `artem_yankovoy_session`, HttpOnly, SameSite=Lax, срок 12 часов. Все операции, кроме входа, требуют авторизации. Ответы и статические страницы имеют `Cache-Control: no-store`. Время передаётся в местном времени Москвы **без смещения UTC**, в формате `YYYY-MM-DDTHH:MM:SS`. Периоды и интервалы полуоткрытые: `[начало, конец)`; соседние приёмы допустимы.

| Метод и путь | Параметры | Успешный ответ | Ошибки |
|---|---|---|---|
| POST /api/login | JSON username, password | 200 `{user:{id,username,full_name}}`, cookie | 401 неверные данные, 422 параметры |
| POST /api/logout | Cookie | 200 `{ok:true}`, удаление cookie | 401 |
| GET /api/me | Cookie | 200 `{user:{id,username,full_name}}` | 401 |
| GET /api/services | Cookie | 200 `{items:[Service]}` | 401 |
| GET /api/specialists | Cookie | 200 `{items:[Specialist]}` | 401 |
| GET /api/slots | service_id≥1 обязательно; page≥1 (1), size 1…100 (20), specialist_id≥1 необязательно, from_at/to_at | 200 `{items:[Slot],total,page,size}`; только свободные совместимые слоты | 401, 404 услуга/специалист, 422 параметры/период |
| GET /api/appointments | page≥1 (1), size 1…100 (20), status из booked/cancelled/completed, specialist_id≥1 необязательно | 200 `{items:[Appointment],total,page,size}` | 401, 404 специалист, 422 параметры |
| GET /api/appointments/{id} | id≥1 | 200 `Appointment` | 401, 404 запись, 422 id |
| POST /api/appointments | JSON slot_id≥1, service_id≥1, client_name 1…160 непустых символов | 201 `Appointment` | 401, 404 слот/услуга, 409 пересечение, 422 параметры/несовместимая услуга/длительность |
| POST /api/appointments/{id}/cancel | id≥1 | 200 `Appointment`, status=cancelled; повторная отмена идемпотентна | 401, 403 чужая запись, 404 запись, 409 завершённый приём, 422 id |
| GET /api/summary | from_at/to_at, по умолчанию 2026-10-01…2027-10-01 | 200 `{period,total_appointments,cancelled_appointments,cancelled_share,specialists:[SpecialistSummary]}` | 401, 422 период |

## Форматы объектов

`Service`: id, name, duration_minutes (положительное целое), price (число рублей).

`Specialist`: id, full_name, service_ids (непустой массив идентификаторов поддерживаемых услуг).

`Slot`: id, specialist_id, start_at, end_at, service_ids, specialist:{id,full_name}. В списке слот начинается в from_at или позднее и целиком заканчивается не позднее to_at. В слоте услуга начинается в start_at; остаток слота не дробится на новые слоты.

`Appointment`: id, slot_id, client_name, status, start_at, end_at, created_at, cancelled_at (null для неотменённых), specialist:{id,full_name}, service:{id,name,duration_minutes,price}, user:{id,username,full_name}. completed — исторический завершённый приём, учитываемый в загрузке; переход в этот статус не является отдельной операцией ДЗ 1. Список доступен авторизованным пользователям; отменять можно только свои записи.

`SpecialistSummary`: id, full_name, slot_minutes, booked_minutes, load_percent, total_appointments, cancelled_appointments. Минуты считаются только внутри заданного периода, включая обрезание приёмов и слотов на границах. load_percent = 100 × booked_minutes / slot_minutes, при нулевой доступности результат 0. Загрузка учитывает booked и completed. total_appointments и cancelled_appointments считают записи, **начавшиеся** внутри периода; cancelled_share = cancelled_appointments / total_appointments, при пустом периоде 0. Доля отмен лежит в диапазоне 0…1.

## Ошибки и наблюдаемость

Ошибка предметного правила: `{detail:"текст причины"}`. Ошибка валидации FastAPI: `{detail:[{type,loc,msg,input,...}]}`. Общие коды: 401 — нет действующей сессии, 403 — нет права отмены, 404 — объект отсутствует, 409 — конфликт, 422 — недопустимые параметры. Ответы на валидные операции измеряются без ошибок.

`X-App-Time-Ms` — время серверной обработки до передачи ответа; `X-DB-Time-Ms` — сумма интервалов execute+fetch и commit драйвера PostgreSQL; `X-DB-Queries` — число бизнес-запросов. Установка соединения, установка search_path через параметры соединения, commit и служебные запросы драйвера не входят в число бизнес-запросов; время явного commit операций записи входит во время доступа к БД. Разность двух времён включает создание соединения, проверку правил и сериализацию. Это измерение **доступа к базе**, включая передачу результатов драйверу, а не чистое процессорное время PostgreSQL. Создание и отмена фиксируют транзакцию до отправки успешного ответа.
