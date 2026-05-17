import os
import json
import sys
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


load_dotenv()


WIALON_HOST = os.getenv("WIALON_HOST", "https://hst-api.wialon.com").rstrip("/")
WIALON_TOKEN = os.getenv("WIALON_TOKEN", "").strip()

TRACKED_BUS_ID = os.getenv("TRACKED_BUS_ID", "").strip()
TRACKED_BUS_NAME = os.getenv("TRACKED_BUS_NAME", "").strip()

OUTPUT_FILE = "discovery_result.json"


class WialonError(Exception):
    pass


class WialonClient:
    def __init__(self, host: str, token: str):
        self.host = host.rstrip("/")
        self.token = token
        self.sid: Optional[str] = None

    def call(self, svc: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if params is None:
            params = {}

        url = f"{self.host}/wialon/ajax.html"

        request_params = {
            "svc": svc,
            "params": json.dumps(params, ensure_ascii=False),
        }

        if self.sid:
            request_params["sid"] = self.sid

        response = requests.get(url, params=request_params, timeout=30)

        try:
            data = response.json()
        except Exception:
            raise WialonError(
                f"Wialon returned non-JSON response. HTTP {response.status_code}: {response.text[:500]}"
            )

        if isinstance(data, dict) and "error" in data:
            raise WialonError(f"Wialon API error in {svc}: {data}")

        return data

    def login(self) -> Dict[str, Any]:
        data = self.call("token/login", {"token": self.token})

        sid = data.get("eid")
        if not sid:
            raise WialonError(f"Login failed, no eid in response: {data}")

        self.sid = sid
        return data

    def logout(self) -> None:
        if self.sid:
            try:
                self.call("core/logout", {})
            except Exception:
                pass
            finally:
                self.sid = None

    def search_items(
        self,
        items_type: str,
        prop_name: str = "sys_name",
        prop_value_mask: str = "*",
        flags: int = 1,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        params = {
            "spec": {
                "itemsType": items_type,
                "propName": prop_name,
                "propValueMask": prop_value_mask,
                "sortType": prop_name,
            },
            "force": 1,
            "flags": flags,
            "from": 0,
            "to": limit,
        }

        data = self.call("core/search_items", params)
        return data.get("items", [])

    def get_resources(self) -> List[Dict[str, Any]]:
        # flags=1 достаточно для id/name базовой информации ресурса
        return self.search_items("avl_resource", flags=1)

    def get_units(self) -> List[Dict[str, Any]]:
        # 1 - базовые свойства
        # 1024 - последняя позиция, если доступна
        flags = 1 | 1024
        return self.search_items("avl_unit", flags=flags)

    def get_zones_for_resource(self, resource_id: int) -> List[Dict[str, Any]]:
        """
        В разных конфигурациях Wialon resource/get_zone_data может вести себя чуть по-разному.
        Поэтому используем несколько вариантов вызова.
        """
        attempts = [
            {"itemId": resource_id},
            {"itemId": resource_id, "col": []},
            {"itemId": resource_id, "col": [0]},
        ]

        last_error = None

        for params in attempts:
            try:
                data = self.call("resource/get_zone_data", params)

                if isinstance(data, list):
                    return data

                if isinstance(data, dict):
                    # Иногда зоны могут вернуться словарём id -> zone
                    if "zones" in data and isinstance(data["zones"], list):
                        return data["zones"]

                    if all(isinstance(v, dict) for v in data.values()):
                        return list(data.values())

                    return []

            except Exception as e:
                last_error = e

        print(f"⚠️ Не удалось получить геозоны ресурса {resource_id}: {last_error}")
        return []


def normalize_unit(unit: Dict[str, Any]) -> Dict[str, Any]:
    pos = unit.get("pos") or {}

    return {
        "id": unit.get("id"),
        "name": unit.get("nm"),
        "last_position": {
            "lat": pos.get("y"),
            "lon": pos.get("x"),
            "speed": pos.get("s"),
            "course": pos.get("c"),
            "time": pos.get("t"),
        } if pos else None,
    }


def normalize_resource(resource: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": resource.get("id"),
        "name": resource.get("nm"),
    }


def normalize_zone(zone: Dict[str, Any], resource_id: int, resource_name: str) -> Dict[str, Any]:
    return {
        "id": zone.get("id"),
        "name": zone.get("n") or zone.get("name") or zone.get("nm"),
        "resource_id": resource_id,
        "resource_name": resource_name,
        "type": zone.get("t"),
        "description": zone.get("d"),
        "area": zone.get("a"),
        "perimeter": zone.get("p"),
    }


def find_tracked_bus(units: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if TRACKED_BUS_ID:
        try:
            target_id = int(TRACKED_BUS_ID)
        except ValueError:
            print(f"⚠️ TRACKED_BUS_ID должен быть числом, сейчас: {TRACKED_BUS_ID}")
            target_id = None

        if target_id is not None:
            for unit in units:
                if unit.get("id") == target_id:
                    return unit

    if TRACKED_BUS_NAME:
        needle = TRACKED_BUS_NAME.lower()
        matches = [
            unit for unit in units
            if needle in str(unit.get("name", "")).lower()
        ]

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 1:
            print("⚠️ По TRACKED_BUS_NAME найдено несколько объектов:")
            for unit in matches:
                print(f"   - {unit.get('id')} | {unit.get('name')}")
            print("   Укажи точный TRACKED_BUS_ID в .env, чтобы не было корпоративной лотереи.")
            return None

    return None


def print_section(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def main() -> int:
    if not WIALON_TOKEN:
        print("❌ Не найден WIALON_TOKEN в .env")
        return 1

    client = WialonClient(WIALON_HOST, WIALON_TOKEN)

    try:
        print("Авторизация в Wialon...")
        user_data = client.login()
        print(f"✅ Авторизация OK. User: {user_data.get('user', {}).get('nm', 'unknown')}")

        print("Получаем ресурсы...")
        raw_resources = client.get_resources()
        resources = [normalize_resource(r) for r in raw_resources]

        print("Получаем объекты/автобусы...")
        raw_units = client.get_units()
        units = [normalize_unit(u) for u in raw_units]

        print("Получаем геозоны по ресурсам...")
        zones: List[Dict[str, Any]] = []

        for resource in resources:
            resource_id = resource["id"]
            resource_name = resource["name"]

            if resource_id is None:
                continue

            resource_zones = client.get_zones_for_resource(int(resource_id))

            for zone in resource_zones:
                zones.append(
                    normalize_zone(
                        zone=zone,
                        resource_id=int(resource_id),
                        resource_name=str(resource_name),
                    )
                )

        tracked_bus = find_tracked_bus(units)

        result = {
            "wialon_host": WIALON_HOST,
            "resources_count": len(resources),
            "zones_count": len(zones),
            "units_count": len(units),
            "tracked_bus": tracked_bus,
            "resources": resources,
            "zones": zones,
            "units": units,
        }

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print_section("РЕСУРСЫ")
        if resources:
            for r in resources:
                print(f"{r['id']} | {r['name']}")
        else:
            print("Ресурсы не найдены.")

        print_section("ГЕОЗОНЫ")
        if zones:
            for z in zones:
                print(
                    f"{z['id']} | {z['name']} | resource_id={z['resource_id']} | {z['resource_name']}"
                )
        else:
            print("Геозоны не найдены или нет прав на чтение геозон.")

        print_section("ОБЪЕКТЫ / АВТОБУСЫ")
        if units:
            for u in units:
                pos = u.get("last_position")
                if pos:
                    print(
                        f"{u['id']} | {u['name']} | "
                        f"lat={pos.get('lat')} lon={pos.get('lon')} speed={pos.get('speed')}"
                    )
                else:
                    print(f"{u['id']} | {u['name']} | нет последней позиции")
        else:
            print("Объекты не найдены.")

        print_section("ОТСЛЕЖИВАЕМЫЙ АВТОБУС")
        if TRACKED_BUS_ID or TRACKED_BUS_NAME:
            if tracked_bus:
                print(f"✅ Найден: {tracked_bus['id']} | {tracked_bus['name']}")
                print(json.dumps(tracked_bus, ensure_ascii=False, indent=2))
            else:
                print("❌ Не найден. Проверь TRACKED_BUS_ID или TRACKED_BUS_NAME в .env")
        else:
            print("Не задан. Это нормально. Добавь TRACKED_BUS_ID или TRACKED_BUS_NAME в .env позже.")

        print()
        print(f"✅ Результат сохранён в файл: {OUTPUT_FILE}")

        return 0

    except WialonError as e:
        print(f"❌ Ошибка Wialon: {e}")
        return 1

    except requests.RequestException as e:
        print(f"❌ Ошибка сети: {e}")
        return 1

    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")
        return 1

    finally:
        client.logout()


if __name__ == "__main__":
    raise SystemExit(main())