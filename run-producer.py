#!/usr/bin/python
# -*- coding: UTF-8

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/. */

# Authors:
# Michael Berg-Mohnicke <michael.berg@zalf.de>
#
# Maintainers:
# Currently maintained by the authors.
#
# This file has been created at the Institute of
# Landscape Systems Analysis at the ZALF.
# Copyright (C: Leibniz Centre for Agricultural Landscape Research (ZALF)

import copy
import json
import numpy as np
import os
from pyproj import CRS, Transformer
import sqlite3
import sys
import time
import zmq
import geopandas as gpd
import rasterio
from rasterio import features

import monica_io3
import cz_soil_io3
import monica_run_lib as Mrunlib

PATHS = {
    # adjust the local path to your environment
    "re-local-remote": {
        # "include-file-base-path": "/home/berg/GitHub/monica-parameters/", # path to monica-parameters
        "path-to-climate-dir": "F:/",
        # mounted path to archive or hard drive with climate data
        "monica-path-to-climate-dir": "F:/",
        # mounted path to archive accessable by monica executable
        "path-to-data-dir": "./data/",  # mounted path to archive or hard drive with data
        "path-debug-write-folder": "./debug-out/",
    },
    "ow-local-remote": {
        # "include-file-base-path": "/home/berg/GitHub/monica-parameters/", # path to monica-parameters
        "path-to-climate-dir": "/beegfs/common/data/climate/",
        # mounted path to archive or hard drive with climate data
        "monica-path-to-climate-dir": "/monica_data/climate-data/",
        # mounted path to archive accessable by monica executable
        "path-to-data-dir": "./data/",  # mounted path to archive or hard drive with data
        "path-debug-write-folder": "./debug-out/",
    },
    "mbm-local-remote": {
        # "include-file-base-path": "/home/berg/GitHub/monica-parameters/", # path to monica-parameters
        "path-to-climate-dir": "/run/user/1000/gvfs/sftp:host=login01.cluster.zalf.de,user=rpm/beegfs/common/data/climate/",
        # mounted path to archive or hard drive with climate data
        "monica-path-to-climate-dir": "/monica_data/climate-data/",
        # mounted path to archive accessable by monica executable
        "path-to-data-dir": "./data/",  # mounted path to archive or hard drive with data
        "path-debug-write-folder": "./debug-out/",
    },
    "mbm-local-local": {
        # "include-file-base-path": "/home/berg/GitHub/monica-parameters/", # path to monica-parameters
        "path-to-climate-dir": "/run/user/1000/gvfs/sftp:host=login01.cluster.zalf.de,user=rpm/beegfs/common/data/climate/",
        # mounted path to archive or hard drive with climate data
        "monica-path-to-climate-dir": "/run/user/1000/gvfs/sftp:host=login01.cluster.zalf.de,user=rpm/beegfs/common/data/climate/",
        # mounted path to archive accessable by monica executable
        "path-to-data-dir": "./data/",  # mounted path to archive or hard drive with data
        "path-debug-write-folder": "./debug-out/",
    },

    "remoteProducer-remoteMonica": {
        # "include-file-base-path": "/monica-parameters/", # path to monica-parameters
        "path-to-climate-dir": "/data/",  # mounted path to archive or hard drive with climate data
        "monica-path-to-climate-dir": "/monica_data/climate-data/",
        # mounted path to archive accessable by monica executable
        "path-to-data-dir": "./data/",  # mounted path to archive or hard drive with data
        "path-debug-write-folder": "/out/debug-out/",
    }
}

DATA_SOIL_DB = "cz/cz_soil.sqlite"
DATA_GRID_HEIGHT = "cz/cz_dem_1000_32633_etrs89-utm33n.asc"
DATA_GRID_SLOPE = "cz/cz_slope_1000_32633_etrs89-utm33n.asc"
DATA_GRID_SOIL = "cz/cz_soil_1000_32633_etrs89-utm33n.asc"
DATA_GRID_CROPS = "cz/cz_crop-cw_1000_32633_etrs89-utm33n.asc"  # crop map for common wheat
TEMPLATE_PATH_LATLON = "data/cz_latlon-to-rowcol.json"
TEMPLATE_PATH_CLIMATE_CSV = "{gcm}/{rcm}/{scenario}/{ensmem}/{version}/row-{crow}/col-{ccol}.csv.gz"

