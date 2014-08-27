# This file is part of Xpra.
# Copyright (C) 2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

from xpra.log import Logger
log = Logger("encoder", "webp")


from libc.stdint cimport uint8_t, uint32_t

cdef extern from "string.h":
    void * memset ( void * ptr, int value, size_t num )

cdef extern from *:
    ctypedef unsigned long size_t

cdef extern from "stdlib.h":
    void free(void *ptr)


cdef extern from "../buffers/buffers.h":
    int    object_as_buffer(object obj, const void ** buffer, Py_ssize_t * buffer_len)

cdef extern from "webp/encode.h":

    int WebPGetEncoderVersion()

    ctypedef int WebPImageHint
    # WebPImageHint:
    WebPImageHint WEBP_HINT_DEFAULT     #default preset
    WebPImageHint WEBP_HINT_PICTURE     #digital picture, like portrait, inner shot
    WebPImageHint WEBP_HINT_PHOTO       #outdoor photograph, with natural lighting
    WebPImageHint WEBP_HINT_GRAPH       #Discrete tone image (graph, map-tile etc).

    ctypedef int WebPPreset
    WebPPreset WEBP_PRESET_DEFAULT      #default preset.
    WebPPreset WEBP_PRESET_PICTURE      #digital picture, like portrait, inner shot
    WebPPreset WEBP_PRESET_PHOTO        #outdoor photograph, with natural lighting
    WebPPreset WEBP_PRESET_DRAWING      #hand or line drawing, with high-contrast details
    WebPPreset WEBP_PRESET_ICON         #small-sized colorful images
    WebPPreset WEBP_PRESET_TEXT         #text-like

    ctypedef int WebPEncCSP
    #chroma sampling
    WebPEncCSP WEBP_YUV420              #4:2:0
    WebPEncCSP WEBP_YUV422              #4:2:2
    WebPEncCSP WEBP_YUV444              #4:4:4
    WebPEncCSP WEBP_YUV400              #grayscale
    WebPEncCSP WEBP_CSP_UV_MASK         #bit-mask to get the UV sampling factors
    WebPEncCSP WEBP_YUV420A
    WebPEncCSP WEBP_YUV422A
    WebPEncCSP WEBP_YUV444A
    WebPEncCSP WEBP_YUV400A             #grayscale + alpha
    WebPEncCSP WEBP_CSP_ALPHA_BIT       #bit that is set if alpha is present

    ctypedef int WebPEncodingError
    WebPEncodingError VP8_ENC_OK
    WebPEncodingError VP8_ENC_ERROR_OUT_OF_MEMORY
    WebPEncodingError VP8_ENC_ERROR_BITSTREAM_OUT_OF_MEMORY
    WebPEncodingError VP8_ENC_ERROR_NULL_PARAMETER
    WebPEncodingError VP8_ENC_ERROR_INVALID_CONFIGURATION
    WebPEncodingError VP8_ENC_ERROR_BAD_DIMENSION
    WebPEncodingError VP8_ENC_ERROR_PARTITION0_OVERFLOW
    WebPEncodingError VP8_ENC_ERROR_PARTITION_OVERFLOW
    WebPEncodingError VP8_ENC_ERROR_BAD_WRITE
    WebPEncodingError VP8_ENC_ERROR_FILE_TOO_BIG
    WebPEncodingError VP8_ENC_ERROR_USER_ABORT
    WebPEncodingError VP8_ENC_ERROR_LAST

    ctypedef struct WebPConfig:
        int lossless                    #Lossless encoding (0=lossy(default), 1=lossless).
        float quality                   #between 0 (smallest file) and 100 (biggest)
        int method                      #quality/speed trade-off (0=fast, 6=slower-better)

        WebPImageHint image_hint        #Hint for image type (lossless only for now).

        #Parameters related to lossy compression only:
        int target_size                 #if non-zero, set the desired target size in bytes.
                                        #Takes precedence over the 'compression' parameter.
        float target_PSNR               #if non-zero, specifies the minimal distortion to
                                        #try to achieve. Takes precedence over target_size.
        int segments                    #maximum number of segments to use, in [1..4]
        int sns_strength                #Spatial Noise Shaping. 0=off, 100=maximum.
        int filter_strength             #range: [0 = off .. 100 = strongest]
        int filter_sharpness            #range: [0 = off .. 7 = least sharp]
        int filter_type                 #filtering type: 0 = simple, 1 = strong (only used
                                        #if filter_strength > 0 or autofilter > 0)
        int autofilter                  #Auto adjust filter's strength [0 = off, 1 = on]
        int alpha_compression           #Algorithm for encoding the alpha plane (0 = none,
                                        #1 compressed with WebP lossless). Default is 1.
        int alpha_filtering             #Predictive filtering method for alpha plane.
                                        #0: none, 1: fast, 2: best. Default if 1.
        int alpha_quality               #Between 0 (smallest size) and 100 (lossless).
                                        #Default is 100.
        int _pass                        #number of entropy-analysis passes (in [1..10]).

        int show_compressed             #if true, export the compressed picture back.
                                        #In-loop filtering is not applied.
        int preprocessing               #preprocessing filter (0=none, 1=segment-smooth)
        int partitions                  #log2(number of token partitions) in [0..3]. Default
                                        #is set to 0 for easier progressive decoding.
        int partition_limit             #quality degradation allowed to fit the 512k limit
                                        #on prediction modes coding (0: no degradation,
                                        #100: maximum possible degradation).
        int emulate_jpeg_size           #If true, compression parameters will be remapped
                                        #to better match the expected output size from
                                        #JPEG compression. Generally, the output size will
                                        #be similar but the degradation will be lower.
        int thread_level                #If non-zero, try and use multi-threaded encoding.
        int low_memory                  #If set, reduce memory usage (but increase CPU use).

        uint32_t pad[5]                 #padding for later use

    ctypedef struct WebPMemoryWriter:
        uint8_t* mem                    #final buffer (of size 'max_size', larger than 'size').
        size_t   size                   #final size
        size_t   max_size               #total capacity
        uint32_t pad[1]

    ctypedef void *WebPWriterFunction
    ctypedef void *WebPProgressHook
    ctypedef void *WebPAuxStats

    ctypedef struct WebPPicture:
        #   INPUT
        # Main flag for encoder selecting between ARGB or YUV input.
        # It is recommended to use ARGB input (*argb, argb_stride) for lossless
        # compression, and YUV input (*y, *u, *v, etc.) for lossy compression
        # since these are the respective native colorspace for these formats.
        int use_argb

        # YUV input (mostly used for input to lossy compression)
        WebPEncCSP colorspace           #colorspace: should be YUV420 for now (=Y'CbCr).
        int width, height               #dimensions (less or equal to WEBP_MAX_DIMENSION)
        uint8_t *y
        uint8_t *u
        uint8_t *v
        int y_stride, uv_stride         #luma/chroma strides.
        uint8_t* a                      #pointer to the alpha plane
        int a_stride                    #stride of the alpha plane
        uint32_t pad1[2]                #padding for later use

        # ARGB input (mostly used for input to lossless compression)
        uint32_t* argb                  #Pointer to argb (32 bit) plane.
        int argb_stride                 #This is stride in pixels units, not bytes.
        uint32_t pad2[3]                #padding for later use

        #   OUTPUT
        # Byte-emission hook, to store compressed bytes as they are ready.
        WebPWriterFunction writer       #can be NULL
        void* custom_ptr                #can be used by the writer.

        # map for extra information (only for lossy compression mode)
        int extra_info_type             #1: intra type, 2: segment, 3: quant
                                        #4: intra-16 prediction mode,
                                        #5: chroma prediction mode,
                                        #6: bit cost, 7: distortion
        uint8_t* extra_info             #if not NULL, points to an array of size
                                        # ((width + 15) / 16) * ((height + 15) / 16) that
                                        #will be filled with a macroblock map, depending
                                        #on extra_info_type.

        #   STATS AND REPORTS
        # Pointer to side statistics (updated only if not NULL)
        WebPAuxStats* stats

        # Error code for the latest error encountered during encoding
        WebPEncodingError error_code

        #If not NULL, report progress during encoding.
        WebPProgressHook progress_hook

        void* user_data                 #this field is free to be set to any value and
                                        #used during callbacks (like progress-report e.g.).

        uint32_t pad3[3]                #padding for later use

        # Unused for now: original samples (for non-YUV420 modes)
        uint8_t *u0
        uint8_t *v0
        int uv0_stride

        uint32_t pad4[7]                #padding for later use

        # PRIVATE FIELDS
        void* memory_                   #row chunk of memory for yuva planes
        void* memory_argb_              #and for argb too.
        void* pad5[2]                   #padding for later use

    void WebPMemoryWriterInit(WebPMemoryWriter* writer)
    int WebPMemoryWrite(const uint8_t* data, size_t data_size, const WebPPicture* picture)

    int WebPConfigInit(WebPConfig* config)
    int WebPConfigPreset(WebPConfig* config, WebPPreset preset, float quality)
    int WebPValidateConfig(const WebPConfig* config)
    int WebPPictureInit(WebPPicture* picture)
    void WebPPictureFree(WebPPicture* picture)

    # Colorspace conversion function to import RGB samples.
    # Previous buffer will be free'd, if any.
    # *rgb buffer should have a size of at least height * rgb_stride.
    # Returns false in case of memory error.
    int WebPPictureImportRGB(WebPPicture* picture, const uint8_t* rgb, int rgb_stride)
    # Same, but for RGBA buffer.
    int WebPPictureImportRGBA(WebPPicture* picture, const uint8_t* rgba, int rgba_stride)
    # Same, but for RGBA buffer. Imports the RGB direct from the 32-bit format
    # input buffer ignoring the alpha channel. Avoids needing to copy the data
    # to a temporary 24-bit RGB buffer to import the RGB only.
    int WebPPictureImportRGBX(WebPPicture* picture, const uint8_t* rgbx, int rgbx_stride)

    # Variants of the above, but taking BGR(A|X) input.
    int WebPPictureImportBGR(WebPPicture* picture, const uint8_t* bgr, int bgr_stride)
    int WebPPictureImportBGRA(WebPPicture* picture, const uint8_t* bgra, int bgra_stride)
    int WebPPictureImportBGRX(WebPPicture* picture, const uint8_t* bgrx, int bgrx_stride)

    # Converts picture->argb data to the YUVA format specified by 'colorspace'.
    # Upon return, picture->use_argb is set to false. The presence of real
    # non-opaque transparent values is detected, and 'colorspace' will be
    # adjusted accordingly. Note that this method is lossy.
    # Returns false in case of error.
    int WebPPictureARGBToYUVA(WebPPicture* picture, WebPEncCSP colorspace)

    # Converts picture->yuv to picture->argb and sets picture->use_argb to true.
    # The input format must be YUV_420 or YUV_420A.
    # Note that the use of this method is discouraged if one has access to the
    # raw ARGB samples, since using YUV420 is comparatively lossy. Also, the
    # conversion from YUV420 to ARGB incurs a small loss too.
    # Returns false in case of error.
    int WebPPictureYUVAToARGB(WebPPicture* picture)

    # Helper function: given a width x height plane of YUV(A) samples
    # (with stride 'stride'), clean-up the YUV samples under fully transparent
    # area, to help compressibility (no guarantee, though).
    void WebPCleanupTransparentArea(WebPPicture* picture)

    # Scan the picture 'picture' for the presence of non fully opaque alpha values.
    # Returns true in such case. Otherwise returns false (indicating that the
    # alpha plane can be ignored altogether e.g.).
    int WebPPictureHasTransparency(const WebPPicture* picture)

    # Main encoding call, after config and picture have been initialized.
    # 'picture' must be less than 16384x16384 in dimension (cf WEBP_MAX_DIMENSION),
    # and the 'config' object must be a valid one.
    # Returns false in case of error, true otherwise.
    # In case of error, picture->error_code is updated accordingly.
    # 'picture' can hold the source samples in both YUV(A) or ARGB input, depending
    # on the value of 'picture->use_argb'. It is highly recommended to use
    # the former for lossy encoding, and the latter for lossless encoding
    # (when config.lossless is true). Automatic conversion from one format to
    # another is provided but they both incur some loss.
    int WebPEncode(const WebPConfig* config, WebPPicture* picture)


