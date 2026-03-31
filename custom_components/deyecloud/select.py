import logging
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import HomeAssistantError
from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    WORK_MODES,
)
from .api import async_get_token, async_control_system_work_mode, async_get_system_config, DeyeCloudAPIError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup work mode select entity."""
    config = entry.data
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    app_id = config.get(CONF_APP_ID)
    app_secret = config.get(CONF_APP_SECRET)
    base_url = config.get(CONF_BASE_URL)
    
    session = async_get_clientsession(hass)
    entities = []

    try:
        # 1. Get Token
        token = await async_get_token(session, username, password, app_id, app_secret, base_url)
        
        # 2. Station List
        station_url = f"{base_url}/station/list"
        headers = {"Authorization": f"Bearer {token}"}
        async with session.post(station_url, headers=headers, json={}, timeout=10) as resp:
            resp.raise_for_status()
            stations_data = (await resp.json()).get("stationList", [])

        # 3. Device List From Station
        station_ids = [st.get("id") or st.get("stationId") for st in stations_data]
        if station_ids:
            device_url = f"{base_url}/station/device"
            payload = {"page": 1, "size": 20, "stationIds": station_ids}
            async with session.post(device_url, headers=headers, json=payload, timeout=10) as resp:
                resp.raise_for_status()
                devices_data = (await resp.json()).get("deviceListItems", [])
                
                # 4. Create select entity for each inverter
                for device in devices_data:
                    if device.get("deviceType") == "INVERTER":
                        sn = device["deviceSn"]
                        entities.append(DeyeWorkModeSelect(
                            hass, username, password, app_id, app_secret, base_url, sn
                        ))
                        _LOGGER.info(f"Created work mode select for device: {sn}")

    except Exception as e:
        _LOGGER.error(f"Error setting up Deye work mode select: {e}")

    async_add_entities(entities)


class DeyeWorkModeSelect(SelectEntity):
    def __init__(self, hass, username, password, app_id, app_secret, base_url, device_sn):
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

    async def async_added_to_hass(self) -> None:
        """Called when entity is added to Home Assistant."""
        # Fetch work mode immediately on startup
        await self.async_update()
    
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
                self._base_url
            )
            
            config_data = await async_get_system_config(
                session,
                token,
                self._base_url,
                self._device_sn
            )
            
            # Extract work mode from response
            work_mode = config_data.get("workMode")
            if work_mode and work_mode in WORK_MODES:
                self._current_option = WORK_MODES[work_mode]
            
        except Exception as e:
            _LOGGER.error(f"Failed to update work mode: {e}")
            # Don't raise here - just log. Update failures shouldn't break the entity
    
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
                self._base_url
            )
            
            # Convert display name back to API value
            work_mode_value = [k for k, v in WORK_MODES.items() if v == option][0]
            
            await async_control_system_work_mode(
                session, 
                token, 
                self._base_url, 
                self._device_sn, 
                work_mode_value
            )
            
            self._current_option = option
            self.async_write_ha_state()
            
        except DeyeCloudAPIError as e:
            _LOGGER.error(f"Failed to set work mode for {self._device_sn}: {e}")
            raise HomeAssistantError(f"Failed to set work mode: {e}") from e
        except Exception as e:
            _LOGGER.error(f"Unexpected error setting work mode: {e}")
            raise HomeAssistantError(f"Unexpected error setting work mode: {e}") from e
