"""Сенсоры для мониторинга интеграции Народного мониторинга."""

import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, CONF_MAC, CONF_NAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Настройка сенсоров для каждой записи."""
    _LOGGER.debug("Загрузка сенсоров для записи %s", config_entry.entry_id)
    entry_id = config_entry.entry_id
    mac = config_entry.data.get(CONF_MAC, "unknown")
    device_name = config_entry.data.get(CONF_NAME, f"Народный мониторинг {mac}")

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, entry_id)},
        name=device_name,
        manufacturer="Narodmon",
        model="Sender",
        sw_version="0.1.0",
    )

    sensors = [
        NarodmonTaskStatusSensor(hass, config_entry),
        NarodmonLastSendSensor(hass, config_entry),
    ]
    async_add_entities(sensors, update_before_add=True)


class NarodmonTaskStatusSensor(BinarySensorEntity):
    """Бинарный сенсор статуса фоновой задачи."""

    def __init__(self, hass, config_entry):
        self.hass = hass
        self.config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_task_status"
        self._attr_name = f"Статус задачи {config_entry.data.get(CONF_NAME, '')}".strip()
        self._attr_is_on = False
        self._attr_available = True
        self._attr_icon = "mdi:play-circle"
        # Привязка к устройству через идентификаторы
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry.entry_id)}
        }

    @property
    def is_on(self):
        return self._attr_is_on

    async def async_update(self):
        entry_data = self.hass.data[DOMAIN].get(self.config_entry.entry_id, {})
        task = entry_data.get("task")
        if task and not task.done():
            self._attr_is_on = True
            self._attr_icon = "mdi:play-circle"
        else:
            self._attr_is_on = False
            self._attr_icon = "mdi:stop-circle"


class NarodmonLastSendSensor(SensorEntity):
    """Сенсор с информацией о последней отправке."""

    def __init__(self, hass, config_entry):
        self.hass = hass
        self.config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_last_send"
        self._attr_name = f"Последняя отправка {config_entry.data.get(CONF_NAME, '')}".strip()
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "last_packet": None,
            "last_response": None,
            "last_error": None,
        }
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock"
        # Привязка к устройству через идентификаторы
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry.entry_id)}
        }

    @property
    def native_value(self):
        return self._attr_native_value

    @property
    def extra_state_attributes(self):
        return self._attr_extra_state_attributes

    async def async_update(self):
        entry_data = self.hass.data[DOMAIN].get(self.config_entry.entry_id, {})
        last_send_info = entry_data.get("last_send", {})
        if last_send_info:
            timestamp = last_send_info.get("timestamp")
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    self._attr_native_value = dt
                except ValueError:
                    self._attr_native_value = None
            else:
                self._attr_native_value = None

            self._attr_extra_state_attributes = {
                "last_packet": last_send_info.get("packet"),
                "last_response": last_send_info.get("response"),
                "last_error": last_send_info.get("error"),
            }
        else:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "last_packet": None,
                "last_response": None,
                "last_error": None,
            }