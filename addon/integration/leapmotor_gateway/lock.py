"""Lock: locking is free, unlocking is decided by the app (role, approval)."""
from __future__ import annotations

from homeassistant.components.lock import LockEntity

from .entity import GatewayEntity, add_vehicles


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, lambda c, vin: [GatewayLock(c, vin, "lock")])


class GatewayLock(GatewayEntity, LockEntity):
    @property
    def is_locked(self):
        return self.values.get("locked")

    async def async_lock(self, **kwargs) -> None:
        await self._command("lock")

    async def async_unlock(self, **kwargs) -> None:
        # While the app waits for approval the lock stays locked until the next poll shows the new state.
        await self._command("unlock")
