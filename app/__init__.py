from fastapi import FastAPI
import asyncio

async def websocket_server():
    """
    async function to start websocket server main function. It will run as separate task in fastapi
    Reason to do this is currently OCPP 1.6j library does not support starlet based websocket library
    (FastAPI websocket is based on starlet, so we can not use it )
    We are running websocket server as a seperate event with the traditional websocket library
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

