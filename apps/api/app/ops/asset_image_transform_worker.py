"""Disposable, credential-free Pillow transform; invoked by filename under -I."""
import io
import json
import resource
import runpy
import sys
import warnings
from pathlib import Path


def main():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 6))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    try:
        from PIL import Image, ImageOps
        policy = runpy.run_path(str(Path(__file__).with_name('image_validation.py')))
        source, target, edge, quality, preserve, minimum = sys.argv[1:]
        edge, quality, preserve = int(edge), int(quality), preserve == '1'
        minimum = int(minimum)
        data = sys.stdin.buffer.read(policy['MAX_ENCODED_BYTES'] + 1)
        fmt, _ = policy['_admit'](data, source)
        # Keep existing strict admission rules, except animated WebP: Pillow
        # decodes every frame below and the RIFF container remains length-bound.
        animated_webp = source == 'image/webp' and data[:4] == b'RIFF' and data[8:12] == b'WEBP' and b'ANIM' in data
        if animated_webp:
            if int.from_bytes(data[4:8], 'little') + 8 != len(data):
                raise ValueError()
        else:
            policy['verify_structure'](data, source)
        if fmt == 'JPEG':
            runpy.run_path(str(Path(__file__).with_name('image_decode_worker.py')))['strict_jpeg_entropy'](data)
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        frames, durations = [], []
        with Image.open(io.BytesIO(data), formats=[fmt]) as image:
            canvas = image.size
            count = getattr(image, 'n_frames', 1)
            policy['dimensions'](*canvas)
            if count > policy['MAX_FRAMES'] or canvas[0] * canvas[1] * count > policy['MAX_PIXELS']:
                raise ValueError()
            loop = int(image.info.get('loop', 0))
            for index in range(count):
                image.seek(index)
                if image.size != canvas:
                    raise ValueError()
                image.load()  # Validate all frames even for an explicit static derivative.
                if index == 0 or preserve:
                    frame = ImageOps.exif_transpose(image).convert('RGBA')
                    frame.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                    # Copy pixels only: no EXIF, ICC, XMP or comments survive.
                    clean = Image.frombytes('RGBA', frame.size, frame.tobytes())
                    if minimum and min(clean.size) < minimum:
                        padded = Image.new('RGBA', (max(minimum, clean.width), max(minimum, clean.height)),
                                           (255, 255, 255, 255) if target == 'image/jpeg' else (0, 0, 0, 0))
                        padded.paste(clean, ((padded.width - clean.width) // 2, (padded.height - clean.height) // 2))
                        clean = padded
                    frames.append(clean)
                    durations.append(int(image.info.get('duration', 100)))
        if preserve and count > 1 and target != 'image/webp':
            raise ValueError()
        output = io.BytesIO()
        options = {'quality': quality}
        if target == 'image/jpeg':
            background = Image.new('RGB', frames[0].size, 'white')
            background.paste(frames[0], mask=frames[0].getchannel('A'))
            frames[0] = background
        if preserve and count > 1:
            options.update(save_all=True, append_images=frames[1:], duration=durations, loop=loop, allow_mixed=False)
        frames[0].save(output, format={'image/webp': 'WEBP', 'image/jpeg': 'JPEG', 'image/png': 'PNG'}[target], **options)
        result = output.getvalue()
        if len(result) > policy['MAX_ENCODED_BYTES']:
            raise ValueError()
        with Image.open(io.BytesIO(result)) as check:
            # Encoder coalescing must not silently turn animation into a still.
            if preserve and count > 1 and not getattr(check, 'is_animated', False):
                raise ValueError()
        header = json.dumps({'width': frames[0].width, 'height': frames[0].height}).encode() + b'\n'
        sys.stdout.buffer.write(header + result)
        return 0
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
