from utility.custom_logger import logger
from config import Settings
from data_store.schemas import ReadTableSchema, UpdateTableSchema, CreateEntrySchema
from utility.async_request_maker import make_async_http_request
from fastapi import HTTPException

settings = Settings()


async def read_data_from_table(data_for_read: ReadTableSchema):
    try:
        result = await make_async_http_request(url=settings.DB_API, body=data_for_read.dict())
    except HTTPException:
        logger.exception("Error while reading data from session table")
        raise
    else:
        return result


async def update_table(data_to_update: UpdateTableSchema):
    # update to session db
    try:
        logger.debug(f"Going to update a table with data {data_to_update}")
        result = await make_async_http_request(url=settings.DB_API, body=data_to_update.dict())
    except HTTPException:
        logger.exception(f"Unable to update table for data {data_to_update.dict()}")
        raise
    else:
        return result


async def create_entry_in_table(data_to_add: CreateEntrySchema):
    try:
        logger.debug(f"Going to create a new in try using data {data_to_add}")
        result = await make_async_http_request(url=settings.DB_API, body=data_to_add.dict())
    except HTTPException:
        logger.exception(f"Unable to create a new entry for data {data_to_add.dict()}")
        raise
    else:
        return result