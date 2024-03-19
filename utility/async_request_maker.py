import aiohttp
import simplejson
from fastapi import HTTPException, status
from utility.custom_logger import logger


async def make_async_http_request(url, params=None, body=None):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=simplejson.dumps(body)) as resp:
                response = await resp.json()
    except aiohttp.ClientConnectorError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is not available")
    except simplejson.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unable to decode data")
    else:
        return response
