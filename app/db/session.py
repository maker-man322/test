"""
Database session management.
Plain PostgreSQL version for Render (no TimescaleDB required).
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from app.core.config import get_settings
from app.db.models import Base
import logging

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """FastAPI dependency — yields a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Create all tables.
    Safe to call multiple times.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # NOTE: TimescaleDB features commented out for Render deployment.
        # Uncomment when using TimescaleDB (self-hosted or managed).

        logger.info("Database initialised — ready for plain PostgreSQL")


async def seed_demo_plant(db: AsyncSession):
    """
    Insert a demo plant + sensor configs for Genome Valley Unit 7.
    Only runs if no plants exist yet.
    """
    from app.db.models import PlantConfig, SensorConfig, SensorProtocol
    from sqlalchemy import select
    import uuid

    result = await db.execute(select(PlantConfig).limit(1))
    if result.scalar_one_or_none():
        return  # Already seeded

    plant = PlantConfig(
        id=uuid.uuid4(),
        name="Genome Valley Unit 7",
        location="Hyderabad, Telangana",
        system_type="Purified Water",
        standard="WHO / IP 2022",
    )
    db.add(plant)
    await db.flush()

    demo_sensors = [
        dict(sensor_key="conductivity", label="Conductivity", unit="µS/cm",
             protocol=SensorProtocol.SIMULATED,
             nominal_value=0.6, warn_threshold=1.0, alert_threshold=1.3,
             min_range=0.1, max_range=1.6, decimals=2,
             host="192.168.1.10", port=502, unit_id=1, register_address=100, scale_factor=0.01),
        dict(sensor_key="toc", label="TOC", unit="ppb",
             protocol=SensorProtocol.SIMULATED,
             nominal_value=200, warn_threshold=400, alert_threshold=500,
             min_range=10, max_range=550, decimals=0,
             host="192.168.1.10", port=502, unit_id=1, register_address=102, scale_factor=1.0),
        dict(sensor_key="ph", label="pH (secondary signal)", unit="pH",
             protocol=SensorProtocol.SIMULATED,
             nominal_value=6.5, warn_threshold=7.4, alert_threshold=7.8,
             min_range=4.5, max_range=8.5, decimals=2,
             host="192.168.1.10", port=502, unit_id=1, register_address=104, scale_factor=0.01),
        dict(sensor_key="temperature", label="Temperature", unit="°C",
             protocol=SensorProtocol.SIMULATED,
             nominal_value=22.0, warn_threshold=26.0, alert_threshold=28.0,
             min_range=18.0, max_range=32.0, decimals=1,
             host="192.168.1.10", port=502, unit_id=1, register_address=106, scale_factor=0.1),
        dict(sensor_key="flow", label="Flow Rate", unit="L/hr",
             protocol=SensorProtocol.SIMULATED,
             nominal_value=130, warn_threshold=170, alert_threshold=185,
             min_range=80, max_range=200, decimals=0,
             host="192.168.1.10", port=502, unit_id=1, register_address=108, scale_factor=1.0),
    ]

    for s in demo_sensors:
        db.add(SensorConfig(plant_id=plant.id, **s))

    await db.commit()
    logger.info(f"Demo plant seeded: {plant.name} with {len(demo_sensors)} sensors")
