import logging
import aiohttp
from homeassistant.components.button import ButtonEntity
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
    CONF_BASE_URL
)
from .api import async_get_token, async_control_solar_sell, DeyeCloudAPIError

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup Buttons dynamically."""
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
                
                # 4. For Loop Inverter
                for device in devices_data:
                    if device.get("deviceType") == "INVERTER":
                        sn = device["deviceSn"]
                        # Enable Solar Sell
                        entities.append(DeyeSolarSellButton(
                            hass, username, password, app_id, app_secret, base_url, sn, 
                            "Enable", True, "mdi:solar-power"
                        ))
                        # Disable Solar Sell
                        entities.append(DeyeSolarSellButton(
                            hass, username, password, app_id, app_secret, base_url, sn, 
                            "Disable", False, "mdi:solar-power-variant-outline"
                        ))
                        _LOGGER.info(f"Created Solar Sell buttons for device: {sn}")

    except Exception as e:
        _LOGGER.error(f"Error setting up Deye buttons: {e}")

    async_add_entities(entities)

class DeyeSolarSellButton(ButtonEntity):
    def __init__(self, hass, username, password, app_id, app_secret, base_url, device_sn, action_name, is_enable, icon):
        self.hass = hass
        self._username = username
        self._password = password
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._device_sn = device_sn
        self._is_enable = is_enable
        
        self._attr_name = f"Deye Solar Sell {action_name}"
        self._attr_unique_id = f"{device_sn}_solar_sell_{action_name.lower()}_btn"
        self._attr_icon = icon

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_sn)},
            "name": f"Deye Inverter {self._device_sn}",
            "manufacturer": "Deye",
            "model": "Inverter",
        }
    
    async def async_press(self) -> None:
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
            await async_control_solar_sell(
                session, 
                token, 
                self._base_url, 
                self._device_sn, 
                self._is_enable
            )
        except DeyeCloudAPIError as e:
            _LOGGER.error(f"Failed to press button {self.name}: {e}")
            raise HomeAssistantError(f"Failed to control solar sell for {self._device_sn}: {e}") from e
        except Exception as e:
            _LOGGER.error(f"Unexpected error pressing button {self.name}: {e}")
            raise HomeAssistantError(f"Unexpected error controlling solar sell: {e}") from e
