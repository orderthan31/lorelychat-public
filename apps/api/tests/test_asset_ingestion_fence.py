"""Owned image imports must not apply stale writes after an await."""
import asyncio
from io import BytesIO

from PIL import Image
import pytest
from sqlmodel import SQLModel, Session, select

from app.db.session import make_engine
from app.db.models import Character, CharacterAsset
from app.schemas.assets import CharacterAssetCreate, CharacterAssetUpdate
from app.services import asset_service, asset_image_service


def setup(tmp_path):
    root=tmp_path/'uploads';root.mkdir()
    image=BytesIO();Image.new('RGB',(320,320),'skyblue').save(image,'PNG');(root/'source.png').write_bytes(image.getvalue())
    engine=make_engine('sqlite:///'+str(tmp_path/'ingestion.db'));SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Character(id='c',name='Synthetic',persona='Synthetic'))
        session.add(CharacterAsset(id='a',character_id='c',label='Old',image_url='/uploads/old.webp',metadata_={'authored':'keep'}));session.commit()
    return engine,root


@pytest.mark.parametrize('change',['metadata','image','delete'])
def test_replacement_rejects_changed_asset_and_cleans_only_its_new_files(tmp_path,monkeypatch,change):
    engine,root=setup(tmp_path);original=asset_image_service.ingest_image;created=[]
    with Session(engine) as reader:
        asset=reader.get(CharacterAsset,'a')
        async def race(**kwargs):
            assert not reader.in_transaction()
            owned=await original(**kwargs);created.append(owned)
            with Session(engine) as writer:
                current=writer.get(CharacterAsset,'a')
                if change=='delete':writer.delete(current)
                elif change=='image':current.image_url='/uploads/concurrent.webp'
                else:current.metadata_={'authored':'keep','concurrent':'preserve'}
                writer.commit()
            return owned
        monkeypatch.setattr(asset_image_service,'ingest_image',race)
        with pytest.raises(ValueError,match='Asset changed'):
            asyncio.run(asset_service.update_asset_ingested(reader,asset,CharacterAssetUpdate(image_url='/uploads/source.png'),upload_root=root))
        reader.expire_all();current=reader.get(CharacterAsset,'a')
        if change=='delete':assert current is None
        elif change=='image':assert current.image_url=='/uploads/concurrent.webp'
        else:assert current.metadata_=={'authored':'keep','concurrent':'preserve'}
    assert created and all(not (root/'character-assets'/name).exists() for name in created[0].names)
    assert (root/'source.png').exists();engine.dispose()


def test_deleted_character_during_import_cannot_receive_new_asset(tmp_path,monkeypatch):
    engine,root=setup(tmp_path);original=asset_image_service.ingest_image;created=[]
    with Session(engine) as reader:
        async def race(**kwargs):
            assert not reader.in_transaction()
            owned=await original(**kwargs);created.append(owned)
            with Session(engine) as writer:
                writer.delete(writer.get(Character,'c'));writer.commit()
            return owned
        monkeypatch.setattr(asset_image_service,'ingest_image',race)
        with pytest.raises(ValueError,match='Character not found'):
            asyncio.run(asset_service.create_asset_ingested(reader,'c',CharacterAssetCreate(label='New',image_url='/uploads/source.png'),upload_root=root))
        assert len(reader.exec(select(CharacterAsset)).all())==1
    assert all(not (root/'character-assets'/name).exists() for name in created[0].names)
    assert (root/'source.png').exists();engine.dispose()
