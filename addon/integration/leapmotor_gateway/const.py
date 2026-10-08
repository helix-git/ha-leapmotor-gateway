"""Constants of the Leapmotor Gateway integration."""
DOMAIN = "leapmotor_gateway"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_TOKEN = "token"
DEFAULT_PORT = 8789
SCAN_INTERVAL_S = 15          # polls the local app, not the Leapmotor cloud, so it is cheap
PLATFORMS = ["sensor", "binary_sensor", "lock", "button", "device_tracker", "image", "climate", "number", "select",
             "switch"]
GRACE_S = 180                 # an unreachable app counts as a short outage this long (app update)
PRESET_DEFAULT = {"mode": "hot", "temperature": 22, "fan_speed": 3, "recirculation": False}

# Commands as the app names them (glossary). Buttons for these get a translated name.
COMMANDS = ("lock", "unlock", "climate_on", "climate_off", "quick_heat", "quick_cool", "defrost_windshield",
            "battery_preheat", "battery_preheat_off", "close_windows", "open_windows", "close_sunshade",
            "open_sunshade", "close_trunk", "open_trunk", "start_charging", "stop_charging",
            "release_charging_cable", "locate_vehicle", "refresh")

# Entity keys before 0.7.0 (German) -> current keys. unique_id is "<VIN>_<key>".
LEGACY_COMMANDS = {
    "verriegeln": "lock", "entriegeln": "unlock", "klima_an": "climate_on", "klima_aus": "climate_off",
    "schnell_heizen": "quick_heat", "schnell_kuehlen": "quick_cool", "scheibe_auftauen": "defrost_windshield",
    "batterie_vorwaermen": "battery_preheat", "batterie_vorwaermen_aus": "battery_preheat_off",
    "fenster_schliessen": "close_windows", "fenster_oeffnen": "open_windows",
    "sonnenrollo_schliessen": "close_sunshade", "sonnenrollo_oeffnen": "open_sunshade",
    "kofferraum_schliessen": "close_trunk", "kofferraum_oeffnen": "open_trunk", "laden_starten": "start_charging",
    "laden_stoppen": "stop_charging", "ladekabel_freigeben": "release_charging_cable",
    "fahrzeug_orten": "locate_vehicle", "aktualisieren": "refresh",
}
LEGACY_KEYS = {
    # sensors from vehicle values
    "akku": "battery", "reichweite": "range", "energie": "energy", "kilometerstand": "odometer",
    "geschwindigkeit": "speed", "gang": "gear", "ladezustand": "charge_state", "restladezeit": "charge_time_left",
    "ladeleistung": "charging_power", "batteriestrom": "battery_current", "batteriespannung": "battery_voltage",
    "akku_temperatur_min": "battery_temp_min", "aussen": "outside_temp", "soll_links": "target_temp_left",
    "geblaese": "fan_speed", "sonnenrollo": "sunshade",
    "fenster_vl": "window_fl", "fenster_vr": "window_fr", "fenster_hl": "window_rl", "fenster_hr": "window_rr",
    "reifen_vl": "tyre_fl", "reifen_vr": "tyre_fr", "reifen_hl": "tyre_rl", "reifen_hr": "tyre_rr",
    "fahrzeugzeit": "vehicle_time",
    # sensors from extras
    "heute_fahren_prozent": "today_driving_percent", "heute_klima_prozent": "today_climate_percent",
    "heute_sonstiges_prozent": "today_other_percent", "woche_fahren_prozent": "week_driving_percent",
    "woche_klima_prozent": "week_climate_percent", "woche_sonstiges_prozent": "week_other_percent",
    "gesamtenergie_kwh": "total_energy_kwh", "strecke_7_tage_km": "distance_7_days_km",
    "verbrauch_6_wochen": "consumption_6_weeks", "nachrichten_ungelesen": "messages_unread",
    "letzte_nachricht": "last_message",
    # gateway, derived and trip computer sensors
    "fahrtende_min": "trip_end_min", "kennzeichen": "plate", "reifendruck": "tyre_pressure",
    "ladeende_prognose": "charge_end_forecast", "noch_zu_laden": "energy_to_charge", "zustand": "state",
    "fahrt_km": "trip_distance", "fahrt_dauer": "trip_duration", "fahrt_energie": "trip_energy",
    "fahrt_verbrauch": "trip_consumption", "seit_laden_km": "since_charge_distance",
    "seit_laden_energie": "since_charge_energy", "seit_laden_verbrauch": "since_charge_consumption",
    "letzte_ladung": "last_charge", "ladungen": "charges", "fahrten": "trips",
    "freigaben_offen": "approvals_pending", "letzter_befehl": "last_command",
    # binary sensors
    "laedt": "charging", "ac_kabel": "ac_cable", "dc_kabel": "dc_cable", "bereit": "ready", "klima": "climate",
    "fahrertuer": "driver_door", "beifahrertuer": "passenger_door", "fond_links": "rear_left_door",
    "fond_rechts": "rear_right_door", "kofferraum": "trunk",
    # other platforms
    "schloss": "lock", "klima_steuerung": "climate_control", "bild": "image", "standort": "location",
    "klima_starten": "start_climate", "vorwahl_modus": "preset_mode", "vorwahl_temperatur": "preset_temperature",
    "vorwahl_geblaese": "preset_fan_speed", "vorwahl_umluft": "preset_recirculation",
    **{f"knopf_{old}": f"button_{new}" for old, new in LEGACY_COMMANDS.items()},
}
