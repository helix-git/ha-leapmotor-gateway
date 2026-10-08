# Third party notices

This project builds on the work of others.

## Adapted code

**[kerniger/leapmotor-ha](https://github.com/kerniger/leapmotor-ha) 0.7.3, MIT License, Copyright (c) 2026 kerniger.**

| Here | Origin in leapmotor-ha | What |
|---|---|---|
| `addon/app/commands.py`: `climate_payload()`, `t03_off_payload()`, `T03_AC_OFF_PAYLOAD` | `api.py` `_build_climate_payload`, `model_helpers.py` | Climate on/off payload for the T03. Climate off needs the full payload on the T03. |
| `addon/app/gateway.py`: `_today_breakdown()` | `api.py` `get_consumption_today_breakdown` | `getLastweekEC` with a time window for today's energy shares |
| `addon/app/gateway.py`: `_distance_energy_7_days()` | `api.py` `get_mileage_energy_detail` | `mileage/energy/detail` with a signed time window for total energy and distance |

The license text is at the end of this file.

## Dependencies (not copied)

| Package | License | Used for |
|---|---|---|
| [leapmotor-api](https://github.com/markoceri/leapmotor-api) (markoceri) | AGPL-3.0-or-later | Login, request signing, vehicle list, status, remote commands. Installed from PyPI when the app is built. |
| [FastAPI](https://github.com/fastapi/fastapi), [Starlette](https://github.com/encode/starlette), [Pydantic](https://github.com/pydantic/pydantic) | MIT, BSD-3-Clause, MIT | Web interface and API |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | Web server |
| [Requests](https://github.com/psf/requests) | Apache-2.0 | Home Assistant and Supervisor API |
| [websocket-client](https://github.com/websocket-client/websocket-client) | Apache-2.0 | Home Assistant events |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | Certificate upload |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU | Vehicle image |
| [tzdata](https://github.com/python/tzdata) | Apache-2.0 | Time zones |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 OR BSD-3-Clause | Certificate check (via leapmotor-api) |
| [Home Assistant base images](https://github.com/home-assistant/docker-base), [bashio](https://github.com/hassio-addons/bashio), [s6-overlay](https://github.com/just-containers/s6-overlay) | Apache-2.0, MIT, ISC | App container |

## Trademarks

Leapmotor is a trademark of its owner. This project is not affiliated with Leapmotor and uses an
undocumented interface that may change at any time.

---

## MIT License, kerniger/leapmotor-ha

```
MIT License

Copyright (c) 2026 kerniger

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
