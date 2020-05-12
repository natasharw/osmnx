################################################################################
# Module: pois.py
# Description: Download and plot points of interests (POIs) from OpenStreetMap
# License: MIT, see full license in LICENSE.txt
# Web: https://github.com/gboeing/osmnx
################################################################################

import geopandas as gpd
from shapely.geometry import MultiPolygon
from shapely.geometry import Point
from shapely.geometry import Polygon

from . import settings
from .core import bbox_from_point
from .core import gdf_from_place
from .downloader import overpass_request
from .geo_utils import geocode
from .utils import log


def create_poi_query(north, south, east, west, tags,
                     timeout=180, memory=None, custom_settings=None):
    """
    Create an overpass query string based on passed tags.

    Parameters
    ----------

    north : float
        Northernmost coordinate from bounding box of the search area.
    south : float
        Southernmost coordinate from bounding box of the search area.
    east : float
        Easternmost coordinate from bounding box of the search area.
    west : float
        Westernmost coordinate of the bounding box of the search area.
    tags : dict
        Dict of tags used for finding POIs from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
            tags = {
                'amenity':True,
                'landuse':['retail','commercial'],
                'highway':'bus_stop'}
        would return all amenities, `landuse=retail`, `landuse=commercial`,
        and `highway=bus_stop`.
    timeout : int
        Timeout for the API request.
    memory : int
        server memory allocation size for the query, in bytes. If none, server
        will use its default allocation size
    custom_settings : string
        custom settings to be used in the overpass query instead of defaults
    
    Returns
    -------
    query : string
    """

    # pass server memory allocation in bytes for the query to the API
    # if None, pass nothing so the server will use its default allocation size
    # otherwise, define the query's maxsize parameter value as whatever the
    # caller passed in
    if memory is None:
        maxsize = ''
    else:
        maxsize = '[maxsize:{}]'.format(memory)

    # use custom settings if delivered, otherwise just the default ones.
    if custom_settings:
        overpass_settings = custom_settings
    else:
        overpass_settings = settings.default_overpass_query_settings.format(timeout=timeout,
                                                                            maxsize=maxsize)


    # make sure every value in dict is bool, str, or list of str
    error_msg = 'tags must be a dict with values of bool, str, or list of str'    
    if not isinstance(tags, dict):
        raise TypeError(error_msg)

    tags_dict = {}
    for key, value in tags.items():
        
        if isinstance(value, bool):
            tags_dict[key] = value

        elif isinstance(value, str):
            tags_dict[key] = [value]
            
        elif isinstance(value, list):
            if not all(isinstance(s, str) for s in value):
                raise TypeError(error_msg)
            tags_dict[key] = value

        else:
            raise TypeError(error_msg)

    # convert the tags dict into a list of {tag:value} dicts
    tags_list = []
    for key, value in tags_dict.items():
        if isinstance(value, bool):
            tags_list.append({key: value})
        else:
            for value_item in value:
                tags_list.append({key: value_item})

    # create query bounding box
    bbox = '({s:.6f},{w:.6f},{n:.6f},{e:.6f})'.format(s=south, w=west, n=north, e=east)
    
    # add node/way/relation query components one at a time
    components = []
    for d in tags_list:
        for key, value in d.items():

            if isinstance(value, bool):
                # if bool (ie, True) just pass the key, no value
                tag_str = '["{key}"]{bbox};(._;>;);'.format(key=key, bbox=bbox)
            else:
                # otherwise, pass "key"="value"
                tag_str = '["{key}"="{value}"]{bbox};(._;>;);'.format(key=key, value=value, bbox=bbox)

            for kind in ['node', 'way', 'relation']:
                components.append('({kind}{tag_str});'.format(kind=kind, tag_str=tag_str))

    # finalize query and return
    components = ''.join(components)
    query = '{overpass_settings};({components});out;'
    query = query.format(overpass_settings=overpass_settings, components=components)
    
    return query


