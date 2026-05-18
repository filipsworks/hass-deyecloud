import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import async_get_token, async_get_tou, async_update_tou
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

NUMERIC_CONFIGS = [
    # (key_in_api, ha_name, unique_id_suffix, min_val, max_val, step, unit)
    ("power", "Program %d Power", "program_%d_power", 0, 30000, 50, "W"),
    ("soc", "Program %d SOC", "program_%d_soc", 0, 100, 1, "%"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup ToU number entities."""
    config = entry.data
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    app_id = config.get(CONF_APP_ID)
    app_secret = config.get(CONF_APP_SECRET)
    base_url = config.get(CONF_BASE_URL)

    session = async_get_clientsession(hass)
    entities = []

    try:
        token = await async_get_token(
            session, username, password, app_id, app_secret, base_url
        )
        stations_resp = await _async_station_list(session, token, base_url)
        station_ids = [st.get("id") or st.get("stationId") for st in stations_resp]

        if station_ids:
            devices_resp = await _async_device_list(
                session, token, base_url, station_ids
            )
            for device in devices_resp:
                if device.get("deviceType") == "INVERTER":
                    sn = device["deviceSn"]
                    tou_data = await async_get_tou(session, token, base_url, sn)

                    items = tou_data.get("timeUseSettingItems", [])
                    for i in range(1, 7):
                        if i - 1 < len(items):
                            item = items[i - 1]
                        else:
                            item = {}

                        for (
                            api_key,
                            name_fmt,
                            uid_suffix,
                            min_val,
                            max_val,
                            step,
                            unit,
                        ) in NUMERIC_CONFIGS:
                            value = item.get(api_key)
                            entities.append(
                                DeyeTouNumber(
                                    hass,
                                    username,
                                    password,
                                    app_id,
                                    app_secret,
                                    base_url,
                                    sn,
                                    i,
                                    api_key,
                                    name_fmt,
                                    uid_suffix,
                                    min_val,
                                    max_val,
                                    step,
                                    unit,
                                    value,
                                )
                            )

    except Exception as e:
        _LOGGER.error("Error setting up Deye ToU numbers: %s", e)

    async_add_entities(entities)


class DeyeTouNumber(NumberEntity):
    """Representation of a ToU number entity (power or SOC)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        app_id: str,
        app_secret: str,
        base_url: str,
        device_sn: str,
        program_num: int,
        api_key: str,
        name_fmt: str,
        unique_id_suffix: str,
        min_val: float,
        max_val: float,
        step: float,
        unit: str,
        initial_value: float | None,
    ):
        self.hass = hass
        self._username = username
        self._password = password
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._device_sn = device_sn
        self._program_num = program_num
        self._api_key = api_key
        self._min_val = min_val
        self._max_val = max_val
        self._step = step
        self._unit = unit

        self._attr_name = name_fmt % program_num
        self._attr_unique_id = f"{device_sn}_{unique_id_suffix % program_num}"
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_native_value = initial_value

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_sn)},
            "name": f"Deye Inverter {self._device_sn}",
            "manufacturer": "Deye",
            "model": "Inverter",
        }

    async def async_update(self) -> None:
        """Fetch latest state from API."""
        session = async_get_clientsession(self.hass)
        try:
            token = await async_get_token(
                session,
                self._username,
                self._password,
                self._app_id,
                self._app_secret,
                self._base_url,
            )
            tou_data = await async_get_tou(
                session, token, self._base_url, self._device_sn
            )
            items = tou_data.get("timeUseSettingItems", [])
            idx = self._program_num - 1
            if idx < len(items):
                self._attr_native_value = items[idx].get(self._api_key)
        except Exception as e:
            _LOGGER.error("Failed to update %s: %s", self.unique_id, e)

    async def async_set_native_value(self, value: float) -> None:
        """Set new value via API."""
        session = async_get_clientsession(self.hass)
        try:
            token = await async_get_token(
                session,
                self._username,
                self._password,
                self._app_id,
                self._app_secret,
                self._base_url,
            )
            tou_data = await async_get_tou(
                session, token, self._base_url, self._device_sn
            )
            items = tou_data.get("timeUseSettingItems", [])

            # Ensure we have enough slots
            while len(items) < self._program_num:
                items.append(
                    {
                        "power": 15000,
                        "voltage": 49,
                        "time": "0000",
                        "enableGridCharge": False,
                        "enableGeneration": False,
                        "soc": 20,
                    }
                )

            # Update the specific key on this program slot
            items[self._program_num - 1][self._api_key] = value

            await async_update_tou(
                session, token, self._base_url, self._device_sn, items
            )
            self._attr_native_value = value
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Failed to set %s to %.1f: %s", self.unique_id, value, e)


async def _async_station_list(session, token, base_url):
    url = f"{base_url}/station/list"
    headers = {"Authorization": f"Bearer {token}"}
    async with session.post(url, headers=headers, json={}, timeout=10) as resp:
        resp.raise_for_status()
        return (await resp.json()).get("stationList", [])


async def _async_device_list(session, token, base_url, station_ids):
    url = f"{base_url}/station/device"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"page": 1, "size": 20, "stationIds": station_ids}
    async with session.post(url, headers=headers, json=payload, timeout=10) as resp:
        resp.raise_for_status()
        return (await resp.json()).get("deviceListItems", [])
