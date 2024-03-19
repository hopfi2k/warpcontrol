from utility.custom_logger import logger
from fastapi import HTTPException, status
import asyncio
from data_store.charge_point_db import charging_activity


async def start_transaction_response_poller(id_tag):
    counter = 0
    while not charging_activity.get(id_tag, None):
        logger.info(f"Waiting for transaction id for id tag {id_tag}")
        counter += 1
        if counter > 15:
            raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT,
                                detail="Charging start reuest timed out. no response from station")
        await asyncio.sleep(.5)
    return charging_activity.get(id_tag)


async def stop_transaction_finder(transaction_id):
    dict_component = {key:val for key, val in charging_activity.items() if val == transaction_id}
    logger.info(f"removing key {dict_component} from charging activity list")
    del charging_activity[list(dict_component.keys())[0]]
