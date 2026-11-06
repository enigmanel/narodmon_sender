"""Интеграция для отправки данных сенсоров в сервис Народный мониторинг."""

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType
from homeassistant import config_entries

from homeassistant.helpers import config_validation as cv



from .const import (
    DOMAIN,
    CONF_MAC,
    CONF_NAME,
    CONF_SENSOR_MAP,
    CONF_OPTIMISTIC_SENSORS,
    CONF_TIMEOUT,
    NARODMON_HOST,
    NARODMON_PORT,
    DEFAULT_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

TCP_TIMEOUT = 20
TCP_CLOSE_TIMEOUT = 5

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Настройка интеграции (совместимость с configuration.yaml)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Настройка интеграции через config_flow."""
    hass.data[DOMAIN][entry.entry_id] = {
        "entry_id": entry.entry_id,
        "sensors_loaded": False,
    }

    # Регистрируем сервис (один раз)
    if not hass.services.has_service(DOMAIN, "send_data"):
        async def send_to_narodmon(call: ServiceCall) -> None:
            #for data in hass.data[DOMAIN].values():
            #    entry_id = data["entry_id"]
            #    await _send_single_config(hass, entry_id)
            device_id = call.data.get("device_id")
            if not device_id:
                _LOGGER.error("Не указан device_id")
                return
            else:
                _LOGGER.debug("найден device_id %s",device_id)    

            # Ищем entry_id по device_id
            entry_id = None
            for entry_data in hass.data[DOMAIN].values():
                if entry_data.get("device_id") == device_id:
                    entry_id = entry_data["entry_id"]
                    break
            if not entry_id:
                _LOGGER.error("Устройство %s не найдено", device_id)
                return

            await _send_single_config(hass, entry_id)    

        hass.services.async_register(DOMAIN, "send_data", send_to_narodmon)

    # Запускаем периодическую отправку (фоновая задача)
    await _restart_send_task(hass, entry)

    # Слушатель обновления записи – перезапускаем только задачу, не загружаем сенсоры повторно
    async def _entry_updated(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> None:
        _LOGGER.debug("Запись %s обновлена, перезапускаем задачу", entry.entry_id)
        await _restart_send_task(hass, entry)

    entry.async_on_unload(entry.add_update_listener(_entry_updated))

    # Загружаем сенсоры только если они ещё не загружены
    if not hass.data[DOMAIN][entry.entry_id].get("sensors_loaded", False):
        try:
            await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
            hass.data[DOMAIN][entry.entry_id]["sensors_loaded"] = True
            _LOGGER.debug("Сенсоры загружены для %s", entry.entry_id)
        except Exception as e:
            _LOGGER.error("Ошибка при загрузке сенсоров: %s", e)
    else:
        _LOGGER.debug("Сенсоры уже загружены для %s, пропускаем", entry.entry_id)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Выгрузка интеграции."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if not unload_ok:
        return unload_ok

    data = hass.data[DOMAIN].get(entry.entry_id) 
    if data:
        if "task" in data:
            data["task"].cancel()
            with suppress(asyncio.CancelledError):
                await data["task"]
            _LOGGER.debug("Задача отправки для %s отменена", entry.entry_id)
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _restart_send_task(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> None:
    """Перезапуск задачи периодической отправки."""
    old_task = hass.data[DOMAIN].get(entry.entry_id, {}).get("task")
    if old_task and not old_task.done():
        old_task.cancel()
        _LOGGER.debug("Старая задача для %s отменена", entry.entry_id)

    timeout = entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
    if timeout > 0:
        new_task = asyncio.create_task(_periodic_send(hass, entry.entry_id))
        hass.data[DOMAIN][entry.entry_id]["task"] = new_task
        _LOGGER.info("Запущена периодическая отправка с интервалом %d мин для %s", timeout, entry.entry_id)
    else:
        hass.data[DOMAIN].get(entry.entry_id, {}).pop("task", None)
        _LOGGER.info("Автоматическая отправка отключена (таймаут = 0) для %s", entry.entry_id)


async def _periodic_send(hass: HomeAssistant, entry_id: str) -> None:
    """Фоновая задача отправки."""
    while True:
        try:
            entry = hass.config_entries.async_get_entry(entry_id)
            if not entry:
                _LOGGER.error("Запись %s не найдена, завершаем задачу", entry_id)
                break

            timeout_min = entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
            if timeout_min <= 0:
                _LOGGER.info("Таймаут = 0, завершаем задачу отправки для %s", entry_id)
                break

            interval = timeout_min * 60
            await asyncio.sleep(interval)
            await _send_single_config(hass, entry_id)

        except asyncio.CancelledError:
            _LOGGER.debug("Задача отправки для %s отменена", entry_id)
            break
        except Exception as e:
            _LOGGER.error("Ошибка в периодической отправке: %s", e)
            await asyncio.sleep(60)


async def _send_single_config(hass: HomeAssistant, entry_id: str) -> None:
    """Отправка данных для одной конфигурации."""																			   
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry:
        _LOGGER.error("Запись %s не найдена", entry_id)
        return

    data = entry.data
    options = entry.options

    mac = data.get(CONF_MAC)
    sensor_map = options.get(CONF_SENSOR_MAP) or data.get(CONF_SENSOR_MAP, {})
    optimistic_sensors = set(options.get(
        CONF_OPTIMISTIC_SENSORS, data.get(CONF_OPTIMISTIC_SENSORS, [])
    ))

    if not mac or not sensor_map:
        _LOGGER.warning("Пропускаем отправку для %s: MAC или сенсоры не заданы", entry_id)
        await _store_send_result(hass, entry_id, error="MAC или сенсоры не заданы")
        return

    # Загружаем сохранённые штампы (ключ — sensor_id)
    if entry_id not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry_id] = {}
    sent_timestamps = hass.data[DOMAIN][entry_id].get("last_sent_timestamps", {})
    updated_timestamps = sent_timestamps.copy()  # будем обновлять после успеха

    _LOGGER.info("Отправка показаний для MAC %s", mac)
    sensor_lines = []
    send_timestamp = int(datetime.now(timezone.utc).timestamp())

    for entity_id, sensor_id in sensor_map.items():
        state = hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("Сенсор %s не найден", entity_id)
            continue

        try:
            value = float(state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Значение сенсора %s ('%s') не является числом", entity_id, state.state)
            continue

        if not (value > -1e308 and value < 1e308):
            _LOGGER.warning("Значение сенсора %s (%s) вне допустимого диапазона", entity_id, value)
            continue

        reported_timestamp = state.last_reported.timestamp()
        optimistic = entity_id in optimistic_sensors
        last_sent = sent_timestamps.get(sensor_id)

        # В обычном режиме отправляем только новые отчёты, даже при прежнем значении.
        if not optimistic and last_sent == reported_timestamp:
            _LOGGER.debug("Сенсор %s (id=%s) не сообщал новых показаний, пропускаем", entity_id, sensor_id)
            continue

        # Подготовка к отправке
        current_timestamp = send_timestamp if optimistic else int(reported_timestamp)
        sensor_lines.append(f"#{sensor_id}#{value:.2f}#{current_timestamp}\n")
        _LOGGER.debug("%s = %s (штамп: %d)", sensor_id, value, current_timestamp)

        # Обновляем штамп в копии
        updated_timestamps[sensor_id] = reported_timestamp

    if not sensor_lines:
        _LOGGER.error("Нет валидных показаний для отправки")
        await _store_send_result(hass, entry_id, error="Нет валидных показаний")
        return

    # Формируем пакет
    header_parts = [f"#{mac}"]
    if data.get(CONF_NAME):
        header_parts.append(f"#{data[CONF_NAME]}")

    header_line = "".join(header_parts) + "\n"
    packet = header_line + "".join(sensor_lines) + "##"

    _LOGGER.info("Отправка пакета:\n%s", packet)

    try:
        response = await _send_via_tcp(packet)
        _LOGGER.info("Ответ сервера: %s", response)

        # ✅ Сохраняем обновлённые штампы только после успешной отправки
        hass.data[DOMAIN][entry_id]["last_sent_timestamps"] = updated_timestamps

        await _store_send_result(
            hass, entry_id,
            timestamp=datetime.now(timezone.utc),
            packet=packet,
            response=response
        )
    except asyncio.TimeoutError:
        error_msg = f"Тайм-аут при отправке данных на {NARODMON_HOST}:{NARODMON_PORT}"
        _LOGGER.error(error_msg)
        await _store_send_result(hass, entry_id, error=error_msg)
    except Exception as e:
        error_msg = f"Ошибка при отправке: {e}"
        _LOGGER.error(error_msg)
        await _store_send_result(hass, entry_id, error=error_msg)


async def _send_via_tcp(packet: str) -> str:
    """Отправка данных через TCP-сокет."""
    writer = None
    try:
        async with asyncio.timeout(TCP_TIMEOUT):
            reader, writer = await asyncio.open_connection(NARODMON_HOST, NARODMON_PORT)
            writer.write(packet.encode("utf-8"))
            await writer.drain()
            response = await reader.read(1024)
    finally:
        if writer is not None:
            writer.close()
            with suppress(TimeoutError, OSError):
                async with asyncio.timeout(TCP_CLOSE_TIMEOUT):
                    await writer.wait_closed()

    return response.decode("utf-8").strip()


async def _store_send_result(hass, entry_id, timestamp=None, packet=None, response=None, error=None):
    """Сохраняет результат отправки в hass.data для отображения в сенсорах."""
    if entry_id not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry_id] = {}

    send_info = hass.data[DOMAIN][entry_id].get("last_send", {})

    if timestamp:
        send_info["timestamp"] = timestamp.isoformat()
    elif timestamp is None and not error:
        send_info["timestamp"] = datetime.now(timezone.utc).isoformat()

    if packet is not None:
        send_info["packet"] = packet
    if response is not None:
        send_info["response"] = response
    if error is not None:
        send_info["error"] = error

    hass.data[DOMAIN][entry_id]["last_send"] = send_info

    # ✅ НОВОЕ: если в hass.data есть обновлённые штампы, сохраняем их отдельно
    if "last_sent_timestamps" in hass.data[DOMAIN][entry_id]:
        # Они уже будут сохранены, ничего дополнительно не делаем
        pass
