import hashlib
import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)


def _sha256(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest().lower()


async def async_get_token(
    session: aiohttp.ClientSession, username, password, app_id, app_secret, base_url
):
    url = f"{base_url}/account/token?appId={app_id}"
    payload = {
        "appSecret": app_secret,
        "username": username,
        "password": _sha256(password),
    }
    async with session.post(url, json=payload, timeout=10) as resp:
        resp.raise_for_status()
        j = await resp.json()
        if not j.get("success"):
            raise Exception(f"Token request failed: {j.get('msg')}")
        return j["accessToken"]


async def async_control_solar_sell(
    session: aiohttp.ClientSession, token, base_url, device_sn, is_enable
):
    """Send Solar Sell control command."""
    url = f"{base_url}/order/sys/solarSell/control"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    action = "on" if is_enable else "off"

    payload = {"action": action, "deviceSn": device_sn}

    async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
        resp.raise_for_status()
        return await resp.json()


async def async_control_system_work_mode(
    session: aiohttp.ClientSession, token, base_url, device_sn, work_mode
):
    """Send system work mode control command."""
    url = f"{base_url}/order/sys/workMode/update"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {"deviceSn": device_sn, "workMode": work_mode}

    async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
        resp.raise_for_status()
        return await resp.json()


async def async_get_system_config(
    session: aiohttp.ClientSession, token, base_url, device_sn
):
    """Get current system configuration including work mode."""
    url = f"{base_url}/config/system"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {"deviceSn": device_sn}

    async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
        resp.raise_for_status()
        return await resp.json()


async def async_get_tou(session: aiohttp.ClientSession, token, base_url, device_sn):
    """Get current Time-of-Use configuration."""
    url = f"{base_url}/config/tou"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {"deviceSn": device_sn}

    async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
        resp.raise_for_status()
        return await resp.json()


async def async_switch_tou(
    session: aiohttp.ClientSession, token, base_url, device_sn, action
):
    """Switch ToU on or off.

    Args:
        action: "on" or "off"
    """
    url = f"{base_url}/order/sys/tou/switch"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {
        "action": action,
        "days": [
            "MONDAY",
            "TUESDAY",
            "WEDNESDAY",
            "THURSDAY",
            "FRIDAY",
            "SATURDAY",
            "SUNDAY",
        ],
        "deviceSn": device_sn,
    }

    async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
        resp.raise_for_status()
        return await resp.json()


async def async_update_tou(
    session: aiohttp.ClientSession, token, base_url, device_sn, time_use_setting_items
):
    """Update ToU time slots.

    Args:
        time_use_setting_items: list of dicts with keys: power, voltage, time, enableGridCharge, enableGeneration, soc
    """
    url = f"{base_url}/order/sys/tou/update"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {
        "deviceSn": device_sn,
        "days": [
            "MONDAY",
            "TUESDAY",
            "WEDNESDAY",
            "THURSDAY",
            "FRIDAY",
            "SATURDAY",
            "SUNDAY",
        ],
        "timeUseSettingItems": time_use_setting_items,
    }

    async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
        body = await resp.text()
        _LOGGER.warning("TOU update HTTP %s — request=%s response=%s", resp.status, payload, body)
        if resp.status >= 400:
            resp.raise_for_status()
        import json as _json
        data = _json.loads(body)
        if isinstance(data, dict) and data.get("success") is False:
            raise Exception(
                f"TOU update rejected: code={data.get('code')} msg={data.get('msg')}"
            )
        return data
