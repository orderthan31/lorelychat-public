"""One-shot Linux decoder. Invoked by filename, never imports the application.

Not an OS security sandbox: resource isolation limits native codec accidents and
hostile workload cost. No secrets/environment, DB, provider or publication I/O.
"""
import io
import os
from pathlib import Path
import resource
import runpy
import sys
import warnings

ADDRESS_SPACE_BYTES = 768 * 1024 * 1024
CPU_SECONDS = 3



def strict_jpeg_entropy(data: bytes) -> None:
    """Consume coefficients with the SAME pinned Pillow libjpeg, warnings fatal.

    Pillow deliberately replaces libjpeg's output_message callback with a no-op
    (src/libImaging/JpegDecode.c), hiding truncated-entropy recovery. Its public
    load API cannot expose num_warnings. This narrow libjpeg 6.2 public-ABI binding
    is isolated in this one-shot process; CreateDecompress validates the entire
    struct size/version before decoding. Unsupported ABIs fail closed, never
    guess offsets or load an unrelated system codec. No C build dependency.
    """
    import ctypes as c
    from PIL import Image
    if Image.core.jpeglib_version != '6.2':
        raise ValueError('Unsupported strict JPEG ABI')
    lib = c.CDLL(Image.core.__file__)
    P, I, U, B = c.c_void_p, c.c_int, c.c_uint, c.c_ubyte
    class Message(c.Union):
        _fields_ = [('integers', I * 8), ('text', c.c_char * 80)]
    class Error(c.Structure):
        _fields_ = [(n, P) for n in ('error_exit', 'emit_message', 'output_message', 'format_message', 'reset_error_mgr')] + [
            ('msg_code', I), ('msg_parm', Message), ('trace_level', I), ('num_warnings', c.c_long),
            ('jpeg_message_table', P), ('last_jpeg_message', I), ('addon_message_table', P),
            ('first_addon_message', I), ('last_addon_message', I)]
    class Decoder(c.Structure):
        _fields_ = [(n, P) for n in ('err', 'mem', 'progress', 'client_data')] + [
            ('is_decompressor', I), ('global_state', I), ('src', P),
            ('image_width', U), ('image_height', U), ('num_components', I),
            ('jpeg_color_space', I), ('out_color_space', I), ('scale_num', U), ('scale_denom', U),
            ('output_gamma', c.c_double)] + [(n, I) for n in (
            'buffered_image', 'raw_data_out', 'dct_method', 'do_fancy_upsampling', 'do_block_smoothing',
            'quantize_colors', 'dither_mode', 'two_pass_quantize', 'desired_number_of_colors',
            'enable_1pass_quant', 'enable_external_quant', 'enable_2pass_quant')] + [
            ('output_width', U), ('output_height', U), ('out_color_components', I), ('output_components', I),
            ('rec_outbuf_height', I), ('actual_number_of_colors', I), ('colormap', P),
            ('output_scanline', U), ('input_scan_number', I), ('input_iMCU_row', U),
            ('output_scan_number', I), ('output_iMCU_row', U), ('coef_bits', P),
            ('quant_tbl_ptrs', P * 4), ('dc_huff_tbl_ptrs', P * 4), ('ac_huff_tbl_ptrs', P * 4),
            ('data_precision', I), ('comp_info', P), ('progressive_mode', I), ('arith_code', I),
            ('arith_dc_L', B * 16), ('arith_dc_U', B * 16), ('arith_ac_K', B * 16),
            ('restart_interval', U), ('saw_JFIF_marker', I), ('JFIF_major_version', B),
            ('JFIF_minor_version', B), ('density_unit', B), ('X_density', c.c_ushort), ('Y_density', c.c_ushort),
            ('saw_Adobe_marker', I), ('Adobe_transform', B), ('CCIR601_sampling', I), ('marker_list', P),
            ('max_h_samp_factor', I), ('max_v_samp_factor', I), ('min_DCT_scaled_size', I),
            ('total_iMCU_rows', U), ('sample_range_limit', P), ('comps_in_scan', I), ('cur_comp_info', P * 4),
            ('MCUs_per_row', U), ('MCU_rows_in_scan', U), ('blocks_in_MCU', I), ('MCU_membership', I * 10),
            ('Ss', I), ('Se', I), ('Ah', I), ('Al', I), ('unread_marker', I)] + [(n, P) for n in (
            'master', 'main', 'coef', 'post', 'inputctl', 'marker', 'entropy', 'idct', 'upsample', 'cconvert', 'cquantize')]
    fatal_type = c.CFUNCTYPE(None, P)
    warning_type = c.CFUNCTYPE(None, P, I)
    @fatal_type
    def fatal(_info):
        os._exit(1)  # libjpeg error_exit MUST NOT return; process owns resources.
    @warning_type
    def warning(_info, level):
        if level < 0:
            os._exit(1)
    lib.jpeg_std_error.argtypes = [c.POINTER(Error)]
    lib.jpeg_std_error.restype = c.POINTER(Error)
    lib.jpeg_CreateDecompress.argtypes = [c.POINTER(Decoder), I, c.c_size_t]
    lib.jpeg_CreateDecompress.restype = None
    lib.jpeg_mem_src.argtypes = [c.POINTER(Decoder), P, c.c_ulong]
    lib.jpeg_mem_src.restype = None
    lib.jpeg_read_header.argtypes = [c.POINTER(Decoder), I]
    lib.jpeg_read_header.restype = I
    lib.jpeg_read_coefficients.argtypes = [c.POINTER(Decoder)]
    lib.jpeg_read_coefficients.restype = P
    lib.jpeg_finish_decompress.argtypes = [c.POINTER(Decoder)]
    lib.jpeg_finish_decompress.restype = I
    lib.jpeg_destroy_decompress.argtypes = [c.POINTER(Decoder)]
    lib.jpeg_destroy_decompress.restype = None
    error = Error(); decoder = Decoder()
    lib.jpeg_std_error(c.byref(error))
    error.error_exit = c.cast(fatal, P).value
    error.emit_message = c.cast(warning, P).value
    error.output_message = c.cast(fatal, P).value
    decoder.err = c.cast(c.pointer(error), P).value
    lib.jpeg_CreateDecompress(c.byref(decoder), 62, c.sizeof(Decoder))
    try:
        payload = c.create_string_buffer(data)
        lib.jpeg_mem_src(c.byref(decoder), payload, len(data))
        if lib.jpeg_read_header(c.byref(decoder), 1) != 1:
            raise ValueError('Invalid JPEG header')
        if not lib.jpeg_read_coefficients(c.byref(decoder)):
            raise ValueError('Invalid JPEG entropy')
        if not lib.jpeg_finish_decompress(c.byref(decoder)) or error.num_warnings:
            raise ValueError('Invalid JPEG end')
    finally:
        lib.jpeg_destroy_decompress(c.byref(decoder))