ERROR_TO_NAME = {
#VP8_ENC_OK
                VP8_ENC_ERROR_OUT_OF_MEMORY             : "memory error allocating objects",
                VP8_ENC_ERROR_BITSTREAM_OUT_OF_MEMORY   : "memory error while flushing bits",
                VP8_ENC_ERROR_NULL_PARAMETER            : "a pointer parameter is NULL",
                VP8_ENC_ERROR_INVALID_CONFIGURATION     : "configuration is invalid",
                VP8_ENC_ERROR_BAD_DIMENSION             : "picture has invalid width/height",
                VP8_ENC_ERROR_PARTITION0_OVERFLOW       : "partition is bigger than 512k",
                VP8_ENC_ERROR_PARTITION_OVERFLOW        : "partition is bigger than 16M",
                VP8_ENC_ERROR_BAD_WRITE                 : "error while flushing bytes",
                VP8_ENC_ERROR_FILE_TOO_BIG              : "file is bigger than 4G",
                VP8_ENC_ERROR_USER_ABORT                : "abort request by user",
                }

def get_encodings():
    return ["webp"]

def get_version():
    cdef int version = WebPGetEncoderVersion()
    log("WebPGetEncoderVersion()=%#x", version)
    return (version >> 16) & 0xff, (version >> 8) & 0xff, version & 0xff

