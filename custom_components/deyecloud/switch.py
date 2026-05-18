import json
import logging

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    async_get_token,
    async_get_tou,
    async_update_tou,
)
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_api_time(api_time: str) -> str:
    """Normalize any time string to 'HH:MM' format."""
    if not api_time:
        return "00:00"
    t = api_time.replace(":", "")
    if len(t) >= 4:
        return f"{t[0:2]}:{t[2:4]}"
    return api_time


async def _build_tou_payload_async(
    session, token, base_url, device_sn, program_num, grid_charge, gen_charge
):
    """Fetch all TOU items from API, overlay the changed charge flags, pad to 6 slots."""
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
    items[idx]["enableGridCharge"] = grid_charge
    items[idx]["enableGeneration"] = gen_charge

    # Normalize all time values to 'HH:MM' format
    for item in items:
        item["time"] = _normalize_api_time(item.get("time", ""))

    return items


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup ToU per-program charge switches."""
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

        # Station List
        stations_data = await _async_station_list(session, token, base_url)

        # Device List From Station
        station_ids = [st.get("id") or st.get("stationId") for st in stations_data]
        if station_ids:
            devices_data = await _async_device_list(
                session, token, base_url, station_ids
            )

            for device in devices_data:
                if device.get("deviceType") == "INVERTER":
                    sn = device["deviceSn"]

                    # ToU per-program grid charge and generation charge switches
                    tou_data = await async_get_tou(session, token, base_url, sn)
                    items = tou_data.get("timeUseSettingItems", [])
                    for i in range(1, 7):
                        if i - 1 < len(items):
                            item = items[i - 1]
                        else:
                            item = {}

                        entities.append(
                            DeyeTouGridChargeSwitch(
                                hass,
                                username,
                                password,
                                app_id,
                                app_secret,
                                base_url,
                                sn,
                                i,
                                item.get("enableGridCharge", False),
                            )
                        )

                        entities.append(
                            DeyeTouGenerationChargeSwitch(
                                hass,
                                username,
                                password,
                                app_id,
                                app_secret,
                                base_url,
                                sn,
                                i,
                                item.get("enableGeneration", False),
                            )
                        )

    except Exception as e:
        _LOGGER.error("Error setting up Deye switches: %s", e)

    async_add_entities(entities)


# ===================================================================
# Per-program Grid Charge Switch
# ===================================================================


class DeyeTouGridChargeSwitch(SwitchEntity):
    """Per-program enableGridCharge switch."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass,
        username,
        password,
        app_id,
        app_secret,
        base_url,
        device_sn,
        program_num: int,
        initial_value: bool,
    ):
        self.hass = hass
        self._username = username
        self._password = password
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._device_sn = device_sn
        self._program_num = program_num

        self._attr_name = f"Program {program_num} Grid Charge"
        self._attr_unique_id = f"{device_sn}_program_{program_num}_grid_charge"
        self._is_on = initial_value

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_sn)},
            "name": f"Deye Inverter {self._device_sn}",
            "manufacturer": "Deye",
            "model": "Inverter",
        }

    @property
    def is_on(self) -> bool | None:
        return self._is_on

    async def async_update(self) -> None:
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
                self._is_on = items[idx].get("enableGridCharge", False)
        except Exception as e:
            _LOGGER.error("Failed to update %s: %s", self.unique_id, e)

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_charging(True, False)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_charging(False, False)

    async def _set_charging(self, grid_charge: bool, gen_charge: bool) -> None:
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

            items = await _build_tou_payload_async(
                session,
                token,
                self._base_url,
                self._device_sn,
                self._program_num,
                grid_charge,
                gen_charge,
            )

            payload = {"deviceSn": self._device_sn, "timeUseSettingItems": items}
            async_create(
                self.hass,
                json.dumps(payload, indent=2),
                title="Deye TOU Update Payload",
                notification_id=f"tou_payload_{self._device_sn}_{self._program_num}_grid",
            )

            await async_update_tou(
                session, token, self._base_url, self._device_sn, items
            )
            self._is_on = grid_charge
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Failed to set %s: %s", self.unique_id, e)


# ===================================================================
# Per-program Generation Charge Switch
# ===================================================================


class DeyeTouGenerationChargeSwitch(SwitchEntity):
    """Per-program enableGeneration switch."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass,
        username,
        password,
        app_id,
        app_secret,
        base_url,
        device_sn,
        program_num: int,
        initial_value: bool,
    ):
        self.hass = hass
        self._username = username
        self._password = password
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._device_sn = device_sn
        self._program_num = program_num

        self._attr_name = f"Program {program_num} Generation Charge"
        self._attr_unique_id = f"{device_sn}_program_{program_num}_generation_charge"
        self._is_on = initial_value

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_sn)},
            "name": f"Deye Inverter {self._device_sn}",
            "manufacturer": "Deye",
            "model": "Inverter",
        }

    @property
    def is_on(self) -> bool | None:
        return self._is_on

    async def async_update(self) -> None:
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
                self._is_on = items[idx].get("enableGeneration", False)
        except Exception as e:
            _LOGGER.error("Failed to update %s: %s", self.unique_id, e)

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_charging(False, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_charging(False, False)

    async def _set_charging(self, grid_charge: bool, gen_charge: bool) -> None:
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

            items = await _build_tou_payload_async(
                session,
                token,
                self._base_url,
                self._device_sn,
                self._program_num,
                grid_charge,
                gen_charge,
            )

            payload = {"deviceSn": self._device_sn, "timeUseSettingItems": items}
            async_create(
                self.hass,
                json.dumps(payload, indent=2),
                title="Deye TOU Update Payload",
                notification_id=f"tou_payload_{self._device_sn}_{self._program_num}_gen",
            )

            await async_update_tou(
                session, token, self._base_url, self._device_sn, items
            )
            self._is_on = gen_charge
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Failed to set %s: %s", self.unique_id, e)


# ===================================================================
# Helpers (shared with number.py / time.py — kept here to avoid circular imports)
# ===================================================================


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
