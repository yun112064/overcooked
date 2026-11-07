from app.db.base import Base
import app.db.models  # registers existing users; never creates or connects
from .schema_v1 import define_tables

tables = define_tables(Base.metadata)
