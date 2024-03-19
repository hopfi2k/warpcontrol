from pydantic import BaseModel
from typing import Optional, Dict
from ocpp.v16 import call
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
import asyncio
from typing import Any


class StartCharge(BaseModel):
    station_id: str
    connector_id: int
    id_tag: Optional[str] = None


class StopCharge(BaseModel):
    station_id: str
    transaction_id: int


class UserResponseModel(BaseModel):
    status_code: int
    message: str
    data: Dict


class ChangeAvailability(BaseModel):
    station_id: str
    current_status: bool


class ResetCharger(BaseModel):
    station_id: str
    reset_type: ResetType


class UnlockConnector(BaseModel):
    station_id: str
    connector_id: int


class ClearProfile(BaseModel):
    station_id: str


class FirmwareUpdate(BaseModel):
    station_id: str


class HandleConfigure(BaseModel):
    station_id: str
    key: str
    value: str


class GetConfiguration(BaseModel):
    station_id: str
    ocpp_key: str


class GetDiagData(BaseModel):
    station_id: str
    url_to_upload_diag: str


class HandleDataTransfer(BaseModel):
    station_id: str
    vendor_id: str
    message_id: str
    data: str


class CurrentStatus(BaseModel):
    station_id: str


class ReadTableSchema(BaseModel):
    read_table: Optional[bool] = True
    table_name: str
    primary_key: str
    primary_key_value: str
    sort_key: Optional[str] = None
    sort_key_value: Optional[str] = None
    index_name: Optional[str] = None
    all_results: Optional[bool] = None


class UpdateTableSchema(BaseModel):
    update_table: Optional[bool] = True
    table_name: str
    primary_key: dict
    sort_key: Optional[dict] = None
    data_to_update: Any


class CreateEntrySchema(BaseModel):
    add_row: Optional[bool] = True
    table_name: str
    row_data: Any
