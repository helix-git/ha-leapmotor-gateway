"""Small client for the /api/v2 interface of the Leapmotor Gateway app."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class GatewayError(Exception):
    """App not reachable or request rejected."""


class GatewayClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, token: str) -> None:
        self._s, self._base, self._token = session, f"http://{host}:{port}/api/v2", token

    async def _request(self, method: str, path: str, **kw) -> Any:
        try:
            async with asyncio.timeout(20):
                async with self._s.request(method, self._base + path,
                                           headers={"Authorization": f"Bearer {self._token}"}, **kw) as r:
                    if r.status == 401:
                        raise GatewayError("token rejected")
                    if r.status >= 400:
                        raise GatewayError(f"HTTP {r.status}: {(await r.text())[:200]}")
                    if r.content_type == "application/json":
                        return await r.json()
                    return await r.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GatewayError(str(err)) from err

    async def vehicles(self) -> dict:
        """{"gateway": {...}, "vehicles": {VIN: {...}}}"""
        return await self._request("GET", "/vehicles")

    async def image(self, vin: str) -> bytes:
        return await self._request("GET", "/image", params={"vin": vin})

    async def command(self, action: str, params: dict | None, user_id: str | None, user_name: str,
                      vin: str | None = None) -> dict:
        return await self._request("POST", "/command", json={"action": action, "params": params or {}, "vin": vin,
                                                             "user_id": user_id, "user_name": user_name})

    async def charge_history(self, vin: str, days: int) -> list:
        return await self._request("GET", "/charge_history", params={"vin": vin, "days": int(days)})
