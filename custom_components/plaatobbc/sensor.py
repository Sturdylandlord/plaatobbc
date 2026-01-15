from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


@dataclass(frozen=True)
class ReadingDef:
    key: str
    name: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None


READINGS: tuple[ReadingDef, ...] = (
    ReadingDef(
        key="temperature",
        name="Temperature",
        unit="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ReadingDef(
        key="density",
        name="Specific Gravity",
        unit="SG",
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ReadingDef(
        key="gravity_points",
        name="Gravity Points",
        unit="pts",
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ReadingDef(
        key="batteryLevel",
        name="Battery",
        unit="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ReadingDef(
        key="wifiStrength",
        name="WiFi Strength",
        unit=None,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ReadingDef(
        key="time",
        name="Last Reading",
        unit=None,
        device_class=SensorDeviceClass.TIMESTAMP,
        state_class=None,
    ),
    ReadingDef(
        key="fermenter",
        name="Fermenter",
        unit=None,
        device_class=None,
        state_class=None,
    ),
    ReadingDef(
        key="batch",
        name="Current Batch",
        unit=None,
        device_class=None,
        state_class=None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    data = coordinator.data or {}
    entities: list[SensorEntity] = []

    for device_id, device_payload in data.items():
        device_name = (device_payload or {}).get("name") or f"Plaato {device_id}"
        for reading in READINGS:
            entities.append(
                PlaatoReadingSensor(
                    coordinator=coordinator,
                    entry=entry,
                    device_id=str(device_id),
                    device_name=device_name,
                    reading=reading,
                )
            )

    async_add_entities(entities)


class PlaatoReadingSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        device_id: str,
        device_name: str,
        reading: ReadingDef,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._device_name = device_name
        self._reading = reading

        self._attr_name = reading.name
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_{reading.key}"

        self._attr_native_unit_of_measurement = reading.unit
        self._attr_device_class = reading.device_class
        self._attr_state_class = reading.state_class

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device_name,
            "manufacturer": "Plaato",
        }

    @property
    def native_value(self) -> Any:
        device_payload = (self.coordinator.data or {}).get(self._device_id) or {}

        # Derived: gravity points (e.g. 1.0487 -> 49)
        if self._reading.key == "gravity_points":
            sg = device_payload.get("density")
            if isinstance(sg, str):
                try:
                    sg = float(sg.strip())
                except Exception:
                    return None
            if isinstance(sg, (int, float)):
                return round((float(sg) - 1.0) * 1000)
            return None

        val = device_payload.get(self._reading.key)

        # SG to 4 decimal places
        if self._reading.key == "density":
            if isinstance(val, str):
                try:
                    val = float(val.strip())
                except Exception:
                    return None
            if isinstance(val, (int, float)):
                return round(float(val), 4)
            return None

        # Timestamp conversion
        if self._reading.device_class == SensorDeviceClass.TIMESTAMP:
            if isinstance(val, datetime) or val is None:
                return val
            if isinstance(val, str):
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except Exception:
                    return val

        return val

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device_payload = (self.coordinator.data or {}).get(self._device_id) or {}
        return {
            "plaato_device_id": self._device_id,
            "source": DOMAIN,
            "raw_device_payload": device_payload,
            "last_update_success": getattr(self.coordinator, "last_update_success", None),
            "last_update": getattr(self.coordinator, "last_update_success_time", None),
        }
