"""Private control tables, registered without importing app.main."""
from sqlalchemy import Table, Column, String, JSON, LargeBinary, Integer
from sqlmodel import SQLModel


def record(name):
    return Table(name, SQLModel.metadata,
                 Column('id', String, primary_key=True),
                 Column('owner_id', String, nullable=False, index=True),
                 Column('data', JSON, nullable=False))

settings = record('agent_settings')
sessions = record('agent_sessions')
drafts = record('agent_drafts')
operations = record('agent_operations')
image_jobs = record('agent_image_jobs')
clients = Table('agent_clients', SQLModel.metadata,
                Column('id', String, primary_key=True),
                Column('token_digest', String, unique=True, nullable=False),
                Column('data', JSON, nullable=False))
image_outputs = Table('agent_image_outputs', SQLModel.metadata,
                      Column('id', String, primary_key=True),
                      Column('owner_id', String, nullable=False),
                      Column('job_id', String, nullable=False, index=True),
                      Column('mime_type', String, nullable=False),
                      Column('digest', String, nullable=False),
                      Column('size', Integer, nullable=False),
                      Column('data', LargeBinary, nullable=False))
image_references = Table('agent_image_references', SQLModel.metadata,
    Column('id', String, primary_key=True),
    Column('owner_id', String, nullable=False),
    Column('draft_id', String, nullable=False, index=True),
    Column('target_id', String, nullable=False),
    Column('target_kind', String, nullable=False),
    Column('source_id', String, nullable=False),
    Column('digest', String, nullable=False),
    Column('mime_type', String, nullable=False),
    Column('data', LargeBinary, nullable=False))
TABLES = [settings, sessions, drafts, operations, image_jobs, clients, image_outputs, image_references]
