"""Константы для интеграции с Народным мониторингом."""

DOMAIN = "narodmon_sender"

# Конфигурационные ключи
CONF_MAC = "mac"
CONF_NAME = "name"
CONF_OWNER = "owner"
CONF_LAT = "lat"
CONF_LON = "lon"
CONF_ALT = "alt"
CONF_SENSOR_MAP = "sensor_map"
CONF_TIMEOUT = "timeout"

# Значения по умолчанию
DEFAULT_NAME = "Home Assistant"
DEFAULT_OWNER = ""
DEFAULT_LAT = "0"
DEFAULT_LON = "0"
DEFAULT_ALT = "0"
DEFAULT_TIMEOUT = 7
DEFAULT_SENSOR_MAP = {}

# URL и порт для отправки
NARODMON_HOST = "narodmon.ru"
NARODMON_PORT = 8283