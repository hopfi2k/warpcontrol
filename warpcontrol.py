#
# warpcontrol is a simple billing system for billing charging
# session for wall boxes using the OCPP protocol
# currently OCPP protocol version 1.6j and 2.0.1 are supported
# thanks to the fantastic OCPP library from Mobility house
#
import asyncio
import logging
import websockets
from datetime import datetime
from collections import defaultdict

from ocpp.routing import on
from ocpp.v16 import ChargePoint as cp, call, call_result
from ocpp.v16.enums import RegistrationStatus

import uuid
from enums import (
    ConfigurationKey as ckey,
    HAChargerDetails as cdet,
    HAChargerServices as csvcs,
    HAChargerSession as csess,
    HAChargerStatuses as cstat,
    OcppMisc as om,
    Profiles as prof,
)

KNOWN_IDS = ['f8026771-4816-4127-a8fd-79f82b853706']

logging.basicConfig(level=logging.INFO)


class Metric:
    """Metric class."""

    def __init__(self, value, unit):
        """Initialize a Metric."""
        self._value = value
        self._unit = unit
        self._extra_attr = {}

    @property
    def value(self):
        """Get the value of the metric."""
        return self._value

    @value.setter
    def value(self, value):
        """Set the value of the metric."""
        self._value = value

    @property
    def unit(self):
        """Get the unit of the metric."""
        return self._unit

    @unit.setter
    def unit(self, unit: str):
        """Set the unit of the metric."""
        self._unit = unit

    @property
    def extra_attr(self):
        """Get the extra attributes of the metric."""
        return self._extra_attr

    @extra_attr.setter
    def extra_attr(self, extra_attr: dict):
        """Set the unit of the metric."""
        self._extra_attr = extra_attr


class ChargePoint(cp):
    def __init__(
            self,
            identifier: str,
            connection: websockets.WebSocketServerProtocol,
            interval_meter_metrics: int = 10,
            skip_schema_validation: bool = False,
            response_timeout=30
    ):
        # instantiate parent
        super().__init__(identifier, connection, response_timeout)

        self.received_boot_notification = None
        self._metrics = defaultdict(lambda: Metric(None, None))
        self._metrics[cdet.identifier.value].value = id

    @on('BootNotification')
    async def on_boot_notification(self, **kwargs):
        response = call_result.BootNotificationPayload(
            current_time=datetime.utcnow().isoformat(),
            interval=10,
            status=RegistrationStatus.accepted
        )
        self.received_boot_notification = True
        logging.debug("Received boot notification for %s: %s", self.id, kwargs)

        # update charger information
        self._metrics[cdet.model.value].value = kwargs.get(
            om.charge_point_model.name, None
        )
        self._metrics[cdet.vendor.value].value = kwargs.get(
            om.charge_point_vendor.name, None
        )
        self._metrics[cdet.firmware_version.value].value = kwargs.get(
            om.firmware_version.name, None
        )
        self._metrics[cdet.serial.value].value = kwargs.get(
            om.charge_point_serial_number.name, None
        )
        return response

    @on('StatusNotification')
    async def on_status_notification(self, connector_id, error_code, status, **kwargs):
        for i in kwargs:
            print("Argument: ", i, " value: ", kwargs[i])

    @on('MeterValues')
    async def on_meter_values(self, connector_id: int, meter_value: dict, **kwargs):
        transaction_id: int = kwargs.get("transaction_id.name, 0")


async def on_connect(websocket, path):
    """ For every new charge point that connects, create a ChargePoint
    instance and start listening for messages.
    """
    try:
        requested_protocols = websocket.request_headers['Sec-WebSocket-Protocol']
    except KeyError:
        logging.info("Client hasn't requested any Sub protocol. "
                     "Closing Connection")
        return await websocket.close()

    if websocket.subprotocol:
        logging.info("Protocols matched: %s", websocket.subprotocol)
    else:
        # In the websockets lib if no sub-protocols are supported by the
        # client and the server, it proceeds without a sub-protocol,
        # so we have to manually close the connection.
        logging.warning('Protocols Mismatched | Expected sub-protocols: %s,'
                        ' but client supports  %s | Closing connection',
                        websocket.available_subprotocols,
                        requested_protocols)
        return await websocket.close()

    charge_point_id = path.strip('/')
    cp = ChargePoint(charge_point_id, websocket)

    await cp.start()


async def main():
    server = await websockets.serve(
        on_connect,
        host='0.0.0.0',
        port=9000,
        subprotocols=["ocpp1.6"],
        ping_interval=None,
        ping_timeout=None,
        close_timeout=20
    )
    logging.info("WebSocket Server Started.")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
