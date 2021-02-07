"""Support for Dyson devices."""

import asyncio
import logging
from functools import partial
from typing import List

from homeassistant.exceptions import ConfigEntryNotReady
from libdyson.discovery import DysonDiscovery
from libdyson.dyson_account import DysonDeviceInfo
from libdyson.dyson_device import DysonDevice
from libdyson.exceptions import DysonException
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.components.zeroconf import async_get_instance
from libdyson import Dyson360Eye, get_device, MessageType

from .const import CONF_DEVICE_TYPE, DATA_DEVICES, DATA_DISCOVERY, DOMAIN, CONF_CREDENTIAL, CONF_SERIAL, DEVICE_TYPE_NAMES

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Dyson integration."""
    hass.data[DOMAIN] = {
        DATA_DEVICES: {},
        DATA_DISCOVERY: None,
    }
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dyson from a config entry."""
    device = get_device(
        entry.data[CONF_SERIAL],
        entry.data[CONF_CREDENTIAL],
        entry.data[CONF_DEVICE_TYPE],
    )

    async def _async_forward_entry_setup():
        for component in _async_get_platform(device):
            hass.async_create_task(
                hass.config_entries.async_forward_entry_setup(entry, component)
            )

    def setup_entry(host: str, is_discovery: bool=True) -> bool:
        try:
            device.connect(host)
            # TODO: environmental state update
        except DysonException:
            if is_discovery:
                _LOGGER.error(
                    "Failed to connect to device %s at %s",
                    device.serial,
                    host,
                )
                return
            raise ConfigEntryNotReady
        hass.data[DOMAIN][DATA_DEVICES][entry.entry_id] = device
        asyncio.run_coroutine_threadsafe(
            _async_forward_entry_setup(), hass.loop
        ).result()

    host = entry.data.get(CONF_HOST)
    if host:
        await hass.async_add_executor_job(
            partial(setup_entry, host, is_discovery=False)
        )
    else:
        discovery = hass.data[DOMAIN][DATA_DISCOVERY]
        if discovery is None:
            discovery = DysonDiscovery()
            hass.data[DOMAIN][DATA_DISCOVERY] = discovery
            _LOGGER.debug("Starting dyson discovery")
            discovery.start_discovery(await async_get_instance(hass))
            def stop_discovery(_):
                _LOGGER.debug("Stopping dyson discovery")
                discovery.stop_discovery()
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_discovery)

        await hass.async_add_executor_job(
            discovery.register_device, device, setup_entry
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Dyson local."""
    device = hass.data[DOMAIN][DATA_DEVICES][entry.entry_id]
    ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in _async_get_platform(device)
            ]
        )
    )
    if ok:
        hass.data[DOMAIN][DATA_DEVICES].pop(entry.entry_id)
        await hass.async_add_executor_job(device.disconnect)
        # TODO: stop discovery
    return ok


@callback
def _async_get_platform(device: DysonDevice) -> List[str]:
    if isinstance(device, Dyson360Eye):
        return ["binary_sensor", "sensor", "vacuum"]
    return ["fan", "sensor"]


class DysonEntity(Entity):

    _MESSAGE_TYPE = None

    def __init__(self, device: DysonDevice, name: str):
        self._device = device
        self._name = name

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        self._device.add_message_listener(self._on_message)

    def _on_message(self, message_type: MessageType) -> None:
        if self._MESSAGE_TYPE is None or message_type == self._MESSAGE_TYPE:
            self.schedule_update_ha_state()

    @property
    def should_poll(self) -> bool:
        """No polling needed."""
        return False

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return the entity unique id."""
        return self._device.serial

    @property
    def device_info(self) -> dict:
        """Return device info of the entity."""
        return {
            "identifiers": {(DOMAIN, self._device.serial)},
            "name": self._name,
            "manufacturer": "Dyson",
            "model": self._device.device_type,
        }
