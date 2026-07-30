"""Config Flow для интеграции с Народным мониторингом."""

from typing import Any, Dict, Optional, List
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import selector
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CONF_MAC,
    CONF_NAME,
    CONF_SENSOR_MAP,
    CONF_TIMEOUT,
    DEFAULT_NAME,
    DEFAULT_TIMEOUT,
)

# Шаг 1: Ввод основных данных
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MAC, description="MAC-адрес устройства (12-18 символов A-Z и 0-9 иногда разделенных '-' или ':')"): str,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.All(vol.Coerce(int), vol.Range(min=0, max=120)),
    }
)


class NarodmonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config Flow для Народного мониторинга."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._all_sensor_entities: List[str] = []
        self._selected_entities: List[str] = []

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Шаг 1: основные параметры."""
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_MAC].upper())
            self._abort_if_unique_id_configured()

            self._config = user_input
            self._config[CONF_MAC] = self._config[CONF_MAC].upper()
            return await self.async_step_select_sensors()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_select_sensors(self, user_input: Optional[Dict[str, Any]] = None):
        """Шаг 2a: выбор сенсоров через EntitySelector."""
        if user_input is not None:
            self._selected_entities = user_input.get("sensors", [])
            if not self._selected_entities:
                return self.async_show_form(
                    step_id="select_sensors",
                    data_schema=vol.Schema({
                        vol.Required("sensors", default=[]): selector.EntitySelector(
                            selector.EntitySelectorConfig(
                                domain="sensor",
                                multiple=True,
                            )
                        )
                    }),
                    errors={"base": "no_sensors_selected"},
                )
            return await self.async_step_assign_ids()

        return self.async_show_form(
            step_id="select_sensors",
            data_schema=vol.Schema({
                vol.Required("sensors", default=[]): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor",
                        multiple=True,
                    )
                )
            }),
            description_placeholders={
                "hint": "Начните вводить название сенсора для поиска."
            }
        )

    async def async_step_assign_ids(self, user_input: Optional[Dict[str, Any]] = None):
        """Шаг 2b: присвоение ID метрик для выбранных сенсоров."""
        if user_input is not None:
            sensor_map = {
                entity_id: user_input.get(entity_id, "").strip()
                for entity_id in self._selected_entities
            }
            sensor_map = {k: v for k, v in sensor_map.items() if v}

            if not sensor_map:
                return self.async_show_form(
                    step_id="assign_ids",
                    data_schema=self._build_id_schema(),
                    errors={"base": "no_ids_specified"},
                    description_placeholders={"error": "Укажите ID хотя бы для одного сенсора."}
                )

            self._config[CONF_SENSOR_MAP] = sensor_map
            return self.async_create_entry(
                title=f"Народный мониторинг ({self._config[CONF_MAC]})",
                data=self._config,
            )

        return self.async_show_form(
            step_id="assign_ids",
            data_schema=self._build_id_schema(),
            description_placeholders={
                "sensor_count": str(len(self._selected_entities)),
                "hint": "Введите ID метрики (например T1, H1, P1) для каждого выбранного сенсора."
            }
        )

    def _build_id_schema(self) -> vol.Schema:
        schema = {}
        for entity_id in self._selected_entities:
            default_id = self._guess_sensor_id(entity_id)
            schema[vol.Required(entity_id, default=default_id)] = str
        return vol.Schema(schema)

    async def _get_sensor_entities(self) -> List[str]:
        entities = []
        for state in self.hass.states.async_all():
            entity_id = state.entity_id
            if not entity_id.startswith("sensor."):
                continue
            try:
                float(state.state)
                entities.append(entity_id)
            except (ValueError, TypeError):
                continue
        return sorted(entities)

    def _guess_sensor_id(self, entity_id: str) -> str:
        name = entity_id.lower()
        if "temp" in name or "temperature" in name:
            return "T1"
        if "hum" in name or "humidity" in name:
            return "H1"
        if "press" in name or "pressure" in name:
            return "P1"
        if "gas" in name:
            return "G1"
        if "water" in name or "flow" in name:
            return "V1"
        if "power" in name or "electric" in name:
            return "E1"
        if "light" in name or "illumin" in name:
            return "L1"
        if "wind" in name:
            return "W1"
        if "rain" in name:
            return "R1"
        return ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NarodmonOptionsFlow(config_entry)


class NarodmonOptionsFlow(config_entries.OptionsFlow):
    """Редактирование опций (трёхшаговый подход: основные параметры → выбор сенсоров → ID метрик)."""

    def __init__(self, config_entry):
        self._config_entry = config_entry
        self._sensor_map = config_entry.options.get(CONF_SENSOR_MAP) or config_entry.data.get(CONF_SENSOR_MAP, {})
        self._basic_config = {}   # временное хранение mac, name, timeout
        self._selected_entities = list(self._sensor_map.keys())

    # ----- Шаг 1: основные настройки (MAC, имя, интервал) -----
    async def async_step_init(self, user_input=None):
        """Редактирование основных параметров."""
        if user_input is not None:
            # Сохраняем основные параметры
            self._basic_config = {
                CONF_MAC: user_input[CONF_MAC].upper(),
                CONF_NAME: user_input.get(CONF_NAME, ""),
                CONF_TIMEOUT: user_input[CONF_TIMEOUT],
            }
            # Переходим к выбору сенсоров
            return await self.async_step_select_sensors()

        # Предзаполняем текущими значениями
        current_mac = self._config_entry.data.get(CONF_MAC, "")
        current_name = self._config_entry.data.get(CONF_NAME, DEFAULT_NAME)
        current_timeout = self._config_entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)

        schema = vol.Schema({
            vol.Required(CONF_MAC, default=current_mac): str,
            vol.Optional(CONF_NAME, default=current_name): str,
            vol.Optional(CONF_TIMEOUT, default=current_timeout): vol.All(vol.Coerce(int), vol.Range(min=0, max=120)),
        })

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "hint": "Измените основные параметры устройства."
            }
        )

    # ----- Шаг 2: выбор сенсоров -----
    async def async_step_select_sensors(self, user_input=None):
        """Выбор сенсоров (EntitySelector)."""
        if user_input is not None:
            self._selected_entities = user_input.get("sensors", [])
            if not self._selected_entities:
                return self.async_show_form(
                    step_id="select_sensors",
                    data_schema=vol.Schema({
                        vol.Required("sensors", default=[]): selector.EntitySelector(
                            selector.EntitySelectorConfig(
                                domain="sensor",
                                multiple=True,
                            )
                        )
                    }),
                    errors={"base": "no_sensors_selected"},
                )
            return await self.async_step_assign_ids()

        # Предзаполняем текущим списком
        return self.async_show_form(
            step_id="select_sensors",
            data_schema=vol.Schema({
                vol.Required("sensors", default=self._selected_entities): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor",
                        multiple=True,
                    )
                )
            }),
            description_placeholders={
                "hint": "Выберите сенсоры, которые будут отправляться."
            }
        )

    # ----- Шаг 3: назначение ID метрик -----
    async def async_step_assign_ids(self, user_input=None):
        """Присвоение ID для каждого выбранного сенсора."""
        if user_input is not None:
            new_map = {
                entity_id: user_input.get(entity_id, "").strip()
                for entity_id in self._selected_entities
            }
            new_map = {k: v for k, v in new_map.items() if v}
            if not new_map:
                return self.async_show_form(
                    step_id="assign_ids",
                    data_schema=self._build_id_schema(),
                    errors={"base": "no_ids_specified"},
                )

            # Обновляем основные данные (mac, name, timeout)
            current_data = dict(self._config_entry.data)
            current_data.update({
                CONF_MAC: self._basic_config[CONF_MAC],
                CONF_NAME: self._basic_config[CONF_NAME],
                CONF_TIMEOUT: self._basic_config[CONF_TIMEOUT],
            })
            # Обновляем запись
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data=current_data,
                options={CONF_SENSOR_MAP: new_map},  # обновляем опции
            )

            # Завершаем options flow
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="assign_ids",
            data_schema=self._build_id_schema(),
            description_placeholders={
                "hint": "Измените ID метрики для каждого сенсора."
            }
        )

    def _build_id_schema(self) -> vol.Schema:
        schema = {}
        for entity_id in self._selected_entities:
            current_id = self._sensor_map.get(entity_id, self._guess_sensor_id(entity_id))
            schema[vol.Required(entity_id, default=current_id)] = str
        return vol.Schema(schema)

    async def _get_sensor_entities(self) -> List[str]:
        entities = []
        for state in self.hass.states.async_all():
            entity_id = state.entity_id
            if not entity_id.startswith("sensor."):
                continue
            try:
                float(state.state)
                entities.append(entity_id)
            except (ValueError, TypeError):
                continue
        return sorted(entities)

    def _guess_sensor_id(self, entity_id: str) -> str:
        name = entity_id.lower()
        if "temp" in name or "temperature" in name:
            return "T1"
        if "hum" in name or "humidity" in name:
            return "H1"
        if "press" in name or "pressure" in name:
            return "P1"
        if "gas" in name:
            return "G1"
        if "water" in name or "flow" in name:
            return "V1"
        if "power" in name or "electric" in name:
            return "E1"
        if "light" in name or "illumin" in name:
            return "L1"
        if "wind" in name:
            return "W1"
        if "rain" in name:
            return "R1"
        return ""