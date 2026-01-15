from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class YourApiClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        headers = {
            "x-plaato-api-key": self._api_key,
            "Accept": "application/json",
        }

        async with self._session.get(
            url,
            headers=headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            r.raise_for_status()
            return await r.json()

    async def _get_latest_reading(self, device_id: str) -> dict[str, Any] | None:
        """Try a few common patterns to retrieve the latest reading for a device (request 1 item)."""
        candidates: list[tuple[str, dict[str, Any]]] = [
            (f"/devices/{device_id}/readings", {"limit": 1}),
            (f"/devices/{device_id}/readings", {"take": 1}),
            (f"/devices/{device_id}/readings", {"pageSize": 1, "page": 1}),
            (f"/devices/{device_id}/measurements", {"limit": 1}),
        ]

        for path, params in candidates:
            try:
                data = await self._get(path, params=params)
            except Exception:
                continue

            if isinstance(data, list):
                return data[0] if data else None
            if isinstance(data, dict):
                for k in ("items", "readings", "data", "results"):
                    v = data.get(k)
                    if isinstance(v, list) and v:
                        return v[0]
                if any(key in data for key in ("temperature", "temp", "density", "sg", "specificGravity", "gravity")):
                    return data

            return None

        return None

    async def fetch_readings(self) -> dict[str, Any]:
        devices = await self._get("/devices")

        def pick(d: Any, *keys: str):
            if not isinstance(d, dict):
                return None
            for k in keys:
                v = d.get(k)
                if v is not None:
                    return v
            return None

        def as_float(v: Any):
            if v is None:
                return None
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v.strip())
                except Exception:
                    return None
            return None

        def normalize_list_response(data: Any) -> list[dict[str, Any]]:
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
            if isinstance(data, dict):
                for k in ("items", "data", "results", "batches"):
                    v = data.get(k)
                    if isinstance(v, list):
                        return [x for x in v if isinstance(x, dict)]
            return []

        # ----------------------------
        # Fetch fermenters + batches (best effort)
        # ----------------------------
        fermenters: list[dict[str, Any]] = []
        batches: list[dict[str, Any]] = []

        try:
            fermenters = normalize_list_response(await self._get("/fermenters"))
        except Exception as e:
            _LOGGER.debug("Failed to fetch /fermenters: %s", e)

        for params in (None, {"limit": 200}, {"take": 200}, {"pageSize": 200, "page": 1}):
            try:
                raw = await self._get("/batches", params=params) if params else await self._get("/batches")
                batches = normalize_list_response(raw)
                if batches:
                    break
            except Exception as e:
                _LOGGER.debug("Failed to fetch /batches params=%s: %s", params, e)

        # ----------------------------
        # Build device -> fermenter name (FV mapping)
        # ----------------------------
        device_to_fermenter_name: dict[str, str] = {}

        def collect_device_ids(container: Any) -> list[str]:
            ids: list[str] = []
            if container is None:
                return ids

            if isinstance(container, dict):
                for k in ("proId", "deviceId", "id", "_id"):
                    v = container.get(k)
                    if v:
                        ids.append(str(v))
                for k in ("items", "data", "devices"):
                    v = container.get(k)
                    if isinstance(v, list):
                        ids.extend(collect_device_ids(v))
                return ids

            if isinstance(container, list):
                for item in container:
                    if isinstance(item, (str, int)):
                        ids.append(str(item))
                    elif isinstance(item, dict):
                        v = item.get("id") or item.get("deviceId") or item.get("proId") or item.get("_id")
                        if v:
                            ids.append(str(v))
                return ids

            if isinstance(container, (str, int)):
                ids.append(str(container))
            return ids

        for fer in fermenters or []:
            fer_name = fer.get("name") or fer.get("title") or fer.get("id") or "Fermenter"
            possible = [
                fer.get("devices"),
                fer.get("device"),
                {"proId": fer.get("proId")} if fer.get("proId") else None,
            ]
            for c in possible:
                for did in collect_device_ids(c):
                    device_to_fermenter_name[str(did)] = str(fer_name)

        # ----------------------------
        # Batch matching
        # ----------------------------
        def batch_label(batch: dict[str, Any]) -> str | None:
            return batch.get("name") or batch.get("batchCode") or batch.get("id") or batch.get("_id")

        def batch_rank(batch: dict[str, Any]) -> tuple[int, int, int, str]:
            """
            Higher is better:
            - enabled True (or missing)
            - not archived
            - not ended (end is None)
            - most recent updatedAt/start/createdAt
            """
            enabled = 1 if batch.get("enabled") is not False else 0
            not_archived = 1 if batch.get("archived") is not True else 0
            not_ended = 1 if batch.get("end") is None else 0
            recency = str(batch.get("updatedAt") or batch.get("start") or batch.get("createdAt") or "")
            return (enabled, not_archived, not_ended, recency)

        def extract_device_ids_from_batch_devices(devices_field: Any) -> list[str]:
            """
            Plaato may return batch.devices as:
              - list[str] (device ids)
              - list[dict] (expanded device objects)
            """
            ids: list[str] = []
            if not isinstance(devices_field, list):
                return ids

            for item in devices_field:
                if isinstance(item, (str, int)):
                    ids.append(str(item))
                elif isinstance(item, dict):
                    v = item.get("id") or item.get("_id") or item.get("deviceId") or item.get("proId")
                    if v:
                        ids.append(str(v))
            return ids

        device_id_to_best_batch: dict[str, dict[str, Any]] = {}

        for batch in batches or []:
            dev_ids = extract_device_ids_from_batch_devices(batch.get("devices"))
            if not dev_ids:
                continue
            for did in dev_ids:
                existing = device_id_to_best_batch.get(did)
                if not existing or batch_rank(batch) > batch_rank(existing):
                    device_id_to_best_batch[did] = batch

        # ----------------------------
        # Build output
        # ----------------------------
        out: dict[str, Any] = {}

        for dev in devices or []:
            dev_id = dev.get("id")
            if not dev_id:
                continue
            dev_id = str(dev_id)

            latest = dev.get("latestReading") or {}
            latest_nested = pick(latest, "reading", "data", "values")
            if isinstance(latest_nested, dict):
                latest = latest_nested

            temp = as_float(pick(latest, "temperature", "temp", "tempC", "temperatureC", "beerTemp", "wortTemp"))
            sg = as_float(pick(latest, "density", "sg", "specificGravity", "gravity", "SG"))
            time = pick(latest, "time", "timestamp", "createdAt", "date") or pick(dev, "latestReadingTime")

            # Handle nested objects:
            # temperature: {"celsius": ..}, density: {"specificGravity": ..}
            if temp is None and isinstance(latest.get("temperature"), dict):
                temp = as_float(pick(latest["temperature"], "celsius", "fahrenheit"))
            if sg is None and isinstance(latest.get("density"), dict):
                sg = as_float(pick(latest["density"], "specificGravity", "plato"))

            if temp is None or sg is None:
                reading = await self._get_latest_reading(dev_id)
                if isinstance(reading, dict):
                    reading2 = pick(reading, "reading", "data", "values")
                    if isinstance(reading2, dict):
                        reading = reading2

                    if temp is None:
                        v = pick(reading, "temperature", "temp", "tempC", "temperatureC", "beerTemp", "wortTemp")
                        temp = as_float(pick(v, "celsius", "fahrenheit")) if isinstance(v, dict) else as_float(v)

                    if sg is None:
                        v = pick(reading, "density", "sg", "specificGravity", "gravity", "SG")
                        sg = as_float(pick(v, "specificGravity", "plato")) if isinstance(v, dict) else as_float(v)

                    time = time or pick(reading, "time", "timestamp", "createdAt", "date")

            fermenter_name = device_to_fermenter_name.get(dev_id)

            batch_name = None
            batch_obj = device_id_to_best_batch.get(dev_id)
            if isinstance(batch_obj, dict):
                batch_name = batch_label(batch_obj)

            out[dev_id] = {
                "name": dev.get("name") or f"Plaato {dev_id}",
                "batteryLevel": pick(dev, "batteryLevel", "battery", "battery_percent"),
                "wifiStrength": pick(dev, "wifiStrength", "rssi", "signal"),
                "temperature": temp,
                "density": sg,
                "time": time,
                "fermenter": fermenter_name,
                "batch": batch_name,
            }

        return out
