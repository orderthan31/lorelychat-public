"""Small synthetic profiles; no production corpus, providers or load tests."""
import json
import os
from pathlib import Path
import time
import tracemalloc
import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel,Session,create_engine
from app.db.models import Character
from app.api.characters import list_characters

@pytest.mark.parametrize('size',[10,200,1000])
def test_catalog_page_materialization_stays_at_page_size(size):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Character(id=f'c{i:05}',name=f'Name {i:05}',persona='profile fixture '*64) for i in range(size)])
        session.commit();session.expunge_all()
        loaded=[];queries=[]
        def on_load(target,context):loaded.append(target.id)
        event.listen(Character,'load',on_load)
        event.listen(engine,'before_cursor_execute',lambda c,u,statement,p,ctx,m:queries.append(statement))
        try:
            tracemalloc.start();started=time.perf_counter()
            result=list_characters(paginated=True,page=1,page_size=10,q='',session=session)
            elapsed=time.perf_counter()-started;_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        finally:event.remove(Character,'load',on_load)
        assert result.total==size and len(result.items)==10 and len(loaded)==10
        assert len(queries)==2 and 'LIMIT' in queries[1]
        record={'catalog_rows':size,'page_size':10,'materialized_character_rows':len(loaded),'sql_statements':len(queries),'elapsed_seconds':elapsed,'tracemalloc_peak_bytes':peak}
        path=Path(os.environ['HARDENING_TEST_RUN'])/'operations-scale.jsonl'
        with path.open('a') as handle:handle.write(json.dumps(record)+'\n')
    engine.dispose()
