"""One checkpointable national inventory step; no unbounded country query."""
from __future__ import annotations
import math
from copy import deepcopy
from hiring_scraper import location_discovery as source
from hiring_scraper.geography import GERMAN_STATES, build_osm_state_query


def advance_map_cursor(cursor, progress_callback=None):
    cursor = deepcopy(cursor or {})
    index = int(cursor.get('state_index', 0))
    if progress_callback:
        if progress_callback(f"Germany: {cursor.get('completed_sections',0)} map sections saved; state {min(16,index+1)} of 16; ranked checks continue") is False:
            raise source.LocationSearchCancelled('Country inventory lease was cancelled or lost')
    if index >= len(GERMAN_STATES):
        return {**cursor, 'done': True}, [], None
    state = GERMAN_STATES[index]
    transport = source.urlopen
    if 'pending' not in cursor:
        south, west, north, east = source.state_map_bounds(state, transport)
        cursor['pending'] = [[south+y*.5, west+x*.5, min(north,south+(y+1)*.5), min(east,west+(x+1)*.5),0]
                             for y in range(math.ceil((north-south)/.5))
                             for x in range(math.ceil((east-west)/.5))]
        cursor['state_index'] = index
        cursor.setdefault('completed_sections',0)
        return cursor, [], state
    pending = cursor['pending']
    if not pending:
        cursor.pop('pending')
        cursor['state_index'] = index+1
        cursor['done'] = index+1 >= len(GERMAN_STATES)
        return cursor, [], state
    box = pending.pop(0)
    depth = box[4]
    try:
        payload = source._fetch_overpass(build_osm_state_query(state,bbox=box[:4]),transport,
                                         split_on_timeout=depth < 2)
    except source.OverpassIncompleteError:
        if depth >= 2:
            raise
        south,west,north,east = box[:4]
        midlat,midlon = (south+north)/2,(west+east)/2
        pending[0:0] = [[s,w,n,e,depth+1] for s,w,n,e in (
            (south,west,midlat,midlon),(south,midlon,midlat,east),
            (midlat,west,north,midlon),(midlat,midlon,north,east))]
        return cursor, [], state
    rows = source.resolve_location_candidates(payload,0,0,None)
    cursor['completed_sections'] += 1
    if not pending:
        cursor.pop('pending')
        cursor['state_index'] = index+1
        cursor['done'] = index+1 >= len(GERMAN_STATES)
    return cursor, rows, state