def webp_check(int ret):
    if ret==0:
        return
    err = ERROR_TO_NAME.get(ret, ret)
    raise Exception("error: %s" % err)

cdef float fclamp(int v):
    if v<0:
        v = 0
    elif v>100:
        v = 100
    return <float> v

def compress(pixels, width, height, stride=0, quality=50, speed=50, has_alpha=False):
    cdef uint8_t *pic_buf
    cdef Py_ssize_t pic_buf_len = 0
    cdef WebPConfig config
    cdef int ret
    cdef int i, c
    #presets: DEFAULT, PICTURE, PHOTO, DRAWING, ICON, TEXT
    cdef WebPPreset preset = WEBP_PRESET_TEXT
    if width*height<8192:
        preset = WEBP_PRESET_ICON

    assert object_as_buffer(pixels, <const void**> &pic_buf, &pic_buf_len)==0
    log("webp.compress(%s bytes, %s, %s, %s, %s, %s, %s) buf=%#x", len(pixels), width, height, stride, quality, speed, has_alpha, <unsigned long> pic_buf)

    if not has_alpha:
        #ensure webp will not decide to encode the alpha channel
        #(this is stupid: we should be able to pass a flag instead)
        i = 3
        c = (stride or width) * height * 4
        while i<c:
            pic_buf[i] = 0xff
            i += 4

    ret = WebPConfigPreset(&config, preset, fclamp(quality))
    if not ret:
        raise Exception("failed to initialise webp config")

    #tune it:
    config.lossless = quality>=100
    if config.lossless:
        config.quality = fclamp(100-speed)
    else:
        config.quality = fclamp(quality)
    config.method = max(0, min(6, 6-speed/16))
    config.alpha_compression = int(quality>=90)
    config.alpha_filtering = max(0, min(2, speed/50)) * int(has_alpha)
    config.alpha_quality = quality * int(has_alpha)
    #hints: DEFAULT, PICTURE, PHOTO, GRAPH
    config.image_hint = WEBP_HINT_GRAPH

    log("webp.compress config: lossless=%s, quality=%s, method=%s, alpha=%s,%s,%s", config.lossless, config.quality, config.method,
                    config.alpha_compression, config.alpha_filtering, config.alpha_quality)
    #config.sns_strength = 90
    #config.filter_sharpness = 6
    ret = WebPValidateConfig(&config)
    if not ret:
        raise Exception("invalid webp configuration!")

    cdef WebPPicture pic
    ret = WebPPictureInit(&pic)
    if not ret:
        raise Exception("failed to initialise webp picture")

    cdef WebPMemoryWriter memory_writer
    WebPMemoryWriterInit(&memory_writer)

    memset(&pic, 0, sizeof(WebPPicture))
    pic.width = width
    pic.height = height
    pic.use_argb = True
    pic.argb = <uint32_t*> pic_buf
    pic.argb_stride = stride or width
    pic.writer = <WebPWriterFunction> WebPMemoryWrite
    pic.custom_ptr = <void*> &memory_writer
    ret = WebPEncode(&config, &pic)
    if not ret:
        raise Exception("WebPEncode failed: %s" % ERROR_TO_NAME.get(pic.error_code, pic.error_code))

    cdata = memory_writer.mem[:memory_writer.size]
    free(memory_writer.mem)
    WebPPictureFree(&pic)
    return cdata
