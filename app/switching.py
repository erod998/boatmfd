"""Digital switching: a panel of named on/off circuits (Garmin's own example list is interior
lights, navigation lights, and livewell circuits), the way a boat wired to a digital switching
module (a CZone/Empirbus-style relay box on NMEA 2000) lets the chartplotter turn accessories on
and off instead of a dash full of physical switches. There is no real switching module here, so
this is a simulator in the same spirit as SimulatedGPS/SimulatedMedia elsewhere in this app --
the circuits just remember their own on/off state.
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CIRCUITS = [
    {"id": "nav_lights", "name": "Navigation Lights"},
    {"id": "anchor_light", "name": "Anchor Light"},
    {"id": "cabin_lights", "name": "Cabin Lights"},
    {"id": "livewell", "name": "Livewell"},
    {"id": "bilge_pump", "name": "Bilge Pump"},
    {"id": "accessory_1", "name": "Accessory 1"},
    {"id": "accessory_2", "name": "Accessory 2"},
    {"id": "horn", "name": "Horn"},
]


@dataclass
class Circuit:
    id: str
    name: str
    on: bool = False


class SwitchingPanel:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._circuits: list = self._load()

    def _load(self):
        if self.storage_path.exists():
            try:
                raw = json.loads(self.storage_path.read_text())
                saved = {c["id"]: c.get("on", False) for c in raw if isinstance(c, dict) and "id" in c}
                return [Circuit(id=d["id"], name=d["name"], on=saved.get(d["id"], False)) for d in DEFAULT_CIRCUITS]
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        return [Circuit(**d) for d in DEFAULT_CIRCUITS]

    def _save(self):
        self.storage_path.write_text(json.dumps([asdict(c) for c in self._circuits], indent=2))

    def list(self):
        return [asdict(c) for c in self._circuits]

    def set(self, circuit_id, on):
        circuit = next((c for c in self._circuits if c.id == circuit_id), None)
        if circuit is None:
            raise ValueError("unknown circuit '%s'" % circuit_id)
        circuit.on = bool(on)
        self._save()
        return circuit
