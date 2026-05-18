import asyncio
import json
import logging
from datetime import time

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.time import TimeEntity
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


def _api_time_to_seconds(api_time: str) -> int | None:
    """Convert API time string 'HHMM' or 'HH:MM' to seconds since midnight."""
    if not api_time:
        return None
    t = api_time.replace(":", "")
    if len(t) < 4:
        return None
    try:
        h = int(t[0:2])
        m = int(t[2:4])
        return h * 3600 + m * 60
    except (ValueError, IndexError):
        return None


def _seconds_to_api_time(seconds: int) -> str:
    """Convert seconds since midnight to API time string 'HH:MM'."""
    h = max(0, min(23, seconds // 3600))
    m = max(0, min(59, (seconds % 3600) // 60))
    return f"{h:02d}:{m:02d}"


def _normalize_api_time(api_time: str) -> str:
    """Normalize any time string to 'HH:MM' format."""
    if not api_time:
        return "00:00"
    t = api_time.replace(":", "")
    if len(t) >= 4:
        return f"{t[0:2]}:{t[2:4]}"
    return api_time


async def _build_tou_payload_async(
    session, token, base_url, device_sn, program_num, new_time_seconds
):
    """Fetch all TOU items from API, overlay the changed field, pad to 6 slots."""
    tou_data = await async_get_tou(session, token, base_url, device_sn)
    items = tou_data.get("timeUseSettingItems", [])

    # Pad missing slots with defaults so we always send 6
    while len(items) < 6:
        items.append(
            {
                "power": 15000,
                "voltage": 49,
                "time": "00:00",
                "enableGridCharge": False,
                "enableGeneration": False,
                "soc": 20,
            }
        )

    idx = program_num - 1
    api_time = _seconds_to_api_time(new_time_seconds)
    items[idx]["time"] = api_time

    # Normalize all time values to 'HH:MM' format
    for item in items:
        item["time"] = _normalize_api_time(item.get("time", ""))

    return items


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup ToU time entities."""
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
                            api_time = items[i - 1].get("time", "00:00")
                        else:
                            api_time = "00:00"

                        seconds = _api_time_to_seconds(api_time)
                        entities.append(
                            DeyeTouTime(
                                hass,
                                username,
                                password,
                                app_id,
                                app_secret,
                                base_url,
                                sn,
                                i,
                                seconds,
                            )
                        )

    except Exception as e:
        _LOGGER.error("Error setting up Deye ToU times: %s", e)

    async_add_entities(entities)


class DeyeTouTime(TimeEntity):
    """Representation of a ToU time entity."""

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
        initial_value: int | None,
    ):
        self.hass = hass
        self._username = username
        self._password = password
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._device_sn = device_sn
        self._program_num = program_num

        self._attr_name = f"Program {program_num} Time"
        self._attr_unique_id = f"{device_sn}_program_{program_num}_time"
        if initial_value is not None:
            h, rem = divmod(initial_value, 3600)
            m, s = divmod(rem, 60)
            self._attr_native_value = time(hour=h, minute=m, second=s)
        else:
            self._attr_native_value = None

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
                api_time = items[idx].get("time", "00:00")
                seconds = _api_time_to_seconds(api_time)
                if seconds is not None:
                    h, rem = divmod(seconds, 3600)
                    m, s = divmod(rem, 60)
                    self._attr_native_value = time(hour=h, minute=m, second=s)
        except Exception as e:
            _LOGGER.error("Failed to update %s: %s", self.unique_id, e)

    def set_value(self, value: time) -> None:
        """Set new time via API (synchronous entry point for time.set_value service)."""
        try:
            asyncio.run_coroutine_threadsafe(
                self.async_select_native_value(value), self.hass.loop
            ).result()
        except Exception as e:
            _LOGGER.error("Failed to set %s: %s", self.unique_id, e)

    async def async_select_native_value(self, value: time) -> None:
        """Set new time via API — fetch all from API, overlay changed field."""
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

            seconds = value.hour * 3600 + value.minute * 60
            items = await _build_tou_payload_async(
                session,
                token,
                self._base_url,
                self._device_sn,
                self._program_num,
                seconds,
            )

            payload = {"deviceSn": self._device_sn, "timeUseSettingItems": items}
            async_create(
                self.hass,
                json.dumps(payload, indent=2),
                title="Deye TOU Update Payload",
                notification_id=f"tou_payload_{self._device_sn}_{self._program_num}",
            )

            await async_update_tou(
                session, token, self._base_url, self._device_sn, items
            )
            self._attr_native_value = value
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Failed to set %s: %s", self.unique_id, e)


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
