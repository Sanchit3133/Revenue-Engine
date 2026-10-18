"""Find random city-area business candidates through Overture Maps Places."""
from __future__ import annotations
import json, math, random, shutil, subprocess, tempfile, zipfile, csv, io
from dataclasses import dataclass
from pathlib import Path
import requests

OVERTURE_CLI = "overturemaps"
GEONAMES_CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
OVERTURE_ATTRIBUTION = "Overture Maps Foundation, overturemaps.org"

@dataclass(frozen=True)
class City:
    name: str
    country_code: str
    latitude: float
    longitude: float
    population: int

def parse_cities_zip(payload: bytes, country_code: str = "IN", min_population: int = 40000) -> list[City]:
    """Parse GeoNames' cities15000 archive. GeoNames requires CC BY 4.0 attribution."""
    found = []
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.endswith(".txt")]
            if not names:
                return []
            with archive.open(names[0]) as binary:
                reader = csv.reader(io.TextIOWrapper(binary, encoding="utf-8", errors="replace"), delimiter="\t")
                for row in reader:
                    if len(row) < 15 or row[6] != "P" or row[8].upper() != country_code.upper():
                        continue
                    try:
                        population, lat, lon = int(row[14] or 0), float(row[4]), float(row[5])
                    except (ValueError, IndexError):
                        continue
                    if row[1].strip() and population >= min_population:
                        found.append(City(row[1].strip(), row[8].upper(), lat, lon, population))
    except (OSError, zipfile.BadZipFile):
        return []
    return found

def random_city(country_code: str = "IN", min_population: int = 40000, *, rng=None) -> City:
    response = requests.get(GEONAMES_CITIES_URL, timeout=30)
    response.raise_for_status()
    cities = parse_cities_zip(response.content, country_code, min_population)
    if not cities:
        raise RuntimeError("No city records were available; discovery stopped safely.")
    return (rng or random.SystemRandom()).choice(cities)

def download_city_places(city: City, *, timeout: int = 240) -> list[dict]:
    """Download only a small bounded place dataset near a randomized city center."""
    if not shutil.which(OVERTURE_CLI):
        raise RuntimeError("The Overture Maps Python client is not installed.")
    dlat = 0.025
    dlon = min(0.08, dlat / max(math.cos(math.radians(city.latitude)), 0.25))
    bbox = (city.longitude-dlon, city.latitude-dlat, city.longitude+dlon, city.latitude+dlat)
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "places.geojson"
        command = [OVERTURE_CLI, "download", f"--bbox={bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}", "-f", "geojson", "--type=place", "-o", str(output)]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
            if output.stat().st_size > 40000000:
                raise RuntimeError("The place result exceeded the configured size limit.")
            payload = json.loads(output.read_text(encoding="utf-8"))
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise RuntimeError("Overture Maps could not provide the bounded city data.") from exc
    features = payload.get("features", []) if isinstance(payload, dict) else payload
    return [item for item in features if isinstance(item, dict)] if isinstance(features, list) else []
