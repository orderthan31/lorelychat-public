"""Fail-closed image admission; native pixel decode runs in a disposable process.

Structural parsers are defense in depth, not a substitute for actual decoding.
All expensive parsing/inflation runs under the worker's CPU/memory/deadline caps.
"""
import asyncio
from pathlib import Path
import subprocess
import sys
import threading

DECODE_TIMEOUT_SECONDS = 5.0
MAX_ENCODED_BYTES = 8 * 1024 * 1024
# Load shedding, not an unbounded executor queue; shared by sync/async callers.
_DECODE_SLOTS = threading.BoundedSemaphore(2)
_WORKER = Path(__file__).with_name('image_decode_worker.py')
_FORMATS = {'image/png': ('PNG', '.png'), 'image/jpeg': ('JPEG', '.jpg'),
            'image/gif': ('GIF', '.gif'), 'image/webp': ('WEBP', '.webp')}


class ImageDecoderBusy(ValueError):
    """No admission slot; callers should retry later."""


class ImageDecoderUnavailable(ValueError):
    """Worker could not be started, rather than invalid image content."""


def _admit(data: bytes, content_type: str) -> tuple[str, str]:
    if not data or len(data) > MAX_ENCODED_BYTES or content_type not in _FORMATS:
        raise ValueError('Invalid image or image exceeds safety budget')
    return _FORMATS[content_type]


def _worker_command(content_type: str) -> list[str]:
    return [sys.executable, '-I', '-B', str(_WORKER), content_type]


def verify_image(data: bytes, content_type: str) -> str:
    """Synchronous compatibility API. Async request handlers must use async API."""
    _, extension = _admit(data, content_type)
    if not _DECODE_SLOTS.acquire(blocking=False):
        raise ImageDecoderBusy('Image decoder busy')
    try:
        result = subprocess.run(_worker_command(content_type), input=data,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env={}, timeout=DECODE_TIMEOUT_SECONDS, check=False)
        if result.returncode != 0 or result.stdout != b'OK':
            raise ValueError('Invalid image or image exceeds safety budget')
    except OSError:
        raise ImageDecoderUnavailable('Image decoder unavailable') from None
    except subprocess.TimeoutExpired:
        raise ValueError('Image decoder budget exceeded') from None
    finally:
        _DECODE_SLOTS.release()
    return extension


async def verify_image_async(data: bytes, content_type: str) -> str:
    """Kill and reap native work on timeout/cancellation; never block the loop."""
    _, extension = _admit(data, content_type)
    if not _DECODE_SLOTS.acquire(blocking=False):
        raise ImageDecoderBusy('Image decoder busy')
    process = None
    # Shield spawn so cancellation cannot lose the handle to a running child.
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *_worker_command(content_type), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env={}))
    try:
        async with asyncio.timeout(DECODE_TIMEOUT_SECONDS):
            process = await asyncio.shield(spawn)
            output, _ = await process.communicate(data)
            if process.returncode != 0 or output != b'OK':
                raise ValueError('Invalid image or image exceeds safety budget')
    except TimeoutError:
        raise ValueError('Image decoder budget exceeded') from None
    except OSError:
        raise ImageDecoderUnavailable('Image decoder unavailable') from None
    finally:
        async def cleanup():
            try:
                child = process if process is not None else await spawn
                if child.returncode is None:
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                await child.wait()
            except OSError:
                pass
            finally:
                _DECODE_SLOTS.release()
        # Repeated cancellation must not leak a child or release its slot early.
        cleanup_task = asyncio.create_task(cleanup())
        await asyncio.shield(cleanup_task)
    return extension

import struct
import zlib

MAX_PIXELS = 24_000_000
MAX_DIMENSION = 16384
MAX_FRAMES = 128
MAX_DECODED_BYTES = 128 * 1024 * 1024


def dimensions(width, height):
    if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION and width * height <= MAX_PIXELS):
        raise ValueError('Image dimensions exceed budget')


