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

                    # Fetch work mode during setup so it's not "unknown"
                    try:
                        token_for_config = await async_get_token(
                            session, username, password, app_id, app_secret, base_url
                        )
                        config_data = await async_get_system_config(
                            session, token_for_config, base_url, sn
                        )
                        work_mode = config_data.get("workMode")
                        initial_work_mode = (
                            WORK_MODES[work_mode]
                            if work_mode and work_mode in WORK_MODES
                            else None
                        )
                    except Exception:
                        _LOGGER.warning("Failed to fetch initial work mode for %s", sn)
                        initial_work_mode = None

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
                            initial_work_mode,
                        )
                    )

                    # ToU main switch (on/off)
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

    except Exception as e:
        _LOGGER.error("Error setting up Deye selects: %s", e)

    async_add_entities(entities)


# ===================================================================
# Work Mode Select (existing)
# ===================================================================


class DeyeWorkModeSelect(SelectEntity):
    def __init__(
        self,
        hass,
        username,
        password,
        app_id,
        app_secret,
        base_url,
        device_sn,
        initial_option=None,
    ):
        self.hass = hass
        self._username = username
        self._password = password
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._device_sn = device_sn
        self._current_option = initial_option

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
# ToU Main Switch (on/off) — kept as Select for On/Off options
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
