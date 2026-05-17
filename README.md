# Bus Live Wialon

Лёгкая страница для отображения движения автобуса по маршруту на данных Wialon.

## Возможности

- FastAPI backend.
- Leaflet + OpenStreetMap frontend.
- Данные автобуса из Wialon.
- Остановки из `route_config.json`.
- Активное окно маршрута: `07:00–08:30`.
- Прогноз прибытия по скорости `eta_speed_kmh = 60`.
- Маршрут стартует строго с первой остановки.
- После первой остановки допускается пропуск промежуточных остановок: если автобус попал в более позднюю геозону, промежуточные отмечаются как посещённые.
- Без базы данных и без истории.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env

python -m uvicorn app:app --host 127.0.0.1 --port 8015 --reload
```

Открыть:

```text
http://127.0.0.1:8015/
```

## Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt

copy .env.example .env
notepad .env

python -m uvicorn app:app --host 127.0.0.1 --port 8015 --reload
```

## API

```text
GET  /api/health
GET  /api/bus-status
GET  /api/config
POST /api/reset-progress
```

Сброс прогресса маршрута:

```bash
curl -X POST http://127.0.0.1:8015/api/reset-progress
```

## Деплой на сервер

На сервере:

```bash
cd /opt
sudo git clone https://github.com/<owner>/<repo>.git bus-live-wialon
cd /opt/bus-live-wialon

sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt

sudo cp .env.example .env
sudo nano .env
```

В `.env` указать реальный `WIALON_TOKEN`.

Systemd:

```bash
sudo cp deploy/bus-live-wialon.service /etc/systemd/system/bus-live-wialon.service
sudo systemctl daemon-reload
sudo systemctl enable bus-live-wialon
sudo systemctl restart bus-live-wialon
sudo systemctl status bus-live-wialon --no-pager
```

Проверка:

```bash
curl http://127.0.0.1:8015/api/health
```

## Nginx

Скопировать конфиг:

```bash
sudo cp deploy/nginx-bus-live-wialon.conf /etc/nginx/sites-available/bus-live-wialon
sudo nano /etc/nginx/sites-available/bus-live-wialon
sudo ln -s /etc/nginx/sites-available/bus-live-wialon /etc/nginx/sites-enabled/bus-live-wialon
sudo nginx -t
sudo systemctl reload nginx
```

В конфиге заменить:

```text
server_name bus.example.com;
```

на реальный домен или IP.

## Важно по безопасности

- `.env` не коммитить.
- Реальный `WIALON_TOKEN` хранить только на сервере.
- `discovery_result*.json` не коммитить: там могут быть внутренние координаты и список объектов.