def validate_png(data):
    offset=8;compressed=[];seen_end=False;seen_header=False;seen_palette=False
    width=height=depth=color=interlace=0
    while offset < len(data):
        if offset+12>len(data):raise ValueError('Truncated PNG')
        length=struct.unpack_from('>I',data,offset)[0]
        kind=data[offset+4:offset+8];end=offset+8+length
        if end+4>len(data):raise ValueError('Truncated PNG chunk')
        body=data[offset+8:end]
        if zlib.crc32(kind+body)!=struct.unpack_from('>I',data,end)[0]:raise ValueError('Invalid PNG checksum')
        if not seen_header and kind!=b'IHDR':raise ValueError('Missing PNG header')
        if kind==b'IHDR':
            if seen_header or length!=13:raise ValueError('Invalid PNG header')
            width,height,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',body)
            dimensions(width,height)
            if color not in {0,2,3,4,6} or depth not in {1,2,4,8,16} or compression or filtering or interlace not in {0,1}:raise ValueError('Invalid PNG format')
            seen_header=True
        if kind==b'acTL':
            if length!=8 or struct.unpack_from('>I',body)[0]>MAX_FRAMES:raise ValueError('Too many PNG frames')
            # APNG frame streams need a real animation decoder. Do not pretend to verify them.
            raise ValueError('Animated PNG requires a verified decoder')
        if kind==b'PLTE':
            if seen_palette or compressed or color in {0,4} or not length or length%3 or length>768:
                raise ValueError('Invalid PNG palette')
            if color==3 and length//3>2**depth:raise ValueError('Invalid PNG palette size')
            seen_palette=True
        if kind==b'IDAT':
            if color==3 and not seen_palette:raise ValueError('Missing PNG palette')
            compressed.append(body)
        if kind==b'IEND':
            if length or end+4!=len(data):raise ValueError('Invalid PNG end')
            seen_end=True;break
        offset=end+4
    if not seen_end or not compressed:raise ValueError('Incomplete PNG')
    decoder=zlib.decompressobj();decoded=0
    for part in compressed:
        while part:
            output=decoder.decompress(part,65536)
            decoded+=len(output)
            if decoded>MAX_DECODED_BYTES:raise ValueError('PNG decompression exceeds budget')
            part=decoder.unconsumed_tail
        if decoder.unused_data:raise ValueError('Extra PNG compressed payload')
    if not decoder.eof:raise ValueError('Truncated PNG pixels')
    if interlace==0:
        channels={0:1,2:3,3:1,4:2,6:4}[color]
        expected=height*(1+(width*channels*depth+7)//8)
        if decoded!=expected:raise ValueError('Invalid PNG pixel byte count')


def validate_jpeg(data):
    if not data.endswith(b'\xff\xd9'):raise ValueError('Missing JPEG end')
    offset=2;seen_size=False;seen_scan=False
    while offset<len(data)-2:
        if data[offset]!=255:raise ValueError('Invalid JPEG marker')
        while offset<len(data) and data[offset]==255:offset+=1
        marker=data[offset];offset+=1
        if marker==0xda:
            seen_scan=True;break
        if marker in {0xd8,0xd9,0x01} or 0xd0<=marker<=0xd7:continue
        if offset+2>len(data):raise ValueError('Truncated JPEG')
        length=int.from_bytes(data[offset:offset+2],'big')
        if length<2 or offset+length>len(data):raise ValueError('Invalid JPEG segment')
        if marker in {0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf}:
            if length<8:raise ValueError('Invalid JPEG size')
            dimensions(int.from_bytes(data[offset+5:offset+7],'big'),int.from_bytes(data[offset+3:offset+5],'big'));seen_size=True
        offset+=length
    if not seen_size or not seen_scan:raise ValueError('Missing JPEG image data')


def validate_gif(data):
    if len(data)<14:raise ValueError('Truncated GIF')
    width,height=struct.unpack_from('<HH',data,6);dimensions(width,height)
    packed=data[10];offset=13+(3*(2**((packed&7)+1)) if packed&128 else 0);frames=0
    def subblocks(pos):
        while True:
            if pos>=len(data):raise ValueError('Truncated GIF blocks')
            count=data[pos];pos+=1
            if not count:return pos
            pos+=count
            if pos>len(data):raise ValueError('Truncated GIF block')
    while offset<len(data):
        marker=data[offset];offset+=1
        if marker==0x3b:
            if frames<1 or offset!=len(data):raise ValueError('Invalid GIF end')
            return
        if marker==0x21:
            offset+=1;offset=subblocks(offset)
        elif marker==0x2c:
            if offset+10>len(data):raise ValueError('Truncated GIF frame')
            left,top,w,h,packed=struct.unpack_from('<HHHHB',data,offset);dimensions(w,h)
            if left+w>width or top+h>height:raise ValueError('GIF frame outside canvas')
            frames+=1
            if frames>MAX_FRAMES or width*height*frames>MAX_PIXELS:raise ValueError('GIF animation exceeds pixel/frame budget')
            if not (data[10]&128 or packed&128):raise ValueError('Missing GIF palette')
            offset+=9+(3*(2**((packed&7)+1)) if packed&128 else 0)
            if offset>=len(data) or not 2<=data[offset]<=8:raise ValueError('Invalid GIF code size')
            offset=subblocks(offset+1)
        else:raise ValueError('Invalid GIF block')
    raise ValueError('Missing GIF end')


def validate_webp(data):
    if len(data)<20 or int.from_bytes(data[4:8],'little')+8!=len(data):raise ValueError('Invalid WebP length')
    offset=12;seen_size=False;frames=0
    while offset<len(data):
        if offset+8>len(data):raise ValueError('Truncated WebP chunk')
        kind=data[offset:offset+4];size=int.from_bytes(data[offset+4:offset+8],'little');body=data[offset+8:offset+8+size]
        if len(body)!=size:raise ValueError('Truncated WebP payload')
        if kind==b'VP8X' and size==10:
            dimensions(1+int.from_bytes(body[4:7],'little'),1+int.from_bytes(body[7:10],'little'));seen_size=True
        elif kind==b'VP8 ':
            if size<10 or body[3:6]!=b'\x9d\x01\x2a':raise ValueError('Invalid WebP VP8 header')
            dimensions(int.from_bytes(body[6:8],'little')&16383,int.from_bytes(body[8:10],'little')&16383);seen_size=True
        elif kind==b'VP8L':
            if size<5 or body[0]!=0x2f:raise ValueError('Invalid WebP lossless header')
            value=int.from_bytes(body[1:5],'little');dimensions(1+(value&16383),1+((value>>14)&16383));seen_size=True
        elif kind==b'ANMF':
            frames+=1
            if frames>MAX_FRAMES:raise ValueError('Too many WebP frames')
            # Nested frame codecs cannot be verified by this structural reader.
            raise ValueError('Animated WebP requires a verified decoder')
        offset+=8+size+(size%2)
    if not seen_size or offset!=len(data):raise ValueError('Missing WebP dimensions')


def verify_structure(data, content_type):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):mime,ext,check='image/png','.png',validate_png
    elif data.startswith(b'\xff\xd8'):mime,ext,check='image/jpeg','.jpg',validate_jpeg
    elif data.startswith((b'GIF87a',b'GIF89a')):mime,ext,check='image/gif','.gif',validate_gif
    elif data.startswith(b'RIFF') and data[8:12]==b'WEBP':mime,ext,check='image/webp','.webp',validate_webp
    else:raise ValueError('Not a supported image')
    if content_type!=mime:raise ValueError('Image MIME does not match content')
    check(data)
    return ext
