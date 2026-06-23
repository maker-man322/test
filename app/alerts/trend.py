"""
Trend detection for fluID — Phase 1.

This is the genuinely predictive part of Phase 1, and it requires NO
plant-specific historical training data. It works on pure time-series math
applied to whatever data is flowing live, starting from hour one.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SensorReading, SensorConfig

logger = logging.getLogger(__name__)


@dataclass
class TrendResult:
    sensor_id: str
    sensor_key: str
    current_value: float
    rate_per_hour: float
    r_squared: float
    hours_to_warn: Optional[float]
    hours_to_alert: Optional[float]
    is_significant: bool
    direction: str


class TrendDetector:
    WINDOW_MINUTES = 240
    MIN_READINGS = 8
    MIN_R_SQUARED = 0.6
    MAX_PROJECTION_HOURS = 6.0

    async def analyze(
        self,
        db: AsyncSession,
        sensor: SensorConfig,
        current_value: float,
    ) -> Optional[TrendResult]:
        since = datetime.utcnow() - timedelta(minutes=self.WINDOW_MINUTES)

        result = await db.execute(
            select(SensorReading.time, SensorReading.value)
            .where(
                SensorReading.sensor_id == sensor.id,
                SensorReading.time >= since,
                SensorReading.quality == "GOOD",
            )
            .order_by(SensorReading.time)
        )
        rows = result.all()

        if len(rows) < self.MIN_READINGS:
            return None

        times = np.array([(r.time - rows[0].time).total_seconds() / 3600.0 for r in rows])
        values = np.array([r.value for r in rows])

        rate_per_hour, r_squared = self._linear_fit(times, values)
        is_significant = r_squared >= self.MIN_R_SQUARED and abs(rate_per_hour) > 1e-6

        direction = "STABLE"
        if is_significant:
            direction = "RISING" if rate_per_hour > 0 else "FALLING"

        hours_to_warn = self._project_time_to_threshold(
            current_value, rate_per_hour, sensor.warn_threshold, is_significant
        )
        hours_to_alert = self._project_time_to_threshold(
            current_value, rate_per_hour, sensor.alert_threshold, is_significant
        )

        return TrendResult(
            sensor_id=str(sensor.id),
            sensor_key=sensor.sensor_key,
            current_value=current_value,
            rate_per_hour=round(rate_per_hour, 5),
            r_squared=round(r_squared, 3),
            hours_to_warn=hours_to_warn,
            hours_to_alert=hours_to_alert,
            is_significant=is_significant,
            direction=direction,
        )

    def _linear_fit(self, times: np.ndarray, values: np.ndarray) -> tuple[float, float]:
        if len(times) < 2 or np.all(times == times[0]):
            return 0.0, 0.0

        slope, intercept = np.polyfit(times, values, 1)

        predicted = slope * times + intercept
        ss_res = np.sum((values - predicted) ** 2)
        ss_tot = np.sum((values - np.mean(values)) ** 2)

        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 1e-9 else 0.0
        r_squared = max(0.0, min(1.0, r_squared))

        return float(slope), float(r_squared)

    def _project_time_to_threshold(
        self,
        current_value: float,
        rate_per_hour: float,
        threshold: float,
        is_significant: bool,
    ) -> Optional[float]:
        if not is_significant:
            return None
        if rate_per_hour <= 0:
            return None
        if current_value >= threshold:
            return None

        hours = (threshold - current_value) / rate_per_hour

        if hours > self.MAX_PROJECTION_HOURS:
            return None

        return round(hours, 2)
