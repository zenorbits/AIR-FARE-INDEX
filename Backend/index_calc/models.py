from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class AirfareIndex(Base):
    __tablename__ = 'airfare_index'

    id = Column(Integer, primary_key=True, autoincrement=True)
    route = Column(String(20), nullable=True) # NULL means "overall/aggregate" row
    lead_time_days = Column(Integer, nullable=True) # NULL for the overall aggregate row
    frequency = Column(String(20), nullable=False) # 'daily', 'weekly', 'monthly'
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    index_value = Column(Float, nullable=False)
    num_observations = Column(Integer, nullable=False)
    calculated_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            'route', 
            'lead_time_days', 
            'frequency', 
            'period_start', 
            name='uq_airfare_index_route_lead_freq_period'
        ),
    )

    def __repr__(self):
        return f"<AirfareIndex(route={self.route}, freq={self.frequency}, start={self.period_start}, val={self.index_value})>"