def osm_poi_download(tags, polygon=None,
                     north=None, south=None, east=None, west=None,
                     timeout=180, memory=None, custom_settings=None):
    """
    Get points of interests (POIs) from OpenStreetMap based on passed tags
    Note that if a polygon is passed-in, the query will be limited to its
    bounding box rather than to the shape of the polygon itself.

    Parameters
    ----------
    tags : dict
        Dict of tags used for finding POIs from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
            tags = {
                'amenity':True,
                'landuse':['retail','commercial'],
                'highway':'bus_stop'}
        would return all amenities, `landuse=retail`, `landuse=commercial`,
        and `highway=bus_stop`.
    polygon : shapely.geometry.Polygon
        Polygon that will be used to limit the POI search.
    north : float
        northern latitude of bounding box
    south : float
        southern latitude of bounding box
    east : float
        eastern longitude of bounding box
    west : float
        western longitude of bounding box
    timeout : int
        Timeout for the API request.
    memory : int
        server memory allocation size for the query, in bytes. If none, server
        will use its default allocation size
    custom_settings : string
        custom settings to be used in the overpass query instead of defaults

    Returns
    -------
    responses : dict
        JSON response with POIs from Overpass API server
    """

    # TODO: add functionality for subdividing search area geometry
    # TODO: add functionality for constraining query to poly rather than its bbox
    if polygon is not None:
        west, south, east, north = polygon.bounds
    elif not (north is None or south is None or east is None or west is None):
        pass
    else:
        raise ValueError('You must pass a polygon or north, south, east, and west')

    # get the POIs
    query = create_poi_query(north=north, south=south, east=east, west=west,
                             tags=tags,
                             timeout=timeout,
                             memory=memory,
                             custom_settings=custom_settings)
    responses = overpass_request(data={'data': query}, timeout=timeout)

    return responses


def parse_nodes_coords(osm_response):
    """
    Parse node coordinates from OSM response. Some nodes are standalone points
    of interest, others are vertices in polygonal (areal) POIs.

    Parameters
    ----------
    osm_response : string
        OSM response JSON string

    Returns
    -------
    coords : dict
        dict of node IDs and their lat, lon coordinates
    """

    coords = {}
    for result in osm_response['elements']:
        if 'type' in result and result['type'] == 'node':
            coords[result['id']] = {'lat': result['lat'],
                                    'lon': result['lon']}
    return coords


def parse_polygonal_poi(coords, response):
    """
    Parse areal POI way polygons from OSM node coords.

    Parameters
    ----------
    coords : dict
        dict of node IDs and their lat, lon coordinates
    response : string
        OSM response JSON string

    Returns
    -------
    dict of POIs containing each's nodes, polygon geometry, and osmid
    """

    if 'type' in response and response['type'] == 'way':
        nodes = response['nodes']
        try:
            polygon = Polygon([(coords[node]['lon'], coords[node]['lat']) for node in nodes])

            poi = {'nodes': nodes,
                   'geometry': polygon,
                   'osmid': response['id']}

            if 'tags' in response:
                for tag in response['tags']:
                    poi[tag] = response['tags'][tag]
            return poi

        except Exception:
            log('Polygon has invalid geometry: {}'.format(nodes))

    return None


def parse_osm_node(response):
    """
    Parse points from OSM nodes.

    Parameters
    ----------
    response : JSON
        Nodes from OSM response.

    Returns
    -------
    Dict of vertex IDs and their lat, lon coordinates.
    """

    try:
        point = Point(response['lon'], response['lat'])

        poi = {'osmid': response['id'],
               'geometry': point}

        if 'tags' in response:
            for tag in response['tags']:
                poi[tag] = response['tags'][tag]

    except Exception:
        log('Point has invalid geometry: {}'.format(response['id']))

    return poi


def invalid_multipoly_handler(gdf, relation, way_ids):
    """
    Handles invalid multipolygon geometries when there exists e.g. a feature without
    geometry (geometry == NaN)

    Parameters
    ----------

    gdf : gpd.GeoDataFrame
        GeoDataFrame with Polygon geometries that should be converted into a MultiPolygon object.
    relation : dict
        OSM 'relation' dictionary
    way_ids : list
        A list of 'way' ids that should be converted into a MultiPolygon object.

    Returns
    -------
    shapely.geometry.MultiPolygon
    """

    try:
        gdf_clean = gdf.dropna(subset=['geometry'])
        multipoly = MultiPolygon(list(gdf_clean['geometry']))
        return multipoly

    except Exception:
        msg = 'Invalid geometry at relation "{}". Way IDs of the invalid MultiPolygon: {}'
        log(msg.format(relation['id'], way_ids))
        return None


