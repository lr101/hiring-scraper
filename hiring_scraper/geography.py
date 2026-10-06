"""Location primitives for bounded German company-discovery experiments."""
import math
import re
import unicodedata


GERMAN_STATES = (
    'Baden-Württemberg', 'Bayern', 'Berlin', 'Brandenburg', 'Bremen', 'Hamburg',
    'Hessen', 'Mecklenburg-Vorpommern', 'Niedersachsen', 'Nordrhein-Westfalen',
    'Rheinland-Pfalz', 'Saarland', 'Sachsen', 'Sachsen-Anhalt',
    'Schleswig-Holstein', 'Thüringen',
)


def _state_key(value):
    value = unicodedata.normalize('NFC', str(value)).strip().casefold()
    for source, replacement in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        value = value.replace(source, replacement)
    return re.sub(r'[^a-z0-9]', '', value)


_STATE_NAMES = {_state_key(name): name for name in GERMAN_STATES}
_STATE_NAMES['badenwurttemberg'] = 'Baden-Württemberg'


def normalize_german_state(value):
    """Return the canonical German state name, accepting common umlaut spellings."""
    try:
        return _STATE_NAMES[_state_key(value)]
    except KeyError as error:
        raise ValueError(f'Unknown German state: {value!r}') from error


def _coordinate(value, label, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f'{label} must be numeric') from error
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f'{label} must be between {low} and {high}')
    return number


def _format_coordinate(value):
    return format(float(value), '.8g')


def build_osm_radius_query(latitude, longitude, radius_m):
    """Build a bounded Overpass circle query for named business-like OSM objects."""
    lat = _coordinate(latitude, 'latitude', -90, 90)
    lon = _coordinate(longitude, 'longitude', -180, 180)
    if isinstance(radius_m, bool) or not isinstance(radius_m, (int, float)):
        raise ValueError('radius_m must be numeric')
    if not math.isfinite(radius_m) or not 0 < radius_m <= 200_000:
        raise ValueError('radius_m must be greater than 0 and at most 200000')
    radius_m = int(radius_m)
    location = f'around:{radius_m},{_format_coordinate(lat)},{_format_coordinate(lon)}'
    return f'''[out:json][timeout:60];
(
  nwr({location})[name][office];
  nwr({location})[name][craft];
  nwr({location})[name][industrial];
);
out center tags;'''


def build_osm_state_query(state, office_value=None):
    """Build an Overpass admin-area query, optionally narrowed to one office tag."""
    name = normalize_german_state(state)
    quoted_name = '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'
    area = f'area["boundary"="administrative"]["admin_level"="4"]["name"={quoted_name}]->.region;'
    if office_value:
        office_value = str(office_value).strip()
        if not office_value or len(office_value) > 80:
            raise ValueError('office_value must contain 1 to 80 characters')
        tag = '"' + office_value.replace('\\', '\\\\').replace('"', '\\"') + '"'
        selectors = f'nwr(area.region)[name]["office"={tag}];'
    else:
        selectors = '''(
  nwr(area.region)[name][office];
  nwr(area.region)[name][craft];
  nwr(area.region)[name][industrial];
);'''
    return f'''[out:json][timeout:90];
{area}
{selectors}
out center tags;'''


def osm_element_coordinates(element):
    """Read a point from an OSM node or the center emitted for a way/relation."""
    point = element if element.get('type') == 'node' else element.get('center')
    if not isinstance(point, dict) or 'lat' not in point or 'lon' not in point:
        return None
    try:
        latitude = _coordinate(point['lat'], 'latitude', -90, 90)
        longitude = _coordinate(point['lon'], 'longitude', -180, 180)
    except ValueError:
        return None
    return latitude, longitude


def resolve_nominatim_city(results, requested_name):
    """Turn one Nominatim German city result into a center and optional state label."""
    if not isinstance(results, list) or not results:
        raise ValueError(f'No German city match for {requested_name!r}')
    result = results[0]
    address = result.get('address', {})
    if not isinstance(address, dict) or address.get('country_code') != 'de':
        raise ValueError(f'Nominatim result for {requested_name!r} is not in Germany')
    city = next((address.get(key) for key in ('city', 'town', 'village', 'municipality', 'hamlet') if address.get(key)), requested_name)
    state = address.get('state')
    if state:
        try:
            state = normalize_german_state(state)
        except ValueError:
            pass
    return {
        'city': city,
        'state': state,
        'lat': _coordinate(result.get('lat'), 'latitude', -90, 90),
        'lon': _coordinate(result.get('lon'), 'longitude', -180, 180),
        'display_name': result.get('display_name', ''),
        'osm_type': result.get('osm_type'),
        'osm_id': result.get('osm_id'),
    }


def _point_in_ring(longitude, latitude, ring):
    if not isinstance(ring, list) or len(ring) < 4:
        return False
    inside = False
    for current, following in zip(ring, ring[1:] + ring[:1]):
        if not isinstance(current, (list, tuple)) or not isinstance(following, (list, tuple)) or len(current) < 2 or len(following) < 2:
            return False
        x1, y1 = float(current[0]), float(current[1])
        x2, y2 = float(following[0]), float(following[1])
        cross = (longitude - x1) * (y2 - y1) - (latitude - y1) * (x2 - x1)
        if abs(cross) < 1e-12 and min(x1, x2) <= longitude <= max(x1, x2) and min(y1, y2) <= latitude <= max(y1, y2):
            return True
        if (y1 > latitude) != (y2 > latitude):
            intersection = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < intersection:
                inside = not inside
    return inside


def _point_in_polygon(longitude, latitude, polygon):
    if not isinstance(polygon, list) or not polygon or not _point_in_ring(longitude, latitude, polygon[0]):
        return False
    return not any(_point_in_ring(longitude, latitude, hole) for hole in polygon[1:])


def state_for_point(latitude, longitude, geojson_features):
    """Return the German state polygon containing a WGS84 point, or None."""
    latitude = _coordinate(latitude, 'latitude', -90, 90)
    longitude = _coordinate(longitude, 'longitude', -180, 180)
    if not isinstance(geojson_features, list):
        return None
    for feature in geojson_features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get('properties', {})
        geometry = feature.get('geometry', {})
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        try:
            state = normalize_german_state(properties.get('gen'))
        except ValueError:
            continue
        coordinates = geometry.get('coordinates')
        if geometry.get('type') == 'Polygon':
            polygons = [coordinates]
        elif geometry.get('type') == 'MultiPolygon':
            polygons = coordinates
        else:
            continue
        if isinstance(polygons, list) and any(_point_in_polygon(longitude, latitude, polygon) for polygon in polygons):
            return state
    return None


def haversine_m(latitude_a, longitude_a, latitude_b, longitude_b):
    """Great-circle distance between WGS84 coordinate pairs, in metres."""
    lat_a = math.radians(_coordinate(latitude_a, 'latitude_a', -90, 90))
    lon_a = math.radians(_coordinate(longitude_a, 'longitude_a', -180, 180))
    lat_b = math.radians(_coordinate(latitude_b, 'latitude_b', -90, 90))
    lon_b = math.radians(_coordinate(longitude_b, 'longitude_b', -180, 180))
    dlat, dlon = lat_b - lat_a, lon_b - lon_a
    chord = math.sin(dlat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(dlon / 2) ** 2
    return 6_371_008.8 * 2 * math.asin(math.sqrt(min(1, chord)))
