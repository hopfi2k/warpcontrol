"""Representation of a OCCP Entities."""
from __future__ import annotations
from data_store.schemas import *
from data_store.charge_point_db import register_new_station, charging_activity, check_existing_stations
import asyncio
from fastapi import HTTPException, status
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import logging
import string
import random
from math import sqrt
import pathlib
import ssl
import time
import websockets.protocol
import websockets.server
import websockets.exceptions

from ocpp.exceptions import NotImplementedError
from ocpp.messages import CallError
from ocpp.routing import on
from ocpp.v16 import ChargePoint as cp, call, call_result
from ocpp.v16.enums import (
    Action,
    AuthorizationStatus,
    AvailabilityStatus,
    AvailabilityType,
    ChargePointStatus,
    ChargingProfileKindType,
    ChargingProfilePurposeType,
    ChargingProfileStatus,
    ChargingRateUnitType,
    ClearChargingProfileStatus,
    ConfigurationStatus,
    DataTransferStatus,
    Measurand,
    MessageTrigger,
    Phase,
    RegistrationStatus,
    RemoteStartStopStatus,
    ResetStatus,
    ResetType,
    TriggerMessageStatus,
    UnitOfMeasure,
    UnlockStatus,
)

from data_store.const import (
    CONF_AUTH_LIST,
    CONF_AUTH_STATUS,
    CONF_CPID,
    CONF_CSID,
    CONF_DEFAULT_AUTH_STATUS,
    CONF_FORCE_SMART_CHARGING,
    CONF_HOST,
    CONF_ID_TAG,
    CONF_IDLE_INTERVAL,
    CONF_METER_INTERVAL,
    CONF_MONITORED_VARIABLES,
    CONF_PORT,
    CONF_SKIP_SCHEMA_VALIDATION,
    CONF_SSL,
    CONF_SUBPROTOCOL,
    CONF_WEBSOCKET_CLOSE_TIMEOUT,
    CONF_WEBSOCKET_PING_INTERVAL,
    CONF_WEBSOCKET_PING_TIMEOUT,
    CONF_WEBSOCKET_PING_TRIES,
    CONFIG,
    DEFAULT_CPID,
    DEFAULT_CSID,
    DEFAULT_ENERGY_UNIT,
    DEFAULT_FORCE_SMART_CHARGING,
    DEFAULT_HOST,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_MEASURAND,
    DEFAULT_METER_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_POWER_UNIT,
    DEFAULT_SKIP_SCHEMA_VALIDATION,
    DEFAULT_SSL,
    DEFAULT_SUBPROTOCOL,
    DEFAULT_WEBSOCKET_CLOSE_TIMEOUT,
    DEFAULT_WEBSOCKET_PING_INTERVAL,
    DEFAULT_WEBSOCKET_PING_TIMEOUT,
    DEFAULT_WEBSOCKET_PING_TRIES,
    DOMAIN,
    HA_ENERGY_UNIT,
    HA_POWER_UNIT
)
from data_store.enums import (
    ConfigurationKey,
    HAChargerDetails,
    HAChargerServices,
    HAChargerSession,
    HAChargerStatuses,
    OcppMisc,
    StationStatus,
    Profiles,
)

from utility.custom_logger import logger
from data_store.charge_point_db import charge_points


class MetricCollector:
    """Server for handling OCPP connections."""

    def __init__(self):
        """Instantiate instance of a CentralSystem."""
        self.host = DEFAULT_HOST
        self.port = DEFAULT_PORT
        self.central_system_id = DEFAULT_CSID
        self.charge_point_id = DEFAULT_CPID
        self.websocket_close_timeout = DEFAULT_WEBSOCKET_CLOSE_TIMEOUT
        self.websocket_ping_tries = DEFAULT_WEBSOCKET_PING_TRIES
        self.websocket_ping_interval = DEFAULT_WEBSOCKET_PING_INTERVAL
        self.websocket_ping_timeout = DEFAULT_WEBSOCKET_PING_TIMEOUT
        self.subprotocol = DEFAULT_SUBPROTOCOL
        self.config = {"DEFAULT_FORCE_SMART_CHARGING": DEFAULT_FORCE_SMART_CHARGING,
                       "DEFAULT_MEASURAND": DEFAULT_MEASURAND,
                       "DEFAULT_METER_INTERVAL": DEFAULT_METER_INTERVAL,
                       "DEFAULT_IDLE_INTERVAL": DEFAULT_IDLE_INTERVAL
                       }
        self._server = None

    async def get_metric(self, cp_id: str, measurand: str):
        """Return last known value for given measurand."""
        # return charge_points[cp_id]._metrics[]
        return charge_points[cp_id]._metrics[measurand].value

    def get_unit(self, cp_id: str, measurand: str):
        if cp_id in charge_points:
            return charge_points[cp_id]._metrics[measurand].unit
        return None

    def get_ha_unit(self, cp_id: str, measurand: str):
        if cp_id in charge_points:
            return charge_points[cp_id]._metrics[measurand].ha_unit
        return None

    def get_extra_attr(self, cp_id: str, measurand: str):
        if cp_id in charge_points:
            return charge_points[cp_id]._metrics[measurand].extra_attr
        return None

    def get_available(self, cp_id: str):
        if cp_id in charge_points:
            return charge_points[cp_id].status == "STATE_OK"
        return False

    def get_supported_features(self, cp_id: str):
        if cp_id in charge_points:
            return charge_points[cp_id].supported_features
        return 0

    async def set_max_charge_rate_amps(self, cp_id: str, value: float):
        """Set the maximum charge rate in amps."""
        if cp_id in charge_points:
            return await charge_points[cp_id].set_charge_rate(limit_amps=value)
        return False