# Additional data for masking the regions
NUTS3_REGIONS = "data/CZ/cz_nuts3_32633.shp"

gdf = gpd.read_file(NUTS3_REGIONS)

DEBUG_DONOT_SEND = False
DEBUG_WRITE = False
DEBUG_ROWS = 10
DEBUG_WRITE_FOLDER = "./debug_out"
DEBUG_WRITE_CLIMATE = False


## Add an argument in the run_producer function and make a loop with changing of the value of the additional parameter (sensitivity analysis)
## Make a list of the parameter values first

# commandline parameters e.g "server=localhost port=6666 shared_id=2"
def run_producer(server={"server": None, "port": None}, shared_id=None):
    context = zmq.Context()
    socket = context.socket(zmq.PUSH)  # pylint: disable=no-member
    # config_and_no_data_socket = context.socket(zmq.PUSH)

    config = {
        # "mode": "mbm-local-remote",
        "mode": "re-local-remote",
        "server-port": server["port"] if server["port"] else "6667",
        "server": server["server"] if server["server"] else "login01.cluster.zalf.de",
        "start-row": "0",
        "end-row": "-1",
        "path_to_dem_grid": "",
        "sim.json": "sim.json",
        "crop.json": "crop.json",
        "site.json": "site.json",
        "setups-file": "sim_setups.csv",
        "run-setups": "[1]",
        "shared_id": shared_id
    }

    # read commandline args only if script is invoked directly from commandline
    if len(sys.argv) > 1 and __name__ == "__main__":
        for arg in sys.argv[1:]:
            k, v = arg.split("=")
            if k in config:
                config[k] = v

    print("config:", config)

    # select paths
    paths = PATHS[config["mode"]]
    # open soil db connection
    soil_db_con = sqlite3.connect(paths["path-to-data-dir"] + DATA_SOIL_DB)
    # soil_db_con = cas_sq3.connect(paths["path-to-data-dir"] + DATA_SOIL_DB) #CAS.
    # connect to monica proxy (if local, it will try to connect to a locally started monica)
    socket.connect("tcp://" + config["server"] + ":" + str(config["server-port"]))

    # read setup from csv file
    setups = Mrunlib.read_sim_setups(config["setups-file"])
    rs_ranges = config["run-setups"][1:-1].split(",")
    run_setups = []
    for rsr in rs_ranges:
        rs_r = rsr.split("-")
        if 1 < len(rs_r) <= 2:
            run_setups.extend(range(int(rs_r[0]), int(rs_r[1])+1))
        elif len(rs_r) == 1:
            run_setups.append(int(rs_r[0]))
    #run_setups = json.loads(config["run-setups"])
    print("read sim setups: ", config["setups-file"])

    # transforms geospatial coordinates from one coordinate reference system to another
    # transform wgs84 into gk5
    soil_crs_to_x_transformers = {}
    wgs84_crs = CRS.from_epsg(4326)
    utm32_crs = CRS.from_epsg(32633)
    # transformers[wgs84] = Transformer.from_crs(wgs84_crs, gk5_crs, always_xy=True)

    # Load grids
    ## note numpy is able to load from a compressed file, ending with .gz or .bz2

    # soil data
    path_to_soil_grid = paths["path-to-data-dir"] + DATA_GRID_SOIL
    soil_epsg_code = int(path_to_soil_grid.split("/")[-1].split("_")[3])
    soil_crs = CRS.from_epsg(soil_epsg_code)
    if wgs84_crs not in soil_crs_to_x_transformers:
        soil_crs_to_x_transformers[wgs84_crs] = Transformer.from_crs(soil_crs, wgs84_crs)
    soil_metadata, _ = Mrunlib.read_header(path_to_soil_grid)
    soil_grid = np.loadtxt(path_to_soil_grid, dtype=int, skiprows=6)
    print("read: ", path_to_soil_grid)

    # height data
    path_to_dem_grid = paths["path-to-data-dir"] + DATA_GRID_HEIGHT
    dem_epsg_code = int(path_to_dem_grid.split("/")[-1].split("_")[3])
    dem_crs = CRS.from_epsg(dem_epsg_code)
    if dem_crs not in soil_crs_to_x_transformers:
        soil_crs_to_x_transformers[dem_crs] = Transformer.from_crs(soil_crs, dem_crs)
    dem_metadata, _ = Mrunlib.read_header(path_to_dem_grid)
    dem_grid = np.loadtxt(path_to_dem_grid, dtype=float, skiprows=6)
    dem_interpolate = Mrunlib.create_ascii_grid_interpolator(dem_grid, dem_metadata)
    print("read: ", path_to_dem_grid)

    # slope data
    path_to_slope_grid = paths["path-to-data-dir"] + DATA_GRID_SLOPE
    slope_epsg_code = int(path_to_slope_grid.split("/")[-1].split("_")[3])
    slope_crs = CRS.from_epsg(slope_epsg_code)
    if slope_crs not in soil_crs_to_x_transformers:
        soil_crs_to_x_transformers[slope_crs] = Transformer.from_crs(soil_crs, slope_crs)
    slope_metadata, _ = Mrunlib.read_header(path_to_slope_grid)
    slope_grid = np.loadtxt(path_to_slope_grid, dtype=float, skiprows=6)
    slope_interpolate = Mrunlib.create_ascii_grid_interpolator(slope_grid, slope_metadata)
    print("read: ", path_to_slope_grid)

    # crop mask data
    path_to_crop_grid = paths["path-to-data-dir"] + DATA_GRID_CROPS
    crop_epsg_code = int(path_to_crop_grid.split("/")[-1].split("_")[3])
    crop_crs = CRS.from_epsg(crop_epsg_code)
    if crop_crs not in soil_crs_to_x_transformers:
        soil_crs_to_x_transformers[crop_crs] = Transformer.from_crs(soil_crs, crop_crs)
    crop_meta, _ = Mrunlib.read_header(path_to_crop_grid)
    crop_grid = np.loadtxt(path_to_crop_grid, dtype=int, skiprows=6)
    crop_interpolate = Mrunlib.create_ascii_grid_interpolator(crop_grid, crop_meta)
    print("read: ", path_to_crop_grid)

    # Create the function for the mask. This function will later use the additional column in a setup file!
    def create_mask_from_shapefile(NUTS3_REGIONS, region_name, path_to_soil_grid):
        regions_df = gpd.read_file(NUTS3_REGIONS)
        region = regions_df[regions_df["NUTS_NAME"] == region_name]

        # This is needed to read the transformation data correctly from the file. With the original opening it does not work
        with rasterio.open(path_to_soil_grid) as dataset:
            soil_grid = dataset.read(1)
            transform = dataset.transform

        rows, cols = soil_grid.shape
        mask = rasterio.features.geometry_mask([region.geometry.values[0]], out_shape=(rows, cols), transform=transform,
                                               invert=True)

        return mask

    sent_env_count = 0
    start_time = time.perf_counter()

    listOfClimateFiles = set()

    # run calculations for each setup
    for _, setup_id in enumerate(run_setups):

        if setup_id not in setups:
            continue
        start_setup_time = time.perf_counter()

        setup = setups[setup_id]
        gcm = setup["gcm"]
        rcm = setup["rcm"]
        scenario = setup["scenario"]
        ensmem = setup["ensmem"]
        version = setup["version"]
        crop_id = setup["crop-id"]
        region_name = setup["region_name"]

        ## extract crop_id from crop-id name that has possible an extenstion
        crop_id_short = crop_id.split('_')[0]

        if region_name and len(region_name) > 0:
            # Create the soil mask for the specific region
            path_to_soil_grid_ow = paths["path-to-data-dir"] + DATA_GRID_SOIL
            mask = create_mask_from_shapefile(NUTS3_REGIONS, region_name, path_to_soil_grid_ow)

            # Apply the soil mask to the soil grid
            soil_grid_copy = soil_grid.copy()
            soil_grid[mask == False] = -8888
            soil_grid[soil_grid_copy == -9999] = -9999

        cdict = {}
        # path to latlon-to-rowcol.json
        # path = TEMPLATE_PATH_LATLON.format(path_to_climate_dir=paths["path-to-climate-dir"] + setup["climate_path_to_latlon_file"] + "/")
        path = TEMPLATE_PATH_LATLON.format(
            path_to_climate_dir=paths["path-to-climate-dir"] + setup["climate_path_to_latlon_file"] + "/")
        climate_data_source_crs = CRS.from_epsg(3035)  # CRS of JSON mapping
        climate_data_target_crs = CRS.from_epsg(32633)  # CRS of simulation
        climate_data_interpolator = Mrunlib.create_climate_geoGrid_interpolator_from_json_file(path,
                                                                                               climate_data_source_crs,
                                                                                               climate_data_target_crs,
                                                                                               cdict)
        print("created climate_data to gk5 interpolator: ", path)

        # read template sim.json
        with open(setup.get("sim.json", config["sim.json"])) as _:
            sim_json = json.load(_)
        # change start and end date according to setup
        if setup["start_date"]:
            sim_json["climate.csv-options"]["start-date"] = str(setup["start_date"])
        if setup["end_date"]:
            sim_json["climate.csv-options"]["end-date"] = str(setup["end_date"])
            # sim_json["include-file-base-path"] = paths["include-file-base-path"]

        # read template site.json
        with open(setup.get("site.json", config["site.json"])) as _:
            site_json = json.load(_)

        if len(scenario) > 0 and scenario[:3].lower() == "rcp":
            site_json["EnvironmentParameters"]["rcp"] = scenario

        # read template crop.json
        with open(setup.get("crop.json", config["crop.json"])) as _:
            crop_json = json.load(_)

        crop_json["CropParameters"]["__enable_vernalisation_factor_fix__"] = setup[
            "use_vernalisation_fix"] if "use_vernalisation_fix" in setup else False

        # set the current crop used for this run id
        crop_json["cropRotation"][2] = crop_id

        # create environment template from json templates
        env_template = monica_io3.create_env_json_from_json_config({
            "crop": crop_json,
            "site": site_json,
            "sim": sim_json,
            "climate": ""
        })

        # set shared id in template
        if config["shared_id"]:
            env_template["sharedId"] = config["shared_id"]

        scols = int(soil_metadata["ncols"])
        srows = int(soil_metadata["nrows"])
        scellsize = int(soil_metadata["cellsize"])
        xllcorner = int(soil_metadata["xllcorner"])
        yllcorner = int(soil_metadata["yllcorner"])
        nodata_value = int(soil_metadata["nodata_value"])

        # unknown_soil_ids = set()
        soil_id_cache = {}
        print("All Rows x Cols: " + str(srows) + "x" + str(scols))
        # cs__ = open("coord_mapping_etrs89-utm32n_to_wgs84-latlon.csv", "w")
        # cs__.write("row,col,center_25832_etrs89-utm32n_r,center_25832_etrs89-utm32n_h,center_lat,center_lon\n")

        # for sensitivity analysis mode
        is_sensitivity_analysis = False
        orig_params = None
        if setup["species_param_name"]:
            if not orig_params:
                orig_params = copy.deepcopy(env_template["cropRotation"][0]["worksteps"][0]["crop"]["cropParams"]["species"])
        elif setup["cultivar_param_name"]:
            if not orig_params:
                orig_params = copy.deepcopy(env_template["cropRotation"][0]["worksteps"][0]["crop"]["cropParams"]["cultivar"])

        for srow in range(0, srows):
            print(srow, end=", ")

            if srow < int(config["start-row"]):
                continue
            elif int(config["end-row"]) > 0 and srow > int(config["end-row"]):
                break

            for scol in range(0, scols):
                soil_id = int(soil_grid[srow, scol])

                # Check if soil data is missing
                if soil_id == nodata_value:
                    continue

                # get coordinate of closest climate element of real soil-cell
                sh = yllcorner + (scellsize / 2) + (srows - srow - 1) * scellsize
                sr = xllcorner + (scellsize / 2) + scol * scellsize

                # Get climate data coordinate
                # inter = crow/ccol encoded into integer
                crow, ccol = climate_data_interpolator(sr, sh)

                # Transform to WGS84 for lat/lon
                wgs84_crs = CRS.from_epsg(4326)  # WGS84 CRS for lat/lon
                transformer_to_wgs84 = Transformer.from_crs(climate_data_target_crs, wgs84_crs, always_xy=True)
                clon, clat = transformer_to_wgs84.transform(sr, sh)

                # OW: clim4cast sensitivity analysis
                p_value = p_name = params = None
                if setup["species_param_name"]:
                    params = env_template["cropRotation"][0]["worksteps"][0]["crop"]["cropParams"]["species"]
                    p_name = setup["species_param_name"]
                elif setup["cultivar_param_name"]:
                    params = env_template["cropRotation"][0]["worksteps"][0]["crop"]["cropParams"]["cultivar"]
                    p_name = setup["cultivar_param_name"]
                if setup["coeff"] and p_name and params and orig_params:
                    # Case 3: List with a coefficient
                    coefficient = float(setup["coeff"])
                    is_sensitivity_analysis = True
                    if type(orig_params[p_name]) is list and len(orig_params[p_name]) > 0:
                        if type(orig_params[p_name][0]) is list:
                            params[p_name][0] = list([float(val) * coefficient for val in orig_params[p_name][0]])
                        else:
                            params[p_name] = list([float(val) * coefficient for val in orig_params[p_name]])
                elif setup["param_value"]:
                    # Case 1: Single value or Case 2: List without coefficient
                    p_value = float(setup["param_value"])
                    is_sensitivity_analysis = True
                    if params and p_name:
                        if setup["param_index_in_array"]:
                            i = int(setup["param_index_in_array"])
                            if type(params[p_name][0]) is list:
                                params[p_name][0][i] = p_value
                            else:
                                params[p_name][i] = p_value
                        else:
                            params[p_name] = p_value

                # Check if crop pixel is valid
                crop_grid_id = int(crop_grid[srow, scol])
                # print(crop_grid_id)
                if crop_grid_id != 1 or soil_id == -9999:
                    # print("row/col:", srow, "/", scol, "is not a crop pixel.")
                    env_template["customId"] = {
                        "setup_id": setup_id,
                        "srow": srow, "scol": scol,
                        "crow": int(crow), "ccol": int(ccol),
                        "soil_id": soil_id,
                        "env_id": sent_env_count,
                        "nodata": True,
                        "is_sensitivity_analysis": is_sensitivity_analysis,
                    }
                    if not is_sensitivity_analysis and not DEBUG_DONOT_SEND:
                        socket.send_json(env_template)
                        # print("sent nodata env ", sent_env_count, " customId: ", env_template["customId"])
                        sent_env_count += 1
                    continue

                tcoords = {}

                """
                lon, lat = soil_crs_to_x_transformers[wgs84_crs].transform(sr, sh)
                try:
                    int(lon)
                    int(lat)
                except Exception as e:
                    lon, lat = wgs84_ip(sr, sh)

                cs__.write(str(srow) + "," + str(scol) + "," + str(sr) + "," + str(sh) + "," + str(lat) + "," + str(lon) + "\n")
                continue
                """

                if soil_id in soil_id_cache:
                    soil_profile = soil_id_cache[soil_id]
                else:
                    soil_profile = cz_soil_io3.soil_parameters(soil_db_con, soil_id)
                    soil_id_cache[soil_id] = soil_profile

                worksteps = env_template["cropRotation"][0]["worksteps"]

                if len(soil_profile) == 0:
                    # print("row/col:", srow, "/", scol, "has unknown soil_id:", soil_id)
                    # unknown_soil_ids.add(soil_id)

                    env_template["customId"] = {
                        "setup_id": setup_id,
                        "srow": srow, "scol": scol,
                        "crow": int(crow), "ccol": int(ccol),
                        "soil_id": soil_id,
                        "env_id": sent_env_count,
                        "nodata": True,
                        "is_sensitivity_analysis": is_sensitivity_analysis
                    }
                    if not is_sensitivity_analysis and not DEBUG_DONOT_SEND:
                        socket.send_json(env_template)
                        # print("sent nodata env ", sent_env_count, " customId: ", env_template["customId"])
                        sent_env_count += 1
                    continue

                if dem_crs not in tcoords:
                    tcoords[dem_crs] = soil_crs_to_x_transformers[dem_crs].transform(sr, sh)
                demr, demh = tcoords[dem_crs]
                height_nn = dem_interpolate(demr, demh)

                if slope_crs not in tcoords:
                    tcoords[slope_crs] = soil_crs_to_x_transformers[slope_crs].transform(sr, sh)
                slr, slh = tcoords[slope_crs]
                slope = slope_interpolate(slr, slh)

                env_template["params"]["userCropParameters"]["__enable_T_response_leaf_expansion__"] = setup[
                    "LeafExtensionModifier"]

                # print("soil:", soil_profile)
                env_template["params"]["siteParameters"]["SoilProfileParameters"] = soil_profile

                # setting groundwater level
                if setup["groundwater-level"]:
                    groundwaterlevel = 20
                    layer_depth = 0
                    for layer in soil_profile:
                        if layer.get("is_in_groundwater", False):
                            groundwaterlevel = layer_depth
                            # print("setting groundwaterlevel of soil_id:", str(soil_id), "to", groundwaterlevel, "m")
                            break
                        layer_depth += Mrunlib.get_value(layer["Thickness"])
                    env_template["params"]["userEnvironmentParameters"]["MinGroundwaterDepthMonth"] = 3
                    env_template["params"]["userEnvironmentParameters"]["MinGroundwaterDepth"] = [
                        max(0, groundwaterlevel - 0.2), "m"]
                    env_template["params"]["userEnvironmentParameters"]["MaxGroundwaterDepth"] = [
                        groundwaterlevel + 0.2, "m"]

                # setting impenetrable layer
                if setup["impenetrable-layer"]:
                    impenetrable_layer_depth = Mrunlib.get_value(
                        env_template["params"]["userEnvironmentParameters"]["LeachingDepth"])
                    layer_depth = 0
                    for layer in soil_profile:
                        if layer.get("is_impenetrable", False):
                            impenetrable_layer_depth = layer_depth
                            # print("setting leaching depth of soil_id:", str(soil_id), "to", impenetrable_layer_depth, "m")
                            break
                        layer_depth += Mrunlib.get_value(layer["Thickness"])
                    env_template["params"]["userEnvironmentParameters"]["LeachingDepth"] = [impenetrable_layer_depth,
                                                                                            "m"]
                    env_template["params"]["siteParameters"]["ImpenetrableLayerDepth"] = [impenetrable_layer_depth, "m"]

                if setup["elevation"]:
                    env_template["params"]["siteParameters"]["heightNN"] = float(height_nn)

                if setup["slope"]:
                    env_template["params"]["siteParameters"]["slope"] = slope / 100.0

                if setup["latitude"]:
                    # clat, _ = cdict[(crow, ccol)]
                    env_template["params"]["siteParameters"]["Latitude"] = clat

                if setup["CO2"]:
                    env_template["params"]["userEnvironmentParameters"]["AtmosphericCO2"] = float(setup["CO2"])

                if setup["O3"]:
                    env_template["params"]["userEnvironmentParameters"]["AtmosphericO3"] = float(setup["O3"])

                if setup["FieldConditionModifier"]:
                    env_template["cropRotation"][0]["worksteps"][0]["crop"]["cropParams"]["species"][
                        "FieldConditionModifier"] = float(setup["FieldConditionModifier"])

                if setup["StageTemperatureSum"]:
                    stage_ts = setup["StageTemperatureSum"].split('_')
                    stage_ts = [int(temp_sum) for temp_sum in stage_ts]
                    orig_stage_ts = env_template["cropRotation"][0]["worksteps"][0]["crop"]["cropParams"]["cultivar"][
                        "StageTemperatureSum"][0]
                    if len(stage_ts) != len(orig_stage_ts):
                        stage_ts = orig_stage_ts
                        print('The provided StageTemperatureSum array is not '
                              'sufficiently long. Falling back to original StageTemperatureSum')

                    env_template["cropRotation"][0]["worksteps"][0]["crop"]["cropParams"]["cultivar"][
                        "StageTemperatureSum"][0] = stage_ts

                env_template["params"]["simulationParameters"]["UseNMinMineralFertilisingMethod"] = setup[
                    "fertilization"]

                env_template["params"]["simulationParameters"]["NitrogenResponseOn"] = setup["NitrogenResponseOn"]
                env_template["params"]["simulationParameters"]["WaterDeficitResponseOn"] = setup[
                    "WaterDeficitResponseOn"]
                env_template["params"]["simulationParameters"]["EmergenceMoistureControlOn"] = setup[
                    "EmergenceMoistureControlOn"]
                env_template["params"]["simulationParameters"]["EmergenceFloodingControlOn"] = setup[
                    "EmergenceFloodingControlOn"]

                env_template["csvViaHeaderOptions"] = sim_json["climate.csv-options"]

                crow = int(crow)
                ccol = int(ccol)

                subpath_to_csv = TEMPLATE_PATH_CLIMATE_CSV.format(gcm=gcm, rcm=rcm, scenario=scenario, ensmem=ensmem,
                                                                  version=version, crow=str(int(crow)),
                                                                  ccol=str(int(ccol)))
                for _ in range(4):
                    subpath_to_csv = subpath_to_csv.replace("//", "/")
                # env_template["pathToClimateCSV"] = [
                #     paths["monica-path-to-climate-dir"] + setup["climate_path_to_csvs"] + subpath_to_csv]

                climate_csv_path = paths["monica-path-to-climate-dir"] + setup["climate_path_to_csvs"] + subpath_to_csv

                # Check if the climate CSV file exists
                if not os.path.isfile(climate_csv_path):
                    print(f"Climate CSV file does not exist for row {crow} and col {ccol}, skipping this pixel.")
                    continue

                print(f"Using climate data from: {climate_csv_path}")
                env_template["pathToClimateCSV"] = [climate_csv_path]

                if setup["incl_hist"]:

                    if rcm[:3] == "UHO":
                        hist_subpath_to_csv = TEMPLATE_PATH_CLIMATE_CSV.format(gcm=gcm, rcm="CLMcom-CCLM4-8-17",
                                                                               scenario="historical", ensmem=ensmem,
                                                                               version=version, crow=str(crow),
                                                                               ccol=str(ccol))
                        for _ in range(4):
                            hist_subpath_to_csv = hist_subpath_to_csv.replace("//", "/")
                        env_template["pathToClimateCSV"].insert(0, paths["monica-path-to-climate-dir"] + setup[
                            "climate_path_to_csvs"] + "/" + hist_subpath_to_csv)

                    elif rcm[:3] == "SMH":
                        hist_subpath_to_csv = TEMPLATE_PATH_CLIMATE_CSV.format(gcm=gcm, rcm="CLMcom-CCLM4-8-17",
                                                                               scenario="historical", ensmem=ensmem,
                                                                               version=version, crow=str(crow),
                                                                               ccol=str(ccol))
                        for _ in range(4):
                            hist_subpath_to_csv = hist_subpath_to_csv.replace("//", "/")
                        env_template["pathToClimateCSV"].insert(0, paths["monica-path-to-climate-dir"] + setup[
                            "climate_path_to_csvs"] + "/" + hist_subpath_to_csv)

                    hist_subpath_to_csv = TEMPLATE_PATH_CLIMATE_CSV.format(gcm=gcm, rcm=rcm, scenario="historical",
                                                                           ensmem=ensmem, version=version,
                                                                           crow=str(crow), ccol=str(ccol))
                    for _ in range(4):
                        hist_subpath_to_csv = hist_subpath_to_csv.replace("//", "/")
                    env_template["pathToClimateCSV"].insert(0, paths["monica-path-to-climate-dir"] + setup[
                        "climate_path_to_csvs"] + "/" + hist_subpath_to_csv)
                print("pathToClimateCSV:", env_template["pathToClimateCSV"])
                if DEBUG_WRITE_CLIMATE:
                    listOfClimateFiles.add(subpath_to_csv)

                env_template["customId"] = {
                    "setup_id": setup_id,
                    "srow": srow, "scol": scol,
                    "crow": int(crow), "ccol": int(ccol),
                    "soil_id": soil_id,
                    "env_id": sent_env_count,
                    "is_sensitivity_analysis": is_sensitivity_analysis,
                    "param_name": p_name,
                    "param_value": p_value,
                    "nodata": False
                }

                print("Harvest type:", setup["harvest-date"])
                print("Srow: ", env_template["customId"]["srow"], "Scol:", env_template["customId"]["scol"])
                harvest_ws = next(
                    filter(lambda ws: ws["type"][-7:] == "Harvest", env_template["cropRotation"][0]["worksteps"]))
                if setup["harvest-date"] == "fixed":
                    print("Harvest-date:", harvest_ws["date"])
                elif setup["harvest-date"] == "auto":
                    print("Harvest-date:", harvest_ws["latest-date"])

                if not DEBUG_DONOT_SEND:
                    socket.send_json(env_template)
                    print("sent env ", sent_env_count, " customId: ", env_template["customId"])

                sent_env_count += 1

                # write debug output, as json file
                if DEBUG_WRITE:
                    debug_write_folder = paths["path-debug-write-folder"]
                    if not os.path.exists(debug_write_folder):
                        os.makedirs(debug_write_folder)
                    if sent_env_count < DEBUG_ROWS:

                        path_to_debug_file = debug_write_folder + "/row_" + str(sent_env_count - 1) + "_" + str(
                            setup_id) + ".json"

                        if not os.path.isfile(path_to_debug_file):
                            with open(path_to_debug_file, "w") as _:
                                _.write(json.dumps(env_template))
                        else:
                            print("WARNING: Row ", (sent_env_count - 1), " already exists")
            # print("unknown_soil_ids:", unknown_soil_ids)

        if env_template and is_sensitivity_analysis:
            env_template["pathToClimateCSV"] = ""
            env_template["customId"] = {
                "setup_id": setup_id,
                "no_of_sent_envs": sent_env_count,
                "is_sensitivity_analysis": is_sensitivity_analysis,
            }
            socket.send_json(env_template)

            # print("crows/cols:", crows_cols)
        # cs__.close()
        stop_setup_time = time.perf_counter()
        print("\nSetup ", sent_env_count, " envs took ", (stop_setup_time - start_setup_time), " seconds")
        sent_env_count = 0

    stop_time = time.perf_counter()

    # write summary of used json files
    if DEBUG_WRITE_CLIMATE:
        debug_write_folder = paths["path-debug-write-folder"]
        if not os.path.exists(debug_write_folder):
            os.makedirs(debug_write_folder)

        path_to_climate_summary = debug_write_folder + "/climate_file_list" + ".csv"
        with open(path_to_climate_summary, "w") as _:
            _.write('\n'.join(listOfClimateFiles))

    try:
        print("sending ", (sent_env_count - 1), " envs took ", (stop_time - start_time), " seconds")
        # print("ran from ", start, "/", row_cols[start], " to ", end, "/", row_cols[end]
        print("exiting run_producer()")
    except Exception:
        raise


if __name__ == "__main__":
    run_producer()