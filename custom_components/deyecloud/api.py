import hashlib
import json
import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)


class TouUpdateError(Exception):
    """Carries the full request/response dump for notifications."""

    def __init__(self, message: str, dump: str):
        super().__init__(message)
        self.dump = dump


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
        "timeUseSettingItems": time_use_setting_items,
    }

    safe_headers = {**headers, "Authorization": "Bearer <redacted>"}
    async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
        body = await resp.text()
        dump = (
            f"POST {url}\n"
            f"Headers: {json.dumps(safe_headers, indent=2)}\n"
            f"Body: {json.dumps(payload, indent=2)}\n"
            f"\n--- Response ---\n"
            f"HTTP {resp.status}\n"
            f"{body}"
        )
        _LOGGER.warning("TOU update:\n%s", dump)
        if resp.status >= 400:
            raise TouUpdateError(
                f"TOU update HTTP {resp.status}: {body or '(empty body)'}", dump
            )
        try:
            data = json.loads(body) if body else {}
        except ValueError:
            raise TouUpdateError(f"TOU update returned non-JSON body", dump)
        if isinstance(data, dict) and data.get("success") is False:
            raise TouUpdateError(
                f"TOU update rejected: code={data.get('code')} msg={data.get('msg')}",
                dump,
            )
        return data