def parse_osm_relations(relations, osm_way_df):
    """
    Parses the osm relations (multipolygons) from osm
    ways and nodes. See more information about relations
    from OSM documentation: http://wiki.openstreetmap.org/wiki/Relation

    Parameters
    ----------
    relations : list
        OSM 'relation' items (dictionaries) in a list.
    osm_way_df : gpd.GeoDataFrame
        OSM 'way' features as a GeoDataFrame that contains all the
        'way' features that will constitute the multipolygon relations.

    Returns
    -------
    geopandas.GeoDataFrame
        A GeoDataFrame with MultiPolygon representations of the
        relations and the attributes associated with them.
    """

    gdf_relations = gpd.GeoDataFrame()

    # Iterate over relations and extract the items
    for relation in relations:
        try:
            if relation['tags']['type'] == 'multipolygon':
                # Parse member 'way' ids
                member_way_ids = [member['ref'] for member in relation['members'] if member['type'] == 'way']
                # Extract the ways
                member_ways = osm_way_df.reindex(member_way_ids)
                # Extract the nodes of those ways
                member_nodes = list(member_ways['nodes'].values)
                try:
                    # Create MultiPolygon from geometries (exclude NaNs)
                    multipoly = MultiPolygon(list(member_ways['geometry']))
                except Exception:
                    multipoly = invalid_multipoly_handler(gdf=member_ways, relation=relation, way_ids=member_way_ids)

                if multipoly:
                    # Create GeoDataFrame with the tags and the MultiPolygon and its 'ways' (ids), and the 'nodes' of those ways
                    geo = gpd.GeoDataFrame(relation['tags'], index=[relation['id']])
                    # Initialize columns (needed for .loc inserts)
                    geo = geo.assign(geometry=None, ways=None, nodes=None, element_type=None, osmid=None)
                    # Add attributes
                    geo.loc[relation['id'], 'geometry'] = multipoly
                    geo.loc[relation['id'], 'ways'] = member_way_ids
                    geo.loc[relation['id'], 'nodes'] = member_nodes
                    geo.loc[relation['id'], 'element_type'] = 'relation'
                    geo.loc[relation['id'], 'osmid'] = relation['id']

                    # Append to relation GeoDataFrame
                    gdf_relations = gdf_relations.append(geo, sort=False)
                    # Remove such 'ways' from 'osm_way_df' that are part of the 'relation'
                    osm_way_df = osm_way_df.drop(member_way_ids)
        except Exception as e:
            log('Could not parse OSM relation {}'.format(relation['id']))

    # Merge 'osm_way_df' and the 'gdf_relations'
    osm_way_df = osm_way_df.append(gdf_relations, sort=False)
    return osm_way_df


def create_poi_gdf(tags, polygon=None, north=None, south=None, east=None, west=None,
                   timeout=180, memory=None, custom_settings=None):
    """
    Create GeoDataFrame from POI json returned by Overpass API

    Parameters
    ----------
    tags : dict
        Dict of tags used for finding POIs from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
            tags = {
                'amenity':True,
                'landuse':['retail','commercial'],
                'highway':'bus_stop'}
        would return all amenities, `landuse=retail`, `landuse=commercial`,
        and `highway=bus_stop`.
    polygon : shapely Polygon or MultiPolygon
        geographic shape to fetch the POIs within
    north : float
        northern latitude of bounding box
    south : float
        southern latitude of bounding box
    east : float
        eastern longitude of bounding box
    west : float
        western longitude of bounding box
    timeout : int
        Timeout for the API request.
    memory : int
        server memory allocation size for the query, in bytes. If none, server
        will use its default allocation size
    custom_settings : string
        custom settings to be used in the overpass query instead of defaults

    Returns
    -------
    geopandas.GeoDataFrame
        POIs and their associated tags
    """

    responses = osm_poi_download(tags, polygon=polygon,
                                 north=north, south=south, east=east, west=west,
                                 timeout=timeout, memory=memory, custom_settings=custom_settings)

    # Parse coordinates from all the nodes in the response
    coords = parse_nodes_coords(responses)

    # POI nodes
    poi_nodes = {}

    # POI ways
    poi_ways = {}

    # A list of POI relations
    relations = []

    for result in responses['elements']:
        if result['type'] == 'node' and 'tags' in result:
            poi = parse_osm_node(response=result)
            # Add element_type
            poi['element_type'] = 'node'
            # Add to 'pois'
            poi_nodes[result['id']] = poi
        elif result['type'] == 'way':
            # Parse POI area Polygon
            poi_area = parse_polygonal_poi(coords=coords, response=result)
            if poi_area:
                # Add element_type
                poi_area['element_type'] = 'way'
                # Add to 'poi_ways'
                poi_ways[result['id']] = poi_area

        elif result['type'] == 'relation':
            # Add relation to a relation list (needs to be parsed after all nodes and ways have been parsed)
            relations.append(result)

    # Create GeoDataFrames
    gdf_nodes = gpd.GeoDataFrame(poi_nodes).T
    gdf_nodes.crs = settings.default_crs

    gdf_ways = gpd.GeoDataFrame(poi_ways).T
    gdf_ways.crs = settings.default_crs

    # Parse relations (MultiPolygons) from 'ways'
    gdf_ways = parse_osm_relations(relations=relations, osm_way_df=gdf_ways)

    # Combine GeoDataFrames
    gdf = gdf_nodes.append(gdf_ways, sort=False)

    if polygon:
        gdf = gdf.loc[gdf['geometry'].centroid.within(polygon)==True]

    return gdf