def main() -> int:
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 1))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
    # libjpeg can report corrupt entropy via native stderr while load succeeds.
    # Keep that diagnostic in capped anonymous memory, never logs or disk.
    diagnostics = os.memfd_create('image-decode-diagnostics', os.MFD_CLOEXEC)
    os.dup2(diagnostics, 2)
    try:
        policy = runpy.run_path(str(Path(__file__).with_name('image_validation.py')))
        data = sys.stdin.buffer.read(policy['MAX_ENCODED_BYTES'] + 1)
        fmt, _ = policy['_admit'](data, sys.argv[1])
        policy['verify_structure'](data, sys.argv[1])
        if fmt == 'JPEG':
            strict_jpeg_entropy(data)
        from PIL import Image, ImageFile
        if ImageFile.LOAD_TRUNCATED_IMAGES:
            return 1
        # This is a disposable single-request interpreter, not shared mutable
        # per-request Pillow globals. No budget overrides or warning suppression.
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=[fmt]) as image:
                if image.format != fmt:
                    return 1
                image.verify()
            with Image.open(io.BytesIO(data), formats=[fmt]) as image:
                pixels = decoded = frames = 0
                canvas = image.size
                while True:
                    width, height = image.size
                    policy['dimensions'](width, height)
                    frames += 1
                    pixels += width * height
                    # Pillow's internal RGB core uses four bytes per pixel;
                    # count a conservative minimum of four for every mode.
                    decoded += width * height * max(4, len(image.getbands()))
                    if (frames > policy['MAX_FRAMES'] or pixels > policy['MAX_PIXELS']
                            or decoded > policy['MAX_DECODED_BYTES'] or image.size != canvas):
                        return 1
                    if fmt != 'GIF' and frames != 1:
                        return 1
                    image.load()
                    try:
                        image.seek(frames)
                    except EOFError:
                        break
        if os.fstat(diagnostics).st_size:
            return 1
        sys.stdout.buffer.write(b'OK')
        return 0
    except Exception:
        # No raw exception/metadata/codec diagnostics leave this process.
        return 1
    finally:
        os.close(diagnostics)


if __name__ == '__main__':
    raise SystemExit(main())
