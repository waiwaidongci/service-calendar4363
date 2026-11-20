from sqlalchemy import Column,Integer,String,JSON,Text,Float
from src.config.database import Base
class SegmentSearch(Base):
 __tablename__='segment_searches'
 id=Column(Integer,primary_key=True); run_id=Column(Integer,unique=True,index=True); version=Column(Integer,default=1)
 status=Column(String(20),default='queued'); stage=Column(Integer,default=-1); data=Column(JSON); error=Column(Text); updated=Column(Float)
