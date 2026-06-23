"""
Alert engine for fluID.

Evaluates each sensor reading against configured thresholds.
Implements cooldown to prevent alert storms.
Writes to alert_events and audit_log tables.

Phase 1: Rule-based thresholds (current)
Phase 2: Multi-parameter correlation (coming after 6 months of data)
Phase 3: ML anomaly detection (coming after 12 months of data)
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    AlertEvent, AlertLevel, AlertStatus, AuditLog, SensorConfig
)
from app.sensors.readers import ReadResult
from app.alerts.trend import TrendDetector, TrendResult

logger = logging.getLogger(__name__)
settings = get_settings()


class AlertEngine:
    def __init__(self):
        self.trend_detector = TrendDetector()

    async def evaluate(
        self,
        db: AsyncSession,
        readings: list[tuple[SensorConfig, ReadResult]],
    ):
        for sensor, result in readings:
            await self._check_sensor(db, sensor, result)

        await self._cross_parameter_check(db, readings)

    async def _check_sensor(
        self,
        db: AsyncSession,
        sensor: SensorConfig,
        result: ReadResult,
    ):
        value = result.value
        level = self._classify(sensor, value)

        if level is None:
            await self._resolve_active_alert(db, sensor)
        else:
            if not await self._is_in_cooldown(db, sensor, level):
                threshold = (
                    sensor.alert_threshold if level == AlertLevel.ALERT
                    else sensor.warn_threshold
                )
                message = self._build_message(sensor, value, level, threshold)

                alert = AlertEvent(
                    sensor_id=sensor.id,
                    plant_id=sensor.plant_id,
                    level=level,
                    triggered_value=value,
                    threshold_value=threshold,
                    message=message,
                )
                db.add(alert)

                await self._write_audit(db, sensor.plant_id, level,
                                        "ALERT", message)

                logger.warning(f"[{level.value}] {message}")

        await self._check_trend(db, sensor, value)

    async def _check_trend(
        self,
        db: AsyncSession,
        sensor: SensorConfig,
        current_value: float,
    ):
        trend = await self.trend_detector.analyze(db, sensor, current_value)

        if trend is None or not trend.is_significant:
            return

        projected_hours = trend.hours_to_alert or trend.hours_to_warn
        target_label = "ALERT" if trend.hours_to_alert else "WARN"

        if projected_hours is None:
            return

        if await self._is_in_trend_cooldown(db, sensor):
            return

        message = (
            f"PREDICTIVE: {sensor.label} trending {trend.direction.lower()} at "
            f"{abs(trend.rate_per_hour):.{sensor.decimals}f} {sensor.unit}/hr "
            f"(trend confidence R²={trend.r_squared}). Projected to reach "
            f"{target_label} threshold in approximately {projected_hours} hours "
            f"if current rate continues. No threshold breach yet — "
            f"early advisory only."
        )

        alert = AlertEvent(
            sensor_id=sensor.id,
            plant_id=sensor.plant_id,
            level=AlertLevel.WARN,
            triggered_value=current_value,
            threshold_value=(
                sensor.alert_threshold if target_label == "ALERT" else sensor.warn_threshold
            ),
            message=message,
        )
        db.add(alert)

        await self._write_audit(db, sensor.plant_id, AlertLevel.WARN,
                                "TREND", message)
        logger.warning(f"[PREDICTIVE] {message}")

    async def _is_in_trend_cooldown(
        self, db: AsyncSession, sensor: SensorConfig
    ) -> bool:
        cooldown_cutoff = datetime.utcnow() - timedelta(
            minutes=settings.alert_cooldown_minutes * 2
        )
        result = await db.execute(
            select(AuditLog)
            .where(
                AuditLog.plant_id == sensor.plant_id,
                AuditLog.category == "TREND",
                AuditLog.time >= cooldown_cutoff,
                AuditLog.message.like(f"%{sensor.label}%"),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    def _classify(
        self, sensor: SensorConfig, value: float
    ) -> Optional[AlertLevel]:
        if value >= sensor.alert_threshold:
            return AlertLevel.ALERT
        if value >= sensor.warn_threshold:
            return AlertLevel.WARN
        return None

    def _build_message(
        self,
        sensor: SensorConfig,
        value: float,
        level: AlertLevel,
        threshold: float,
    ) -> str:
        val_str = f"{value:.{sensor.decimals}f} {sensor.unit}"
        thr_str = f"{threshold:.{sensor.decimals}f} {sensor.unit}"
        if level == AlertLevel.ALERT:
            return (
                f"{sensor.label} exceeded alert threshold — "
                f"{val_str} (limit: {thr_str}). Immediate SOP review required."
            )
        return (
            f"{sensor.label} trending toward threshold — "
            f"{val_str} (warn at: {thr_str}). Monitor closely."
        )

    async def _is_in_cooldown(
        self,
        db: AsyncSession,
        sensor: SensorConfig,
        level: AlertLevel,
    ) -> bool:
        cooldown_cutoff = datetime.utcnow() - timedelta(
            minutes=settings.alert_cooldown_minutes
        )
        result = await db.execute(
            select(AlertEvent)
            .where(
                AlertEvent.sensor_id == sensor.id,
                AlertEvent.level == level,
                AlertEvent.status == AlertStatus.ACTIVE,
                AlertEvent.triggered_at >= cooldown_cutoff,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _resolve_active_alert(
        self, db: AsyncSession, sensor: SensorConfig
    ):
        result = await db.execute(
            select(AlertEvent)
            .where(
                AlertEvent.sensor_id == sensor.id,
                AlertEvent.status == AlertStatus.ACTIVE,
            )
        )
        active_alerts = result.scalars().all()
        for alert in active_alerts:
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = datetime.utcnow()
            await self._write_audit(
                db, sensor.plant_id, AlertLevel.INFO, "ALERT",
                f"{sensor.label} returned to normal range — alert resolved automatically."
            )

    async def _cross_parameter_check(
        self,
        db: AsyncSession,
        readings: list[tuple[SensorConfig, ReadResult]],
    ):
        elevated = [
            (s, r) for s, r in readings
            if self._classify(s, r.value) is not None
        ]

        if len(elevated) >= 3:
            sensor_names = ", ".join(s.label for s, _ in elevated)
            plant_id = elevated[0][0].plant_id
            message = (
                f"COMPOUND RISK: {len(elevated)} parameters simultaneously elevated "
                f"({sensor_names}). Cross-parameter correlation suggests systemic issue. "
                f"Manual inspection recommended."
            )
            await self._write_audit(
                db, plant_id, AlertLevel.ALERT, "ALERT", message
            )
            logger.critical(message)

    async def _write_audit(
        self,
        db: AsyncSession,
        plant_id,
        level: AlertLevel,
        category: str,
        message: str,
    ):
        now = datetime.utcnow()
        checksum = hashlib.sha256(
            f"{now.isoformat()}|{message}".encode()
        ).hexdigest()
        db.add(AuditLog(
            time=now,
            plant_id=plant_id,
            level=level,
            category=category,
            message=message,
            checksum=checksum,
        ))
