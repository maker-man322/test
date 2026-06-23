"""
Database models for fluID.

Tables:
  - sensor_configs   : registered sensors per plant
  - readings         : time-series sensor data
  - alert_events     : triggered alerts with resolution tracking
  - audit_log        : immutable append-only compliance log
  - plant_configs    : plant metadata
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, String, Float, Boolean, DateTime,
    Text, Integer, ForeignKey, Enum as SAEnum, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid
import enum


class Base(DeclarativeBase):
    pass


class SensorProtocol(str, enum.Enum):
    MODBUS_TCP = "modbus_tcp"
    MODBUS_RTU = "modbus_rtu"
    OPC_UA = "opc_ua"
    SIMULATED = "simulated"


class AlertLevel(str, enum.Enum):
    WARN = "WARN"
    ALERT = "ALERT"
    INFO = "INFO"


class AlertStatus(str, enum.Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"


# Plant
class PlantConfig(Base):
    __tablename__ = "plant_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    location = Column(String(200))
    system_type = Column(String(100))
    standard = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    sensors = relationship("SensorConfig", back_populates="plant")


# Sensor configuration
class SensorConfig(Base):
    __tablename__ = "sensor_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plant_configs.id"), nullable=False)

    sensor_key = Column(String(100), nullable=False)
    label = Column(String(100), nullable=False)
    unit = Column(String(30), nullable=False)

    protocol = Column(SAEnum(SensorProtocol), default=SensorProtocol.SIMULATED)
    host = Column(String(200))
    port = Column(Integer, default=502)
    unit_id = Column(Integer, default=1)
    register_address = Column(Integer)
    register_count = Column(Integer, default=2)
    scale_factor = Column(Float, default=1.0)

    nominal_value = Column(Float, nullable=False)
    warn_threshold = Column(Float, nullable=False)
    alert_threshold = Column(Float, nullable=False)
    min_range = Column(Float, nullable=False)
    max_range = Column(Float, nullable=False)
    decimals = Column(Integer, default=2)

    is_active = Column(Boolean, default=True)
    last_calibration = Column(DateTime)
    calibration_due = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    plant = relationship("PlantConfig", back_populates="sensors")
    readings = relationship("SensorReading", back_populates="sensor",
                            cascade="all, delete-orphan")


# Readings
class SensorReading(Base):
    __tablename__ = "readings"

    time = Column(DateTime, primary_key=True, default=datetime.utcnow)
    sensor_id = Column(UUID(as_uuid=True),
                       ForeignKey("sensor_configs.id"),
                       primary_key=True)

    value = Column(Float, nullable=False)
    raw_value = Column(Float)
    quality = Column(String(20), default="GOOD")

    sensor = relationship("SensorConfig", back_populates="readings")

    __table_args__ = (
        Index("ix_readings_sensor_time", "sensor_id", "time"),
    )


# Alert events
class AlertEvent(Base):
    __tablename__ = "alert_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(UUID(as_uuid=True), ForeignKey("sensor_configs.id"))
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plant_configs.id"))

    level = Column(SAEnum(AlertLevel), nullable=False)
    status = Column(SAEnum(AlertStatus), default=AlertStatus.ACTIVE)

    triggered_value = Column(Float, nullable=False)
    threshold_value = Column(Float, nullable=False)
    message = Column(Text, nullable=False)

    triggered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime)
    acknowledged_by = Column(String(200))
    resolution_note = Column(Text)


# Audit log
class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    time = Column(DateTime, default=datetime.utcnow, nullable=False)
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plant_configs.id"))

    level = Column(SAEnum(AlertLevel), default=AlertLevel.INFO)
    category = Column(String(50))
    message = Column(Text, nullable=False)

    user_id = Column(String(200))
    ip_address = Column(String(50))
    checksum = Column(String(64))

    __table_args__ = (
        Index("ix_audit_log_time", "time"),
        Index("ix_audit_log_plant_time", "plant_id", "time"),
    )
