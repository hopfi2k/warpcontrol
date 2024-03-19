import asyncio

from utility.custom_logger import logger

charge_points = {}
charging_activity = {}


def check_existing_stations(chargepoint_id):
    # return true if station exist in the list
    try:
        logger.info("Checking the existing stations")
        charge_point_object = charge_points[chargepoint_id]
    except KeyError:
        return None
    else:
        logger.debug(f"Chargepoint exist returning the object for id: {chargepoint_id}")
        return charge_point_object


def register_new_station(chargepoint_id, charging_station):
    logger.info(f"Adding new charging station with id {chargepoint_id}")
    charge_points[chargepoint_id] = charging_station


def register_new_charging_activity(id_tag):
    logger.info(f"Registering new charging activity with {id_tag}")
    charging_activity[id_tag] = asyncio.Queue()
    logger.info(f"Activity list after update is {charging_activity}")