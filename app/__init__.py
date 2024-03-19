from fastapi import FastAPI
import asyncio
from config import Settings
from utility.custom_logger import init_logging
from server.ocpp_websocket_server import main
from .own_station_router import station_router

settings = Settings()
websocket_server_instance: asyncio.Task = None

session = boto3.session.Session(
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY)


def get_db():
    return session.resource('dynamodb', region_name=settings.DYNAMODB_REGION)


async def websocket_server():
    """
    async function to start websocket server main function. It will run as separate task in fastapi
    Reason to do this is currently OCPP 1.6j library does not support starlet based websocket library
    (FastAPI websocket is based on starlet, so we can not use it )
    We are running websocket server as a separate event with the traditional websocket library
    :return:
    """

    await main()


app = FastAPI()


@app.on_event("startup")
async def startup_event():
    # Create the asyncio task when the application starts up
    global websocket_server_instance
    websocket_server_instance = asyncio.create_task(websocket_server())


@app.on_event("shutdown")
async def closure_event():
    # Create the asyncio task when the application starts up
    global websocket_server_instance
    websocket_server_instance.done()


init_logging()

app.include_router(station_router.router)
