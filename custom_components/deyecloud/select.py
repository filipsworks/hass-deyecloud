import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    async_control_system_work_mode,
    async_get_system_config,
    async_get_token,
    async_get_tou,
    async_switch_tou,
    async_update_tou,
)
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    WORK_MODES,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ToU charging type options
# ---------------------------------------------------------------------------
TOU_CHARGING_OPTIONS = ["Off", "Grid Charge", "Generation Charge"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup work mode and ToU select entities."""
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

                    # Work mode select
                    entities.append(
                        DeyeWorkModeSelect(
                            hass,
                            username,
                            password,
                            app_id,
                            app_secret,
                            base_url,
                            sn,
                        )
                    )

                    # ToU switch
                    tou_data = await async_get_tou(session, token, base_url, sn)
                    tou_action = tou_data.get("touAction", "off")
                    entities.append(
                        DeyeTouSwitch(
                            hass,
                            username,
                            password,
                            app_id,
                            app_secret,
                            base_url,
                            sn,
                            tou_action,
                        )
                    )

                    # Per-program charging selects
                    items = tou_data.get("timeUseSettingItems", [])
                    for i in range(1, 7):
                        if i - 1 < len(items):
                            item = items[i - 1]
                        else:
                            item = {}
                        entities.append(
                            DeyeTouChargingSelect(
                                hass,
                                username,
                                password,
                                app_id,
                                app_secret,
                                base_url,
                                sn,
                                i,
                                item,
                            )
                        )

    except Exception as e:
        _LOGGER.error("Error setting up Deye selects: %s", e)

    async_add_entities(entities)


# ===================================================================
# Work Mode Select (existing)
# ===================================================================


class DeyeWorkModeSelect(SelectEntity):
    def __init__(
        self, hass, username, password, app_id, app_secret, base_url, device_sn
    ):
        self.hass = hass
        self._username = username
        self._password = password
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._device_sn = device_sn
        self._current_option = None

        self._attr_name = f"Deye Work Mode"
        self._attr_unique_id = f"{device_sn}_work_mode_select"
        self._attr_options = list(WORK_MODES.values())

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_sn)},
            "name": f"Deye Inverter {self._device_sn}",
            "manufacturer": "Deye",
            "model": "Inverter",
        }

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self._current_option

    async def async_update(self) -> None:
        """Update the current work mode from API."""
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

            config_data = await async_get_system_config(
                session, token, self._base_url, self._device_sn
            )

            work_mode = config_data.get("workMode")
            if work_mode and work_mode in WORK_MODES:
                self._current_option = WORK_MODES[work_mode]

        except Exception as e:
            _LOGGER.error("Failed to update work mode: %s", e)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
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

            work_mode_value = [k for k, v in WORK_MODES.items() if v == option][0]

            await async_control_system_work_mode(
                session, token, self._base_url, self._device_sn, work_mode_value
            )

            self._current_option = option
            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error("Failed to set work mode: %s", e)


# ===================================================================
# ToU Switch (on/off)
# ===================================================================


class DeyeTouSwitch(SelectEntity):
    """Main ToU on/off select entity."""

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
        initial_action: str,
    ):
        self.hass = hass
        self._username = username
        self._password = password
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._device_sn = device_sn

        self._attr_name = "Time of Use"
        self._attr_unique_id = f"{device_sn}_time_of_use"
        self._attr_options = ["Off", "On"]
        self._current_option = "On" if initial_action == "on" else "Off"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_sn)},
            "name": f"Deye Inverter {self._device_sn}",
            "manufacturer": "Deye",
            "model": "Inverter",
        }

    @property
    def current_option(self) -> str | None:
        return self._current_option

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
            action = tou_data.get("touAction", "off")
            self._current_option = "On" if action == "on" else "Off"
        except Exception as e:
            _LOGGER.error("Failed to update ToU switch: %s", e)

    async def async_select_option(self, option: str) -> None:
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
            action = "on" if option == "On" else "off"
            await async_switch_tou(
                session, token, self._base_url, self._device_sn, action
            )
            self._current_option = option
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Failed to set ToU switch: %s", e)


# ===================================================================
# ToU Charging Type Select (per program slot)
# ===================================================================


class DeyeTouChargingSelect(SelectEntity):
    """Per-program charging type select entity."""

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
        item: dict,
    ):
        self.hass = hass
        self._username = username
        self._password = password
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._device_sn = device_sn
        self._program_num = program_num

        self._attr_name = f"Program {program_num} Charging"
        self._attr_unique_id = f"{device_sn}_program_{program_num}_charging"
        self._attr_options = TOU_CHARGING_OPTIONS

        # Derive initial option from API item
        grid = item.get("enableGridCharge", False)
        gen = item.get("enableGeneration", False)
        if grid and not gen:
            self._current_option = "Grid Charge"
        elif gen and not grid:
            self._current_option = "Generation Charge"
        else:
            self._current_option = "Off"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_sn)},
            "name": f"Deye Inverter {self._device_sn}",
            "manufacturer": "Deye",
            "model": "Inverter",
        }

    @property
    def current_option(self) -> str | None:
        return self._current_option

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
                item = items[idx]
                grid = item.get("enableGridCharge", False)
                gen = item.get("enableGeneration", False)
                if grid and not gen:
                    self._current_option = "Grid Charge"
                elif gen and not grid:
                    self._current_option = "Generation Charge"
                else:
                    self._current_option = "Off"
        except Exception as e:
            _LOGGER.error("Failed to update %s: %s", self.unique_id, e)

    async def async_select_option(self, option: str) -> None:
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

            # Read current ToU data to preserve other fields
            tou_data = await async_get_tou(
                session, token, self._base_url, self._device_sn
            )
            items = tou_data.get("timeUseSettingItems", [])

            # Ensure enough slots exist
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

            # Set charging flags based on selected option
            idx = self._program_num - 1
            if option == "Grid Charge":
                items[idx]["enableGridCharge"] = True
                items[idx]["enableGeneration"] = False
            elif option == "Generation Charge":
                items[idx]["enableGridCharge"] = False
                items[idx]["enableGeneration"] = True
            else:  # Off
                items[idx]["enableGridCharge"] = False
                items[idx]["enableGeneration"] = False

            await async_update_tou(
                session, token, self._base_url, self._device_sn, items
            )
            self._current_option = option
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