def pois_from_point(point, tags, distance=1000,
                    timeout=180, memory=None, custom_settings=None):
    """
    Get point of interests (POIs) within some distance north, south, east, and
    west of a lat-long point.

    Parameters
    ----------
    point : tuple
        a lat-long point
    tags : dict
        Dict of tags used for finding POIs from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
            tags = {
                'amenity':True,
                'landuse':['retail','commercial'],
                'highway':'bus_stop'}
        would return all amenities, `landuse=retail`, `landuse=commercial`,
        and `highway=bus_stop`.
    distance : numeric
        distance in meters
    timeout : int
        timeout for the API request
    memory : int
        server memory allocation size for the query, in bytes. If none, server
        will use its default allocation size
    custom_settings : string
        custom settings to be used in the overpass query instead of defaults

    Returns
    -------
    geopandas.GeoDataFrame
    """

    bbox = bbox_from_point(point=point, distance=distance)
    north, south, east, west = bbox
    return create_poi_gdf(tags=tags, north=north, south=south, east=east, west=west,
                          timeout=timeout, memory=memory, custom_settings=custom_settings)


def pois_from_address(address, tags, distance=1000,
                      timeout=180, memory=None, custom_settings=None):
    """
    Get point of interests (POIs) within some distance north, south, east, and
    west of an address.

    Parameters
    ----------
    address : string
        the address to geocode to a lat-long point
    tags : dict
        Dict of tags used for finding POIs from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
            tags = {
                'amenity':True,
                'landuse':['retail','commercial'],
                'highway':'bus_stop'}
        would return all amenities, `landuse=retail`, `landuse=commercial`,
        and `highway=bus_stop`.
    distance : numeric
        distance in meters
    timeout : int
        timeout for the API request
    memory : int
        server memory allocation size for the query, in bytes. If none, server
        will use its default allocation size
    custom_settings : string
        custom settings to be used in the overpass query instead of defaults

    Returns
    -------
    geopandas.GeoDataFrame
    """

    # geocode the address string to a (lat, lon) point
    point = geocode(query=address)

    # get POIs within distance of this point
    return pois_from_point(point=point, tags=tags, distance=distance,
                           timeout=timeout, memory=memory, custom_settings=custom_settings)


def pois_from_polygon(polygon, tags,
                      timeout=180, memory=None, custom_settings=None):
    """
    Get point of interests (POIs) within some polygon.

    Parameters
    ----------
    polygon : Polygon
        Polygon where the POIs are search from.
    tags : dict
        Dict of tags used for finding POIs from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
            tags = {
                'amenity':True,
                'landuse':['retail','commercial'],
                'highway':'bus_stop'}
        would return all amenities, `landuse=retail`, `landuse=commercial`,
        and `highway=bus_stop`.
    timeout : int
        timeout for the API request
    memory : int
        server memory allocation size for the query, in bytes. If none, server
        will use its default allocation size
    custom_settings : string
        custom settings to be used in the overpass query instead of defaults
        
    Returns
    -------
    geopandas.GeoDataFrame
    """

    return create_poi_gdf(tags=tags, polygon=polygon,
                          timeout=timeout, memory=memory, custom_settings=custom_settings)


def pois_from_place(place, tags, which_result=1,
                    timeout=180, memory=None, custom_settings=None):
    """
    Get points of interest (POIs) within the boundaries of some place.

    Parameters
    ----------
    place : string
        the query to geocode to get boundary polygon.
    tags : dict
        Dict of tags used for finding POIs from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
            tags = {
                'amenity':True,
                'landuse':['retail','commercial'],
                'highway':'bus_stop'}
        would return all amenities, `landuse=retail`, `landuse=commercial`,
        and `highway=bus_stop`.
    which_result : int
        max number of geocoding results to return and which to process
    timeout : int
        timeout for the API request
    memory : int
        server memory allocation size for the query, in bytes. If none, server
        will use its default allocation size
    custom_settings : string
        custom settings to be used in the overpass query instead of defaults

    Returns
    -------
    geopandas.GeoDataFrame
    """

    city = gdf_from_place(place, which_result=which_result)
    polygon = city['geometry'].iloc[0]
    return create_poi_gdf(tags=tags, polygon=polygon,
                          timeout=timeout, memory=memory, custom_settings=custom_settings)