class ChargePoint(cp):
    """Server side representation of a charger."""

    def __init__(
            self,
            id: str,
            connection: websockets.server.WebSocketServerProtocol,
            metric_operations: MetricCollector,
            interval_meter_metrics: int = 10,
            skip_schema_validation: bool = False,
    ):
        """Instantiate a ChargePoint."""
        super().__init__(id, connection)
        for action in self.route_map:
            self.route_map[action]["_skip_schema_validation"] = skip_schema_validation

        self.interval_meter_metrics = interval_meter_metrics
        self.metric_operations = metric_operations
        self.status = StationStatus.state_init
        # Indicates if the charger requires a reboot to apply new
        # configuration.
        self._requires_reboot = False
        self.preparing = asyncio.Event()
        self.active_transaction_id: int = 0
        self.triggered_boot_notification = False
        self.received_boot_notification = False
        self.post_connect_success = False
        self.tasks = None
        self._metrics = defaultdict(lambda: Metric(None, None))
        self._metrics[HAChargerDetails.identifier.value].value = id
        self._metrics[HAChargerSession.session_time.value].unit = 1
        self._metrics[HAChargerSession.session_energy.value].unit = UnitOfMeasure.kwh.value
        self._metrics[HAChargerSession.meter_start.value].unit = UnitOfMeasure.kwh.value
        self._metrics[HAChargerStatuses.reconnects.value].value = 0
        self._attr_supported_features: int = 0

    async def post_connect(self):
        """Logic to be executed right after a charger connects."""
        try:
            self.status = StationStatus.state_ok.value
            await asyncio.sleep(2)
            await self.get_supported_features()
            resp = await self.get_configuration(ConfigurationKey.number_of_connectors.value)
            self._metrics[HAChargerDetails.connectors.value].value = resp
            # await self.get_configuration(ConfigurationKey.heartbeat_interval.value)
            await self.configure(
                ConfigurationKey.meter_values_sampled_data.value,
                self.metric_operations.config.get("CONF_MONITORED_VARIABLES", "DEFAULT_MEASURAND"),
            )
            await self.configure(
                ConfigurationKey.meter_value_sample_interval.value,
                str(self.metric_operations.config.get(CONF_METER_INTERVAL, DEFAULT_METER_INTERVAL)),
            )
            await self.configure(
                ConfigurationKey.clock_aligned_data_interval.value,
                str(self.metric_operations.config.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL)),
            )
            self.post_connect_success = True
            logger.debug(f"'{self.id}' post connection setup completed successfully")

            # nice to have, but not needed for integration to function
            # and can cause issues with some chargers
            await self.configure(ConfigurationKey.web_socket_ping_interval.value, "60")
            await self.set_availability()
            if Profiles.REM in self._attr_supported_features:
                if self.received_boot_notification is False:
                    await self.trigger_boot_notification()
                await self.trigger_status_notification()
        except NotImplementedError as e:
            logger.error(f"Configuration of the charger failed: {e}")

    async def get_supported_features(self):
        """Get supported features."""
        req = call.GetConfigurationPayload(key=[ConfigurationKey.supported_feature_profiles.value])
        resp = await self.call(req)
        feature_list = (resp.configuration_key[0][OcppMisc.value.value]).split(",")
        if feature_list[0] == "":
            logger.warning("No feature profiles detected, defaulting to Core")
            feature_list = [OcppMisc.feature_profile_core.value]
        if self.metric_operations.config.get(
                CONF_FORCE_SMART_CHARGING, DEFAULT_FORCE_SMART_CHARGING
        ):
            logger.warning("Force Smart Charging feature profile")
            self._attr_supported_features |= Profiles.SMART
        for item in feature_list:
            item = item.strip()
            if item == OcppMisc.feature_profile_core.value:
                self._attr_supported_features |= Profiles.CORE
            elif item == OcppMisc.feature_profile_firmware.value:
                self._attr_supported_features |= Profiles.FW
            elif item == OcppMisc.feature_profile_smart.value:
                self._attr_supported_features |= Profiles.SMART
            elif item == OcppMisc.feature_profile_reservation.value:
                self._attr_supported_features |= Profiles.RES
            elif item == OcppMisc.feature_profile_remote.value:
                self._attr_supported_features |= Profiles.REM
            elif item == OcppMisc.feature_profile_auth.value:
                self._attr_supported_features |= Profiles.AUTH
            else:
                logger.warning(f"Unknown feature profile detected ignoring: {item}")

        self._metrics[HAChargerDetails.features.value].value = self._attr_supported_features
        logger.debug(f"Feature profiles returned: {self._attr_supported_features}")

    async def trigger_boot_notification(self):
        """Trigger a boot notification."""
        req = call.TriggerMessagePayload(
            requested_message=MessageTrigger.boot_notification
        )
        resp = await self.call(req)
        if resp.status == TriggerMessageStatus.accepted:
            self.triggered_boot_notification = True
            return True
        else:
            self.triggered_boot_notification = False
            logger.warning(f"Failed with response: {resp.status}")
            return False

    async def trigger_status_notification(self):
        """Trigger status notifications for all connectors."""
        return_value = True
        nof_connectors = int(self._metrics[HAChargerDetails.connectors.value].value)
        for id in range(0, nof_connectors + 1):
            logger.debug(f"trigger status notification for connector={id}")
            req = call.TriggerMessagePayload(
                requested_message=MessageTrigger.status_notification,
                connector_id=int(id),
            )
            resp = await self.call(req)
            if resp.status != TriggerMessageStatus.accepted:
                logger.warning(f"Failed with response: {resp.status}")
                return_value = False
        return return_value

    async def clear_profile(self):
        """Clear all charging profiles."""
        req = call.ClearChargingProfilePayload()
        resp = await self.call(req)
        if resp.status == ClearChargingProfileStatus.accepted:
            return True
        else:
            logger.warning("Failed with response: %s", resp.status)
            return False

    async def set_availability(self, state: bool = True):
        """Change availability."""
        if state is True:
            typ = AvailabilityType.operative.value
        else:
            typ = AvailabilityType.inoperative.value

        req = call.ChangeAvailabilityPayload(connector_id=0, type=typ)
        resp = await self.call(req)
        if resp.status == AvailabilityStatus.accepted:
            return {"status_code": status.HTTP_200_OK, "message": "availability changed",
                    "data": {"result": resp.status}}
        else:
            logger.warning(f"Failed with response: {resp.status}")
            return {"status_code": status.HTTP_409_CONFLICT, "message": "Failed attempt to change availaibility",
                    "data": {"result": resp.status}}

    async def start_transaction(self, start_charge_data: StartCharge):
        """
        Remote start a transaction.

        Check if authorisation enabled, if it is disable it before remote start
        """
        logger.debug(f"Starting transaction for {start_charge_data}")
        resp = await self.get_configuration(ConfigurationKey.authorize_remote_tx_requests.value)
        logger.info(f"## conf response for start transaction is {resp}")
        if resp is True:
            await self.configure(ConfigurationKey.authorize_remote_tx_requests.value, "false")
        req = call.RemoteStartTransactionPayload(connector_id=start_charge_data.connector_id,
                                                 id_tag=start_charge_data.id_tag)
        resp = await self.call(req)
        logger.info(f"Got start response {resp}")
        if resp.status == RemoteStartStopStatus.accepted:
            logger.info(f"started with response: {resp}")
            return resp.status
        else:
            logger.warning(f"Failed with response: {resp}")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="Start request rejected from charging station")

    async def stop_transaction(self, stop_charge_data: StopCharge):
        """
        Request remote stop of current transaction.

        Leaves charger in finishing state until unplugged.
        Use reset() to make the charger available again for remote start
        """
        logger.info(f"stopping transaction for the {stop_charge_data}")
        if self.active_transaction_id == 0:
            logger.warning(f"There is no active transaction id for this station. "
                           f"Trying to stop with passed transaction id {stop_charge_data.transaction_id}")
            current_transaction_id = stop_charge_data.transaction_id
        else:
            if stop_charge_data.transaction_id == self.active_transaction_id:
                current_transaction_id = self.active_transaction_id
            else:
                logger.warning("Active transaction id and current transactionid is not matching. "
                               "Going ahead with passed transaction id")
                current_transaction_id = stop_charge_data.transaction_id

        req = call.RemoteStopTransactionPayload(
            transaction_id=current_transaction_id
        )
        resp = await self.call(req)
        if resp.status == RemoteStartStopStatus.accepted:
            logger.info(f"Stopped with response: {resp.status}")
            return resp.status
        else:
            logger.warning(f"Failed with response: {resp.status}")
            return resp.status

    async def monitor_connection(self):
        """Monitor the connection, by measuring the connection latency."""
        self._metrics[HAChargerStatuses.latency_ping.value].unit = "ms"
        self._metrics[HAChargerStatuses.latency_pong.value].unit = "ms"
        connection = self._connection
        timeout_counter = 0
        while connection.open:
            try:
                await asyncio.sleep(self.metric_operations.websocket_ping_interval)
                time0 = time.perf_counter()
                latency_ping = self.metric_operations.websocket_ping_timeout * 1000
                pong_waiter = await asyncio.wait_for(
                    connection.ping(), timeout=self.metric_operations.websocket_ping_timeout
                )
                time1 = time.perf_counter()
                latency_ping = round(time1 - time0, 3) * 1000
                latency_pong = self.metric_operations.websocket_ping_timeout * 1000
                await asyncio.wait_for(
                    pong_waiter, timeout=self.metric_operations.websocket_ping_timeout
                )
                timeout_counter = 0
                time2 = time.perf_counter()
                latency_pong = round(time2 - time1, 3) * 1000
                logger.debug(
                    f"Connection latency from '{self.metric_operations.central_system_id}' to '{self.id}': ping={latency_ping} ms, pong={latency_pong} ms",
                )
                self._metrics[HAChargerStatuses.latency_ping.value].value = latency_ping
                self._metrics[HAChargerStatuses.latency_pong.value].value = latency_pong

            except asyncio.TimeoutError as timeout_exception:
                logger.debug(
                    f"Connection latency from '{self.metric_operations.central_system_id}' to '{self.id}': ping={latency_ping} ms, pong={latency_pong} ms",
                )
                self._metrics[HAChargerStatuses.latency_ping.value].value = latency_ping
                self._metrics[HAChargerStatuses.latency_pong.value].value = latency_pong
                timeout_counter += 1
                if timeout_counter > self.metric_operations.websocket_ping_tries:
                    logger.debug(
                        f"Connection to '{self.id}' timed out after '{self.metric_operations.websocket_ping_tries}' ping tries",
                    )
                    raise timeout_exception
                else:
                    continue

    async def reset(self, typ=ResetType.soft):
        """Hard reset charger unless soft reset requested."""
        self._metrics[HAChargerStatuses.reconnects.value].value = 0
        req = call.ResetPayload(typ)
        resp = await self.call(req)
        if resp.status == ResetStatus.accepted:
            return {"status_code": status.HTTP_200_OK, "message": "Charger reset successfully",
                    "data": {"result": resp.status}}
        else:
            logger.warning(f"Failed with response: {resp.status}")
            return {"status_code": status.HTTP_400_BAD_REQUEST, "message": "Charger reset failed",
                    "data": {"result": resp.status}}

    async def unlock(self, connector_id: int = 1):
        """Unlock charger if requested."""
        req = call.UnlockConnectorPayload(connector_id)
        resp = await self.call(req)
        if resp.status == UnlockStatus.unlocked:
            return {"status_code": status.HTTP_200_OK, "message": f"connector {connector_id} unlocked successfully",
                    "data": {"result": resp.status}}
        else:
            logger.warning(f"Failed with response: {resp.status}")
            return {"status_code": status.HTTP_400_BAD_REQUEST, "message": f"Connector {connector_id} unlock failed",
                    "data": {"result": resp.status}}

    async def update_firmware(self, firmware_url: str, wait_time: int = 0):
        """Update charger with new firmware if available."""
        """where firmware_url is the http or https url of the new firmware"""
        """and wait_time is hours from now to wait before install"""
        logger.info("Going to execute update firmware function")
        if Profiles.FW in self._attr_supported_features:
            schema = vol.Schema(vol.Url())
            try:
                url = schema(firmware_url)
            except vol.MultipleInvalid as e:
                logger.debug("Failed to parse url: %s", e)
            update_time = (
                datetime.now(tz=timezone.utc) + timedelta(hours=wait_time)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            req = call.UpdateFirmwarePayload(location=url, retrieve_date=update_time)
            resp = await self.call(req)
            logger.info("Response: %s", resp)
            return True
        else:
            logger.warning("Charger does not support ocpp firmware updating")
            return False

    async def get_diagnostics(self, upload_url: str):
        """Upload diagnostic data to server from charger."""
        if Profiles.FW in self._attr_supported_features:
            req = call.GetDiagnosticsPayload(location=upload_url)
            resp = await self.call(req)
            logger.info(f"Diagnostic Response: {resp}")
            return {"status_code": status.HTTP_200_OK, "message": f"Diagnostic request successfully",
                    "data": {"result": resp}}
        else:
            logger.warning("Charger does not support ocpp diagnostics uploading")
            return {"status_code": status.HTTP_403_FORBIDDEN, "message": f"OCPP diagnostic upload is not supported",
                    "data": {}}

    async def data_transfer(self, data_for_transfer: HandleDataTransfer):
        """Request vendor specific data transfer from charger."""
        req = call.DataTransferPayload(
            vendor_id=data_for_transfer.vendor_id, message_id=data_for_transfer.message_id,
            data=data_for_transfer.data
        )
        resp = await self.call(req)
        if resp.status == DataTransferStatus.accepted:
            logger.info(f"data transfer response for the {data_for_transfer.vendor_id}, "
                        f"{data_for_transfer.message_id}, {data_for_transfer.data}")
            self._metrics[HAChargerDetails.data_response.value].value = datetime.now(
                tz=timezone.utc
            )
            self._metrics[HAChargerDetails.data_response.value].extra_attr = {data_for_transfer.message_id: resp.data}
            return {"status_code": status.HTTP_200_OK, "message": f"Data transfer handled successfully",
                    "data": {"result": resp.data}}
        else:
            logger.warning("Failed with response: %s", resp.status)
            return {"status_code": status.HTTP_400_BAD_REQUEST, "message": f"data transfer event failed with status {resp.status}",
                    "data": {}}

    async def _handle_call(self, msg):
        try:
            await super()._handle_call(msg)
        except NotImplementedError as e:
            response = msg.create_call_error(e).to_json()
            await self._send(response)

    async def start(self):
        """Start charge point."""
        logger.info("Starting a new station running start, monitor and post connect operations")
        await self.run(
            [super().start(), self.post_connect(), self.monitor_connection()]
        )

    async def run(self, tasks):
        """Run a specified list of tasks."""
        self.tasks = [asyncio.ensure_future(task) for task in tasks]
        try:
            await asyncio.gather(*self.tasks)
        except asyncio.TimeoutError:
            pass
        except websockets.exceptions.WebSocketException as websocket_exception:
            logger.debug(f"Connection closed to '{self.id}': {websocket_exception}")
        except Exception as other_exception:
            logger.error(
                f"Unexpected exception in connection to '{self.id}': '{other_exception}'",
                exc_info=True,
            )
        finally:
            await self.stop()

    async def stop(self):
        """Close connection and cancel ongoing tasks."""
        self.status = StationStatus.state_unavailable
        if self._connection.open:
            logger.debug(f"Closing websocket to '{self.id}'")
            await self._connection.close()
        for task in self.tasks:
            task.cancel()

    async def reconnect(self, connection: websockets.server.WebSocketServerProtocol):
        """Reconnect charge point."""
        logger.debug(f"Reconnect websocket to {self.id}")

        await self.stop()
        self.status = StationStatus.state_ok
        self._connection = connection
        self._metrics[HAChargerStatuses.reconnects.value].value += 1
        if self.post_connect_success is True:
            await self.run([super().start(), self.monitor_connection()])
        else:
            await self.run(
                [super().start(), self.post_connect(), self.monitor_connection()]
            )

    async def get_configuration(self, key: str = ""):
        """Get Configuration of charger for supported keys else return None."""
        if key == "":
            req = call.GetConfigurationPayload()
        else:
            logger.debug(f"Current configuration needed for the {key}")
            req = call.GetConfigurationPayload(key=[key])
        resp = await self.call(req)
        if resp.configuration_key is not None:
            logger.info(f"configuration response is {resp}")
            value = resp.configuration_key[0][OcppMisc.value.value]
            logger.debug(f"Get Configuration for {key}, {value}")
            self._metrics[HAChargerDetails.config_response.value].value = datetime.now(
                tz=timezone.utc
            )
            self._metrics[HAChargerDetails.config_response.value].extra_attr = {key: value}
            return value
        if resp.unknown_key is not None:
            logger.warning(f"Get Configuration returned unknown key for: {key}")
            return None

    async def configure(self, key: str, value: str):
        """Configure charger by setting the key to target value.

        First the configuration key is read using GetConfiguration. The key's
        value is compared with the target value. If the key is already set to
        the correct value nothing is done.

        If the key has a different value a ChangeConfiguration request is issued.

        """
        req = call.GetConfigurationPayload(key=[key])
        resp = await self.call(req)
        if resp.unknown_key is not None:
            if key in resp.unknown_key:
                logger.warning(f"{key} is unknown (not supported)")
                return {"status_code": status.HTTP_404_NOT_FOUND, "message": f"{key} is unknown", "data": {}}

        for key_value in resp.configuration_key:
            # If the key already has the targeted value we don't need to set
            # it.
            if key_value[OcppMisc.key.value] == key and key_value[OcppMisc.value.value] == value:
                return {"status_code": status.HTTP_200_OK, "message": f"{key} is already set", "data": {}}

            if key_value.get(OcppMisc.readonly.name, False):
                logger.warning("%s is a read only setting", key)
                return {"status_code": status.HTTP_403_FORBIDDEN, "message": f"{key} is read only", "data": {}}

        req = call.ChangeConfigurationPayload(key=key, value=value)

        resp = await self.call(req)

        if resp.status in [ConfigurationStatus.rejected, ConfigurationStatus.not_supported]:
            logger.warning(f"{resp.status} while setting {key} to {value}")
            return {"status_code": status.HTTP_403_FORBIDDEN, "message": f"{key} configuration request rejected",
                    "data": {}}
        else:
            if resp.status == ConfigurationStatus.reboot_required:
                self._requires_reboot = True
            return {"status_code": status.HTTP_200_OK, "message": f"{key} configuration successful",
                    "data": {"requires_reboot": True}}



    async def _get_specific_response(self, unique_id, timeout):
        # The ocpp library silences CallErrors by default. See
        # https://github.com/mobilityhouse/ocpp/issues/104.
        # This code 'unsilences' CallErrors by raising them as exception
        # upon receiving.
        resp = await super()._get_specific_response(unique_id, timeout)
        if isinstance(resp, CallError):
            raise resp.to_exception()
        return resp

    async def async_update_device_info(self, boot_info: dict):
        """Update device info asynchronuously."""

        logger.debug("Updating device info %s: %s", self.central.cpid, boot_info)
        # identifiers = {
        #     (DOMAIN, self.central.cpid),
        #     (DOMAIN, self.id),
        # }
        # serial = boot_info.get(om.charge_point_serial_number.name, None)
        # if serial is not None:
        #     identifiers.add((DOMAIN, serial))
        #
        # registry = device_registry.async_get(self.hass)
        # registry.async_get_or_create(
        #     config_entry_id=self.entry.entry_id,
        #     identifiers=identifiers,
        #     name=self.central.cpid,
        #     manufacturer=boot_info.get(om.charge_point_vendor.name, None),
        #     model=boot_info.get(om.charge_point_model.name, None),
        #     suggested_area="Garage",
        #     sw_version=boot_info.get(om.firmware_version.name, None),
        # )

    def get_authorization_status(self, id_tag):
        """Get the authorization status for an id_tag."""
        # get the domain wide configuration
        # config = self.hass.data[DOMAIN].get(CONFIG, {})
        # get the default authorization status. Use accept if not configured
        default_auth_status = AuthorizationStatus.accepted.value

        # get the authorization list
        auth_list = {}
        # search for the entry, based on the id_tag
        auth_status = None
        for auth_entry in auth_list:
            id_entry = auth_entry.get(CONF_ID_TAG, None)
            if id_tag == id_entry:
                # get the authorization status, use the default if not configured
                auth_status = auth_entry.get(CONF_AUTH_STATUS, default_auth_status)
                logger.debug(
                    f"id_tag='{id_tag}' found in auth_list, authorization_status='{auth_status}'"
                )
                break

        if auth_status is None:
            auth_status = default_auth_status
            logger.debug(
                f"id_tag='{id_tag}' not found in auth_list, default authorization_status='{auth_status}'"
            )
        return auth_status

    def process_phases(self, data):
        """Process phase data from meter values payload."""
        logger.debug("Processing phase data obtained from server")

        def average_of_nonzero(values):
            nonzero_values: list = [v for v in values if float(v) != 0.0]
            nof_values: int = len(nonzero_values)
            average = sum(nonzero_values) / nof_values if nof_values > 0 else 0
            return average

        measurand_data = {}
        for item in data:
            # create ordered Dict for each measurand, eg {"voltage":{"unit":"V","L1-N":"230"...}}
            measurand = item.get(OcppMisc.measurand.value, None)
            phase = item.get(OcppMisc.phase.value, None)
            value = item.get(OcppMisc.value.value, None)
            unit = item.get(OcppMisc.unit.value, None)
            context = item.get(OcppMisc.context.value, None)
            if measurand is not None and phase is not None and unit is not None:
                if measurand not in measurand_data:
                    measurand_data[measurand] = {}
                measurand_data[measurand][OcppMisc.unit.value] = unit
                measurand_data[measurand][phase] = float(value)
                self._metrics[measurand].unit = unit
                self._metrics[measurand].extra_attr[OcppMisc.unit.value] = unit
                self._metrics[measurand].extra_attr[phase] = float(value)
                self._metrics[measurand].extra_attr[OcppMisc.context.value] = context

        line_phases = [Phase.l1.value, Phase.l2.value, Phase.l3.value]
        line_to_neutral_phases = [Phase.l1_n.value, Phase.l2_n.value, Phase.l3_n.value]
        line_to_line_phases = [Phase.l1_l2.value, Phase.l2_l3.value, Phase.l3_l1.value]

        for metric, phase_info in measurand_data.items():
            metric_value = None
            if metric in [Measurand.voltage.value]:
                if not phase_info.keys().isdisjoint(line_to_neutral_phases):
                    # Line to neutral voltages are averaged
                    metric_value = average_of_nonzero(
                        [phase_info.get(phase, 0) for phase in line_to_neutral_phases]
                    )
                elif not phase_info.keys().isdisjoint(line_to_line_phases):
                    # Line to line voltages are averaged and converted to line to neutral
                    metric_value = average_of_nonzero(
                        [phase_info.get(phase, 0) for phase in line_to_line_phases]
                    ) / sqrt(3)
                elif not phase_info.keys().isdisjoint(line_phases):
                    # Workaround for chargers that don't follow engineering convention
                    # Assumes voltages are line to neutral
                    metric_value = average_of_nonzero(
                        [phase_info.get(phase, 0) for phase in line_phases]
                    )
            else:
                if not phase_info.keys().isdisjoint(line_phases):
                    metric_value = sum(
                        phase_info.get(phase, 0) for phase in line_phases
                    )
                elif not phase_info.keys().isdisjoint(line_to_neutral_phases):
                    # Workaround for some chargers that erroneously use line to neutral for current
                    metric_value = sum(
                        phase_info.get(phase, 0) for phase in line_to_neutral_phases
                    )

            if metric_value is not None:
                metric_unit = phase_info.get(OcppMisc.unit.value)
                logger.debug(
                    "process_phases: metric: {metric}, phase_info: {phase_info} value: {metric_value} unit : {metric_unit}")
                if metric_unit == DEFAULT_POWER_UNIT:
                    self._metrics[metric].value = float(metric_value) / 1000
                    self._metrics[metric].unit = HA_POWER_UNIT
                elif metric_unit == DEFAULT_ENERGY_UNIT:
                    self._metrics[metric].value = float(metric_value) / 1000
                    self._metrics[metric].unit = HA_ENERGY_UNIT
                else:
                    self._metrics[metric].value = float(metric_value)
                    self._metrics[metric].unit = metric_unit

    @on(Action.MeterValues)
    def on_meter_values(self, connector_id: int, meter_value: dict, **kwargs):
        """Request handler for MeterValues Calls."""
        print(meter_value)
        transaction_id: int = kwargs.get(OcppMisc.transaction_id.name, 0)
        transaction_matches: bool = False
        # match is also false if no transaction is in progress ie active_transaction_id==transaction_id==0
        if transaction_id == self.active_transaction_id and transaction_id != 0:
            transaction_matches = True
        elif transaction_id != 0:
            logger.warning("Unknown transaction detected with id=%i", transaction_id)
        for bucket in meter_value:
            unprocessed = bucket[OcppMisc.sampled_value.name]
            processed_keys = []
            for idx, sampled_value in enumerate(bucket[OcppMisc.sampled_value.name]):
                print(idx, sampled_value)
                measurand = sampled_value.get(OcppMisc.measurand.value, DEFAULT_MEASURAND)
                value = sampled_value.get(OcppMisc.value.value, None)
                unit = sampled_value.get(OcppMisc.unit.value, DEFAULT_ENERGY_UNIT)
                phase = sampled_value.get(OcppMisc.phase.value, None)
                location = sampled_value.get(OcppMisc.location.value, None)
                context = sampled_value.get(OcppMisc.context.value, None)

                if len(sampled_value.keys()) == 1:  # Backwards compatibility
                    measurand = DEFAULT_MEASURAND
                    unit = DEFAULT_ENERGY_UNIT

                if phase is None:
                    if unit == DEFAULT_POWER_UNIT:
                        self._metrics[measurand].value = float(value) / 1000
                        self._metrics[measurand].unit = HA_POWER_UNIT
                    elif unit == DEFAULT_ENERGY_UNIT:
                        if transaction_matches:
                            self._metrics[measurand].value = float(value) / 1000
                            self._metrics[measurand].unit = HA_ENERGY_UNIT
                    else:
                        self._metrics[measurand].value = float(value)
                        self._metrics[measurand].unit = unit
                    if location is not None:
                        self._metrics[measurand].extra_attr[
                            OcppMisc.location.value
                        ] = location
                    if context is not None:
                        self._metrics[measurand].extra_attr[OcppMisc.context.value] = context
                    processed_keys.append(idx)
            for idx in sorted(processed_keys, reverse=True):
                unprocessed.pop(idx)
            # _LOGGER.debug("Meter data not yet processed: %s", unprocessed)
            if unprocessed is not None:
                self.process_phases(unprocessed)
        if HAChargerSession.meter_start.value not in self._metrics:
            self._metrics[HAChargerSession.meter_start.value].value = self._metrics[
                DEFAULT_MEASURAND
            ]
        if HAChargerSession.transaction_id.value not in self._metrics:
            self._metrics[HAChargerSession.transaction_id.value].value = kwargs.get(
                OcppMisc.transaction_id.name
            )
            self.active_transaction_id = kwargs.get(OcppMisc.transaction_id.name)
        if transaction_matches:
            self._metrics[HAChargerSession.session_time.value].value = round(
                (
                        int(time.time())
                        - float(self._metrics[HAChargerSession.transaction_id.value].value)
                )
                / 60
            )
            self._metrics[HAChargerSession.session_time.value].unit = "min"
            if self._metrics[HAChargerSession.meter_start.value].value is not None:
                self._metrics[HAChargerSession.session_energy.value].value = float(
                    self._metrics[DEFAULT_MEASURAND].value or 0
                ) - float(self._metrics[HAChargerSession.meter_start.value].value)

                self._metrics[HAChargerSession.session_energy.value].extra_attr[
                    HAChargerStatuses.id_tag.name
                ] = self._metrics[HAChargerStatuses.id_tag.value].value
        # self.hass.async_create_task(self.central.update(self.central.cpid))
        return call_result.MeterValuesPayload()

    @on(Action.BootNotification)
    def on_boot_notification(self, **kwargs):
        """Handle a boot notification."""
        resp = call_result.BootNotificationPayload(
            current_time=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            interval=3600,
            status=RegistrationStatus.accepted.value,
        )
        self.received_boot_notification = True
        logger.debug(f"Received boot notification for {self.id}: {kwargs}")
        # update metrics
        self._metrics[HAChargerDetails.model.value].value = kwargs.get(
            OcppMisc.charge_point_model.name, None
        )
        self._metrics[HAChargerDetails.vendor.value].value = kwargs.get(
            OcppMisc.charge_point_vendor.name, None
        )
        self._metrics[HAChargerDetails.firmware_version.value].value = kwargs.get(
            OcppMisc.firmware_version.name, None
        )
        self._metrics[HAChargerDetails.serial.value].value = kwargs.get(
            OcppMisc.charge_point_serial_number.name, None
        )
        return resp

    @on(Action.StatusNotification)
    def on_status_notification(self, connector_id, error_code, status, **kwargs):
        """Handle a status notification."""
        logger.info(f"Got status notification for connector {connector_id} error {error_code} status {status} "
                    f"extra {kwargs}")
        if connector_id == 0 or connector_id is None:
            self._metrics[HAChargerStatuses.status.value].value = status
            self._metrics[HAChargerStatuses.error_code.value].value = error_code
        elif connector_id == 1:
            self._metrics[HAChargerStatuses.status_connector.value].value = status
            self._metrics[HAChargerStatuses.error_code_connector.value].value = error_code
        if connector_id >= 1:
            self._metrics[HAChargerStatuses.status_connector.value].extra_attr[
                connector_id
            ] = status
            self._metrics[HAChargerStatuses.error_code_connector.value].extra_attr[
                connector_id
            ] = error_code
        if (
                status == ChargePointStatus.suspended_ev.value
                or status == ChargePointStatus.suspended_evse.value
        ):
            if Measurand.current_import.value in self._metrics:
                self._metrics[Measurand.current_import.value].value = 0
            if Measurand.power_active_import.value in self._metrics:
                self._metrics[Measurand.power_active_import.value].value = 0
            if Measurand.power_reactive_import.value in self._metrics:
                self._metrics[Measurand.power_reactive_import.value].value = 0
            if Measurand.current_export.value in self._metrics:
                self._metrics[Measurand.current_export.value].value = 0
            if Measurand.power_active_export.value in self._metrics:
                self._metrics[Measurand.power_active_export.value].value = 0
            if Measurand.power_reactive_export.value in self._metrics:
                self._metrics[Measurand.power_reactive_export.value].value = 0
        return call_result.StatusNotificationPayload()

    @on(Action.FirmwareStatusNotification)
    def on_firmware_status(self, status, **kwargs):
        """Handle firmware status notification."""
        self._metrics[HAChargerStatuses.firmware_status.value].value = status
        # self.hass.async_create_task(self.central.update(self.central.cpid))
        # self.hass.async_create_task(self.notify_ha(f"Firmware upload status: {status}"))
        return call_result.FirmwareStatusNotificationPayload()

    @on(Action.DiagnosticsStatusNotification)
    def on_diagnostics_status(self, status, **kwargs):
        """Handle diagnostics status notification."""
        logger.info("Diagnostics upload status: %s", status)
        # self.hass.async_create_task(
        #     self.notify_ha(f"Diagnostics upload status: {status}")
        # )
        return call_result.DiagnosticsStatusNotificationPayload()

    @on(Action.SecurityEventNotification)
    def on_security_event(self, type, timestamp, **kwargs):
        """Handle security event notification."""
        logger.info(
            "Security event notification received: %s at %s [techinfo: %s]",
            type,
            timestamp,
            kwargs.get(OcppMisc.tech_info.name, "none"),
        )
        # self.hass.async_create_task(
        #     self.notify_ha(f"Security event notification received: {type}")
        # )
        return call_result.SecurityEventNotificationPayload()

    @on(Action.Authorize)
    def on_authorize(self, id_tag, **kwargs):
        """Handle an Authorization request."""
        self._metrics[HAChargerStatuses.id_tag.value].value = id_tag
        auth_status = self.get_authorization_status(id_tag)
        return call_result.AuthorizePayload(id_tag_info={OcppMisc.status.value: auth_status})

    @on(Action.StartTransaction)
    def on_start_transaction(self, connector_id, id_tag, meter_start, **kwargs):
        """Handle a Start Transaction request."""
        auth_status = self.get_authorization_status(id_tag)
        if auth_status == AuthorizationStatus.accepted.value:
            self.active_transaction_id = int(time.time())
            self._metrics[HAChargerStatuses.id_tag.value].value = id_tag
            self._metrics[HAChargerStatuses.stop_reason.value].value = ""
            self._metrics[HAChargerSession.transaction_id.value].value = self.active_transaction_id
            self._metrics[HAChargerSession.meter_start.value].value = int(meter_start) / 1000
            result = call_result.StartTransactionPayload(
                id_tag_info={OcppMisc.status.value: AuthorizationStatus.accepted.value},
                transaction_id=self.active_transaction_id,
            )
            logger.info(f"Updating active transaction id for future reference {id_tag} {self.active_transaction_id}")
            charging_activity[id_tag] = self.active_transaction_id

        else:
            result = call_result.StartTransactionPayload(
                id_tag_info={OcppMisc.status.value: auth_status}, transaction_id=0
            )

        # self.hass.async_create_task(self.central.update(self.central.cpid))
        return result

    @on(Action.StopTransaction)
    def on_stop_transaction(self, meter_stop, timestamp, transaction_id, **kwargs):
        """Stop the current transaction."""

        if transaction_id != self.active_transaction_id:
            logger.error(
                "Stop transaction received for unknown transaction id=%i",
                transaction_id,
            )
        self.active_transaction_id = 0
        self._metrics[HAChargerStatuses.stop_reason.value].value = kwargs.get(OcppMisc.reason.name, None)
        if self._metrics[HAChargerSession.meter_start.value].value is not None:
            self._metrics[HAChargerSession.session_energy.value].value = int(
                meter_stop
            ) / 1000 - float(self._metrics[HAChargerSession.meter_start.value].value)
        if Measurand.current_import.value in self._metrics:
            self._metrics[Measurand.current_import.value].value = 0
        if Measurand.power_active_import.value in self._metrics:
            self._metrics[Measurand.power_active_import.value].value = 0
        if Measurand.power_reactive_import.value in self._metrics:
            self._metrics[Measurand.power_reactive_import.value].value = 0
        if Measurand.current_export.value in self._metrics:
            self._metrics[Measurand.current_export.value].value = 0
        if Measurand.power_active_export.value in self._metrics:
            self._metrics[Measurand.power_active_export.value].value = 0
        if Measurand.power_reactive_export.value in self._metrics:
            self._metrics[Measurand.power_reactive_export.value].value = 0
        return call_result.StopTransactionPayload(
            id_tag_info={OcppMisc.status.value: AuthorizationStatus.accepted.value}
        )

    @on(Action.DataTransfer)
    def on_data_transfer(self, vendor_id, **kwargs):
        """Handle a Data transfer request."""
        logger.debug("Data transfer received from %s: %s", self.id, kwargs)
        self._metrics[HAChargerDetails.data_transfer.value].value = datetime.now(tz=timezone.utc)
        self._metrics[HAChargerDetails.data_transfer.value].extra_attr = {vendor_id: kwargs}
        return call_result.DataTransferPayload(status=DataTransferStatus.accepted.value)

    @on(Action.Heartbeat)
    def on_heartbeat(self, **kwargs):
        """Handle a Heartbeat."""
        now = datetime.now(tz=timezone.utc)
        self._metrics[HAChargerStatuses.heartbeat.value].value = now
        return call_result.HeartbeatPayload(
            current_time=now.strftime("%Y-%m-%dT%H:%M:%SZ")
        )

    @property
    def supported_features(self) -> int:
        """Flag of Ocpp features that are supported."""
        return self._attr_supported_features

    def get_metric(self, measurand: str):
        """Return last known value for given measurand."""
        return self._metrics[measurand].value

    def get_extra_attr(self, measurand: str):
        """Return last known extra attributes for given measurand."""
        return self._metrics[measurand].extra_attr

    def get_unit(self, measurand: str):
        """Return unit of given measurand."""
        return self._metrics[measurand].unit


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


central_system = MetricCollector()


async def on_connect(websocket, path):
    """For every new charge point that connects, create a ChargePoint
    instance and start listening for messages.
    """
    try:
        requested_protocols = websocket.request_headers["Sec-WebSocket-Protocol"]
    except KeyError:
        logger.error("Client hasn't requested any Subprotocol. Closing Connection")
        return await websocket.close()
    if websocket.subprotocol:
        logger.info(f"Protocols Matched: {websocket.subprotocol}")
    else:
        # In the websockets lib if no subprotocols are supported by the
        # client and the server, it proceeds without a subprotocol,
        # so we have to manually close the connection.
        logger.warning(
            f"Protocols Mismatched | Expected Subprotocols: {websocket.available_subprotocols}, "
            f"but client supports  {requested_protocols} | Closing connection",
        )
        return await websocket.close()
    charge_point_id = path.strip("/")
    charge_point_id = charge_point_id[charge_point_id.rfind("/") + 1:]

    global central_system
    charge_point_object = check_existing_stations(charge_point_id)
    if charge_point_object:
        await charge_point_object.reconnect(websocket)
    else:
        new_charge_point = ChargePoint(charge_point_id, websocket, central_system)
        register_new_station(chargepoint_id=charge_point_id, charging_station=new_charge_point)
        await new_charge_point.start()


async def main():
    server = await websockets.serve(
        on_connect, "0.0.0.0", 9090, subprotocols=["ocpp1.6"]
    )
    logger.info("Server Started listening to new connections...")
    await server.wait_closed()


if __name__ == "__main__":
    # asyncio.run() is used when running this example with Python >= 3.7v
    asyncio.run(main())
