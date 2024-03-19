from fastapi import APIRouter, HTTPException, status
from data_store.schemas import UserResponseModel
import asyncio
import random
import string
from data_store.schemas import *
from data_store.enums import ChargingStatus
from data_store.charge_point_db import charge_points
from utility.pollers import start_transaction_response_poller, stop_transaction_finder
from data_store import crud
from utility.custom_logger import logger

router = APIRouter()


@router.post("/start", response_model=UserResponseModel)
async def start_charging(start_charge_data: StartCharge):
    try:
        start_charge_data.id_tag = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        initial_response = await charge_points[start_charge_data.station_id].start_transaction(start_charge_data)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        try:
            transaction_id = await start_transaction_response_poller(start_charge_data.id_tag)
        except HTTPException as e:
            return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
        else:
            return {"status_code": status.HTTP_200_OK, "message": "Charging activity started",
                    "data": {"transaction_id": transaction_id}}


@router.post("/stop", response_model=UserResponseModel)
async def stop_charging(stop_charge_data: StopCharge):
    try:
        initial_response = await charge_points[stop_charge_data.station_id].stop_transaction(stop_charge_data)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        await stop_transaction_finder(stop_charge_data.transaction_id)
        return {"status_code": status.HTTP_200_OK, "message": "Charging activity stopped",
                "data": {}}


@router.post("/availability", response_model=UserResponseModel)
async def change_availability(status_data: ChangeAvailability):
    try:
        result = await charge_points[status_data.station_id].set_availability(status_data.current_status)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return result


@router.post("/reset", response_model=UserResponseModel)
async def reset_charger(reset_data: ResetCharger):
    try:
        result = await charge_points[reset_data.station_id].reset(reset_data.reset_type)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return result


@router.post("/unlock", response_model=UserResponseModel)
async def unlock_connector(unlock_data: UnlockConnector):
    try:
        result = await charge_points[unlock_data.station_id].unlock(unlock_data.connector_id)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return result


@router.post("/clear/profile", response_model=UserResponseModel)
async def clear_profile(clear_profile_data: ClearProfile):
    try:
        result = await charge_points[clear_profile_data.station_id].clear_profile()
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return result


@router.post("/update/firmware", response_model=UserResponseModel)
async def handle_firmware_update(firmware_update_data: FirmwareUpdate):
    try:
        # url = call.data.get("firmware_url")
        # delay = int(call.data.get("delay_hours", 0))
        url = ""
        delay = 0
        result = await charge_points[firmware_update_data.station_id].update_firmware(url, delay)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return result


@router.post("/configure", response_model=UserResponseModel)
async def handle_configuration(handle_configure_data: HandleConfigure):
    try:
        result = await charge_points[handle_configure_data.station_id].configure(handle_configure_data.key,
                                                                                 handle_configure_data.value)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return result


@router.get("/configuration", response_model=UserResponseModel)
async def get_configuration(get_conf_data: GetConfiguration):
    try:
        result = await charge_points[get_conf_data.station_id].get_configuration(get_conf_data.ocpp_key)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return result


@router.get("/diagnostic", response_model=UserResponseModel)
async def get_diagnostic(get_diag_data: GetDiagData):
    try:
        result = await charge_points[get_diag_data.station_id].get_diagnostics(get_diag_data.url_to_upload_diag)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return result


@router.post("/data/transfer", response_model=UserResponseModel)
async def handle_data_transfer(data_for_transfer: HandleDataTransfer):
    try:
        result = await charge_points[data_for_transfer.station_id].data_transfer(data_for_transfer)
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return result


@router.post("/status", response_model=UserResponseModel)
async def give_current_status(current_status_data: CurrentStatus):
    try:
        from server.ocpp_websocket_server import MetricCollector
        metric = MetricCollector()
        result = await metric.get_metric(current_status_data.station_id, "Energy.Session")
    except HTTPException as e:
        return {"status_code": e.status_code, "message": f"{e.detail}", "data": {}}
    else:
        return {"status_code": status.HTTP_200_OK, "message": f"Here is the current status",
                "data": {"current_status": result}}


@router.get("/partners", response_model=UserResponseModel)
async def get_all_partners():
    return {"status_code": status.HTTP_200_OK, "message": f"Here is the current status",
            "data": {"all_vendors": ["electrolite", "chargemod", "magenta"]}}


@router.get("/stations")
async def get_connected_vendors(vendor_id: str):
    try:
        vendor_stations = await crud.read_data_from_table(
            ReadTableSchema.parse_obj({"table_name": "ChargingStationLocation",
                                       "primary_key": "vendor_id",
                                       "primary_key_value": vendor_id,
                                       "index_name": 'vendor_id-station_status-index',
                                       "all_results": True}))
    except HTTPException:
        raise
    else:
        return {"status_code": status.HTTP_200_OK, "message": f"Here is the current status",
                "data": {"vendor_stations": vendor_stations}}
        # try:
        #     return data_schemas.InitUser.parse_obj(user_in_db)
        # except ValidationError:
        #     raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.get("/transactions", response_model=UserResponseModel)
async def get_ongoing_transactions(transaction_type: ChargingStatus):
    try:
        sessions = await crud.read_data_from_table(
            ReadTableSchema.parse_obj({"table_name": "ChargingSessionRecords",
                                       "primary_key": "current_status",
                                       "primary_key_value": transaction_type.value,
                                       "index_name": 'current_status-vendor_id-index',
                                       "all_results": True}))
    except HTTPException:
        raise
    else:
        return {"status_code": status.HTTP_200_OK, "message": f"Here is the current status",
                "data": {"vendor_stations": sessions}}
