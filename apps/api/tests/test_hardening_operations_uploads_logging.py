import asyncio
import io
import logging
import struct
import sys
import zlib
import pytest
from fastapi import HTTPException


def png(width=1,height=1):
    def chunk(kind,body): return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\0\0\0'))+chunk(b'IEND',b'')

class Upload:
    content_type='image/png'
    def __init__(self,data): self.data=io.BytesIO(data);self.reads=[]
    async def read(self,size=-1): self.reads.append(size);return self.data.read(size)


def test_bounded_upload_read():
    from app.core.upload_files import read_verified_image
    upload=Upload(b'x'*100)
    with pytest.raises(HTTPException) as err: asyncio.run(read_verified_image(upload,32))
    assert err.value.status_code==413
    assert all(0 < size <= 65536 for size in upload.reads)
    assert upload.data.tell() == 33

@pytest.mark.parametrize('data',[b'not image',png()[:-2],png(100000,100000)])
def test_invalid_or_bomb_images(data):
    from app.core.upload_files import read_verified_image
    with pytest.raises(HTTPException): asyncio.run(read_verified_image(Upload(data),1024))

def test_png_validation_and_mime_match():
    from app.core.upload_files import read_verified_image
    upload=Upload(png())
    assert asyncio.run(read_verified_image(upload,1024)) == (png(),'.png')
    upload=Upload(png());upload.content_type='image/jpeg'
    with pytest.raises(HTTPException): asyncio.run(read_verified_image(upload,1024))

def test_final_formatter_privacy_and_two_handlers():
    from app.core.logging_config import SensitiveDataFormatter
    try:
        try: raise ValueError('https://provider/a?key=SYNTHETIC_SECRET /private/story.txt PRIVATE_PROSE')
        except ValueError as cause: raise RuntimeError('outer Bearer SYNTHETIC_BEARER') from cause
    except RuntimeError:
        record=logging.LogRecord('test',logging.ERROR,__file__,1,'safe task %s',('id',),sys.exc_info())
    record.exc_text='cached SYNTHETIC_CACHED /private/secret.txt'
    fmt=SensitiveDataFormatter('%(levelname)s %(message)s')
    one=fmt.format(record);two=fmt.format(record)
    for value in ['SYNTHETIC_SECRET','SYNTHETIC_BEARER','PRIVATE_PROSE','SYNTHETIC_CACHED','/private/']:
        assert value not in one and value not in two
    assert 'ValueError' in one and 'RuntimeError' in one and 'safe task id' in one

def test_body_limit_chunked_and_content_length():
    from app.ops.request_limits import RequestBodyLimitMiddleware
    async def exercise(headers,chunks):
        consumed=[];sent=[]
        async def endpoint(scope,receive,send):
            while True:
                msg=await receive(); consumed.append(msg)
                if not msg.get('more_body'):break
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        async def receive(): return chunks.pop(0)
        async def send(value):sent.append(value)
        await RequestBodyLimitMiddleware(endpoint,max_bytes=10)({'type':'http','headers':headers},receive,send)
        return consumed,sent
    consumed,sent=asyncio.run(exercise([(b'content-length',b'11')],[]))
    assert not consumed and sent[0]['status']==413
    consumed,sent=asyncio.run(exercise([],[{'type':'http.request','body':b'123456','more_body':True},{'type':'http.request','body':b'12345'}]))
    assert sent[0]['status']==413 and len(consumed)==1
