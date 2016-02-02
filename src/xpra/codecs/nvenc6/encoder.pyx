# This file is part of Xpra.
# Copyright (C) 2013-2015 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import binascii
import time
import os
import sys
import numpy
import array
from collections import deque

import pycuda
from pycuda import driver
from pycuda.driver import memcpy_htod
from pycuda.compiler import compile

from xpra.util import AtomicInteger, updict, engs, csv, pver
from xpra.os_util import _memoryview
from xpra.codecs.cuda_common.cuda_context import init_all_devices, get_devices, select_device, \
                get_cuda_info, get_pycuda_info, device_info, reset_state, \
                get_CUDA_function, record_device_failure, record_device_success, CUDA_ERRORS_INFO
from xpra.codecs.codec_constants import video_spec, TransientCodecException
from xpra.codecs.image_wrapper import ImageWrapper
from xpra.codecs.nv_util import get_nvidia_module_version, is_blacklisted

from xpra.log import Logger
from xpra.codecs.nvenc4.encoder import context_counter
log = Logger("encoder", "nvenc")

import ctypes
from ctypes import cdll as loader, POINTER
from libc.stdint cimport uint8_t, uint16_t, uint32_t, int32_t, uint64_t

DESIRED_PRESET = os.environ.get("XPRA_NVENC_PRESET", "")
#NVENC requires compute capability value 0x30 or above:
cdef int MIN_COMPUTE = 0x30

cdef int YUV444_THRESHOLD = int(os.environ.get("XPRA_NVENC_YUV444_THRESHOLD", "85"))
cdef int LOSSLESS_THRESHOLD = int(os.environ.get("XPRA_NVENC_LOSSLESS_THRESHOLD", "100"))
cdef int DEBUG_API = int(os.environ.get("XPRA_NVENC_DEBUG_API", "0")=="1")

cdef int QP_MAX_VALUE = 51   #newer versions of ffmpeg can decode up to 63


cdef inline int MIN(int a, int b):
    if a<=b:
        return a
    return b
cdef inline int MAX(int a, int b):
    if a>=b:
        return a
    return b


cdef extern from "string.h":
    void* memset(void * ptr, int value, size_t num)
    void* memcpy(void * destination, void * source, size_t num)

cdef extern from "stdlib.h":
    void* malloc(size_t __size)
    void free(void* mem)


CUresult = ctypes.c_int
CUcontext = ctypes.c_void_p


cdef extern from "nvEncodeAPI.h":
    ctypedef int NVENCSTATUS
    ctypedef void* NV_ENC_INPUT_PTR
    ctypedef void* NV_ENC_OUTPUT_PTR
    ctypedef void* NV_ENC_REGISTERED_PTR

    ctypedef enum NV_ENC_CAPS:
        NV_ENC_CAPS_NUM_MAX_BFRAMES
        NV_ENC_CAPS_SUPPORTED_RATECONTROL_MODES
        NV_ENC_CAPS_SUPPORT_FIELD_ENCODING
        NV_ENC_CAPS_SUPPORT_MONOCHROME
        NV_ENC_CAPS_SUPPORT_FMO
        NV_ENC_CAPS_SUPPORT_QPELMV
        NV_ENC_CAPS_SUPPORT_BDIRECT_MODE
        NV_ENC_CAPS_SUPPORT_CABAC
        NV_ENC_CAPS_SUPPORT_ADAPTIVE_TRANSFORM
        NV_ENC_CAPS_NUM_MAX_TEMPORAL_LAYERS
        NV_ENC_CAPS_SUPPORT_HIERARCHICAL_PFRAMES
        NV_ENC_CAPS_SUPPORT_HIERARCHICAL_BFRAMES
        NV_ENC_CAPS_LEVEL_MAX
        NV_ENC_CAPS_LEVEL_MIN
        NV_ENC_CAPS_SEPARATE_COLOUR_PLANE
        NV_ENC_CAPS_WIDTH_MAX
        NV_ENC_CAPS_HEIGHT_MAX
        NV_ENC_CAPS_SUPPORT_TEMPORAL_SVC
        NV_ENC_CAPS_SUPPORT_DYN_RES_CHANGE
        NV_ENC_CAPS_SUPPORT_DYN_BITRATE_CHANGE
        NV_ENC_CAPS_SUPPORT_DYN_FORCE_CONSTQP
        NV_ENC_CAPS_SUPPORT_DYN_RCMODE_CHANGE
        NV_ENC_CAPS_SUPPORT_SUBFRAME_READBACK
        NV_ENC_CAPS_SUPPORT_CONSTRAINED_ENCODING
        NV_ENC_CAPS_SUPPORT_INTRA_REFRESH
        NV_ENC_CAPS_SUPPORT_CUSTOM_VBV_BUF_SIZE
        NV_ENC_CAPS_SUPPORT_DYNAMIC_SLICE_MODE
        NV_ENC_CAPS_SUPPORT_REF_PIC_INVALIDATION
        NV_ENC_CAPS_PREPROC_SUPPORT
        NV_ENC_CAPS_ASYNC_ENCODE_SUPPORT
        NV_ENC_CAPS_MB_NUM_MAX
        NV_ENC_CAPS_EXPOSED_COUNT
        NV_ENC_CAPS_SUPPORT_YUV444_ENCODE
        NV_ENC_CAPS_SUPPORT_LOSSLESS_ENCODE
        NV_ENC_CAPS_SUPPORT_SAO
        NV_ENC_CAPS_SUPPORT_MEONLY_MODE

    ctypedef enum NV_ENC_DEVICE_TYPE:
        NV_ENC_DEVICE_TYPE_DIRECTX
        NV_ENC_DEVICE_TYPE_CUDA

    ctypedef enum NV_ENC_INPUT_RESOURCE_TYPE:
        NV_ENC_INPUT_RESOURCE_TYPE_DIRECTX
        NV_ENC_INPUT_RESOURCE_TYPE_CUDADEVICEPTR
        NV_ENC_INPUT_RESOURCE_TYPE_CUDAARRAY

    ctypedef enum NV_ENC_MEMORY_HEAP:
        NV_ENC_MEMORY_HEAP_AUTOSELECT
        NV_ENC_MEMORY_HEAP_VID
        NV_ENC_MEMORY_HEAP_SYSMEM_CACHED
        NV_ENC_MEMORY_HEAP_SYSMEM_UNCACHED

    ctypedef enum NV_ENC_H264_ENTROPY_CODING_MODE:
        NV_ENC_H264_ENTROPY_CODING_MODE_AUTOSELECT
        NV_ENC_H264_ENTROPY_CODING_MODE_CABAC
        NV_ENC_H264_ENTROPY_CODING_MODE_CAVLC

    ctypedef enum NV_ENC_STEREO_PACKING_MODE:
        NV_ENC_STEREO_PACKING_MODE_NONE
        NV_ENC_STEREO_PACKING_MODE_CHECKERBOARD
        NV_ENC_STEREO_PACKING_MODE_COLINTERLEAVE
        NV_ENC_STEREO_PACKING_MODE_ROWINTERLEAVE
        NV_ENC_STEREO_PACKING_MODE_SIDEBYSIDE
        NV_ENC_STEREO_PACKING_MODE_TOPBOTTOM
        NV_ENC_STEREO_PACKING_MODE_FRAMESEQ

    ctypedef enum NV_ENC_H264_FMO_MODE:
        NV_ENC_H264_FMO_AUTOSELECT
        NV_ENC_H264_FMO_ENABLE
        NV_ENC_H264_FMO_DISABLE

    ctypedef enum NV_ENC_H264_BDIRECT_MODE:
        NV_ENC_H264_BDIRECT_MODE_AUTOSELECT
        NV_ENC_H264_BDIRECT_MODE_DISABLE
        NV_ENC_H264_BDIRECT_MODE_TEMPORAL
        NV_ENC_H264_BDIRECT_MODE_SPATIAL

    ctypedef enum NV_ENC_H264_ADAPTIVE_TRANSFORM_MODE:
        NV_ENC_H264_ADAPTIVE_TRANSFORM_AUTOSELECT
        NV_ENC_H264_ADAPTIVE_TRANSFORM_DISABLE
        NV_ENC_H264_ADAPTIVE_TRANSFORM_ENABLE

    ctypedef enum NV_ENC_PARAMS_FRAME_FIELD_MODE:
        NV_ENC_PARAMS_FRAME_FIELD_MODE_FRAME
        NV_ENC_PARAMS_FRAME_FIELD_MODE_FIELD
        NV_ENC_PARAMS_FRAME_FIELD_MODE_MBAFF

    ctypedef enum NV_ENC_BUFFER_FORMAT:
        NV_ENC_BUFFER_FORMAT_UNDEFINED
        NV_ENC_BUFFER_FORMAT_NV12_PL
        NV_ENC_BUFFER_FORMAT_YV12_PL
        NV_ENC_BUFFER_FORMAT_IYUV_PL
        NV_ENC_BUFFER_FORMAT_YUV444_PL
        NV_ENC_BUFFER_FORMAT_ARGB
        NV_ENC_BUFFER_FORMAT_ARGB10
        NV_ENC_BUFFER_FORMAT_AYUV

    ctypedef enum NV_ENC_PIC_FLAGS:
        NV_ENC_PIC_FLAG_FORCEINTRA
        NV_ENC_PIC_FLAG_FORCEIDR
        NV_ENC_PIC_FLAG_OUTPUT_SPSPPS
        NV_ENC_PIC_FLAG_EOS

    ctypedef enum NV_ENC_PIC_STRUCT:
        NV_ENC_PIC_STRUCT_FRAME
        NV_ENC_PIC_STRUCT_FIELD_TOP_BOTTOM
        NV_ENC_PIC_STRUCT_FIELD_BOTTOM_TOP

    ctypedef enum NV_ENC_PIC_TYPE:
        NV_ENC_PIC_TYPE_P
        NV_ENC_PIC_TYPE_B
        NV_ENC_PIC_TYPE_I
        NV_ENC_PIC_TYPE_IDR
        NV_ENC_PIC_TYPE_BI
        NV_ENC_PIC_TYPE_SKIPPED
        NV_ENC_PIC_TYPE_INTRA_REFRESH
        NV_ENC_PIC_TYPE_UNKNOWN

    ctypedef enum NV_ENC_SLICE_TYPE:
        NV_ENC_SLICE_TYPE_DEFAULT
        NV_ENC_SLICE_TYPE_I
        NV_ENC_SLICE_TYPE_UNKNOWN

    ctypedef enum  NV_ENC_MV_PRECISION:
        NV_ENC_MV_PRECISION_FULL_PEL
        NV_ENC_MV_PRECISION_HALF_PEL
        NV_ENC_MV_PRECISION_QUARTER_PEL

    ctypedef enum NV_ENC_LEVEL:
        NV_ENC_LEVEL_AUTOSELECT
        NV_ENC_LEVEL_H264_1
        NV_ENC_LEVEL_H264_1b
        NV_ENC_LEVEL_H264_11
        NV_ENC_LEVEL_H264_12
        NV_ENC_LEVEL_H264_13
        NV_ENC_LEVEL_H264_2
        NV_ENC_LEVEL_H264_21
        NV_ENC_LEVEL_H264_22
        NV_ENC_LEVEL_H264_3
        NV_ENC_LEVEL_H264_31
        NV_ENC_LEVEL_H264_32
        NV_ENC_LEVEL_H264_4
        NV_ENC_LEVEL_H264_41
        NV_ENC_LEVEL_H264_42
        NV_ENC_LEVEL_H264_5
        NV_ENC_LEVEL_H264_51
        NV_ENC_LEVEL_H264_52
        NV_ENC_LEVEL_HEVC_1
        NV_ENC_LEVEL_HEVC_2
        NV_ENC_LEVEL_HEVC_21
        NV_ENC_LEVEL_HEVC_3
        NV_ENC_LEVEL_HEVC_31
        NV_ENC_LEVEL_HEVC_4
        NV_ENC_LEVEL_HEVC_41
        NV_ENC_LEVEL_HEVC_5
        NV_ENC_LEVEL_HEVC_51
        NV_ENC_LEVEL_HEVC_52
        NV_ENC_LEVEL_HEVC_6
        NV_ENC_LEVEL_HEVC_61
        NV_ENC_LEVEL_HEVC_62        

    ctypedef enum NV_ENC_PARAMS_RC_MODE:
        NV_ENC_PARAMS_RC_CONSTQP            #Constant QP mode
        NV_ENC_PARAMS_RC_VBR                #Variable bitrate mode
        NV_ENC_PARAMS_RC_CBR                #Constant bitrate mode
        NV_ENC_PARAMS_RC_VBR_MINQP          #Variable bitrate mode with MinQP
        NV_ENC_PARAMS_RC_2_PASS_QUALITY     #Multi pass encoding optimized for image quality and works only with low latency mode
        NV_ENC_PARAMS_RC_2_PASS_FRAMESIZE_CAP   #Multi pass encoding optimized for maintaining frame size and works only with low latency mode
        NV_ENC_PARAMS_RC_2_PASS_VBR         #Multi pass VBR
        NV_ENC_PARAMS_RC_CBR2               #(deprecated)

    ctypedef enum NV_ENC_HEVC_CUSIZE:
        NV_ENC_HEVC_CUSIZE_AUTOSELECT
        NV_ENC_HEVC_CUSIZE_8x8
        NV_ENC_HEVC_CUSIZE_16x16
        NV_ENC_HEVC_CUSIZE_32x32
        NV_ENC_HEVC_CUSIZE_64x64


    ctypedef struct NV_ENC_LOCK_BITSTREAM:
        uint32_t    version             #[in]: Struct version. Must be set to ::NV_ENC_LOCK_BITSTREAM_VER.
        uint32_t    doNotWait           #[in]: If this flag is set, the NvEncodeAPI interface will return buffer pointer even if operation is not completed. If not set, the call will block until operation completes.
        uint32_t    ltrFrame            #[out]: Flag indicating this frame is marked as LTR frame
        uint32_t    reservedBitFields   #[in]: Reserved bit fields and must be set to 0
        void*       outputBitstream     #[in]: Pointer to the bitstream buffer being locked.
        uint32_t*   sliceOffsets        #[in,out]: Array which receives the slice offsets. Currently used only when NV_ENC_CONFIG_H264::sliceMode == 3. Array size must be equal to NV_ENC_CONFIG_H264::sliceModeData.
        uint32_t    frameIdx            #[out]: Frame no. for which the bitstream is being retrieved.
        uint32_t    hwEncodeStatus      #[out]: The NvEncodeAPI interface status for the locked picture.
        uint32_t    numSlices           #[out]: Number of slices in the encoded picture. Will be reported only if NV_ENC_INITIALIZE_PARAMS::reportSliceOffsets set to 1.
        uint32_t    bitstreamSizeInBytes#[out]: Actual number of bytes generated and copied to the memory pointed by bitstreamBufferPtr.
        uint64_t    outputTimeStamp     #[out]: Presentation timestamp associated with the encoded output.
        uint64_t    outputDuration      #[out]: Presentation duration associates with the encoded output.
        void*       bitstreamBufferPtr  #[out]: Pointer to the generated output bitstream. Client should allocate sufficiently large buffer to hold the encoded output. Client is responsible for managing this memory.
        NV_ENC_PIC_TYPE     pictureType #[out]: Picture type of the encoded picture.
        NV_ENC_PIC_STRUCT   pictureStruct   #[out]: Structure of the generated output picture.
        uint32_t    frameAvgQP          #[out]: Average QP of the frame.
        uint32_t    frameSatd           #[out]: Total SATD cost for whole frame.
        uint32_t    ltrFrameIdx         #[out]: Frame index associated with this LTR frame.
        uint32_t    ltrFrameBitmap      #[out]: Bitmap of LTR frames indices which were used for encoding this frame. Value of 0 if no LTR frames were used.
        uint32_t    reserved[236]       #[in]: Reserved and must be set to 0
        void*       reserved2[64]       #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_LOCK_INPUT_BUFFER:
        uint32_t    version             #[in]:  Struct version. Must be set to ::NV_ENC_LOCK_INPUT_BUFFER_VER.
        uint32_t    doNotWait           #[in]:  Set to 1 to make ::NvEncLockInputBuffer() a unblocking call. If the encoding is not completed, driver will return ::NV_ENC_ERR_ENCODER_BUSY error code.
        uint32_t    reservedBitFields   #[in]:  Reserved bitfields and must be set to 0
        NV_ENC_INPUT_PTR inputBuffer    #[in]:  Pointer to the input buffer to be locked, client should pass the pointer obtained from ::NvEncCreateInputBuffer() or ::NvEncMapInputResource API.
        void*       bufferDataPtr       #[out]: Pointed to the locked input buffer data. Client can only access input buffer using the \p bufferDataPtr.
        uint32_t    pitch               #[out]: Pitch of the locked input buffer.
        uint32_t    reserved1[251]      #[in]:  Reserved and must be set to 0
        void*       reserved2[64]       #[in]:  Reserved and must be set to NULL

    ctypedef struct NV_ENC_STAT:
        uint32_t    version             #[in]:  Struct version. Must be set to ::NV_ENC_STAT_VER.
        uint32_t    reserved            #[in]:  Reserved and must be set to 0
        NV_ENC_OUTPUT_PTR outputBitStream   #[out]: Specifies the pointer to output bitstream.
        uint32_t    bitStreamSize       #[out]: Size of generated bitstream in bytes.
        uint32_t    picType             #[out]: Picture type of encoded picture. See ::NV_ENC_PIC_TYPE.
        uint32_t    lastValidByteOffset #[out]: Offset of last valid bytes of completed bitstream
        uint32_t    sliceOffsets[16]    #[out]: Offsets of each slice
        uint32_t    picIdx              #[out]: Picture number
        uint32_t    reserved1[233]      #[in]:  Reserved and must be set to 0
        void*       reserved2[64]       #[in]:  Reserved and must be set to NULL

    ctypedef struct NV_ENC_SEQUENCE_PARAM_PAYLOAD:
        pass
    ctypedef struct NV_ENC_EVENT_PARAMS:
        pass
    ctypedef struct NV_ENC_MAP_INPUT_RESOURCE:
        uint32_t    version             #[in]:  Struct version. Must be set to ::NV_ENC_MAP_INPUT_RESOURCE_VER.
        uint32_t    subResourceIndex    #[in]:  Deprecated. Do not use.
        void*       inputResource       #[in]:  Deprecated. Do not use.
        NV_ENC_REGISTERED_PTR registeredResource    #[in]:  The Registered resource handle obtained by calling NvEncRegisterInputResource.
        NV_ENC_INPUT_PTR mappedResource #[out]: Mapped pointer corresponding to the registeredResource. This pointer must be used in NV_ENC_PIC_PARAMS::inputBuffer parameter in ::NvEncEncodePicture() API.
        NV_ENC_BUFFER_FORMAT mappedBufferFmt    #[out]: Buffer format of the outputResource. This buffer format must be used in NV_ENC_PIC_PARAMS::bufferFmt if client using the above mapped resource pointer.
        uint32_t    reserved1[251]      #[in]:  Reserved and must be set to 0.
        void*       reserved2[63]       #[in]:  Reserved and must be set to NULL
    ctypedef struct NV_ENC_REGISTER_RESOURCE:
        uint32_t    version             #[in]: Struct version. Must be set to ::NV_ENC_REGISTER_RESOURCE_VER.
        NV_ENC_INPUT_RESOURCE_TYPE  resourceType    #[in]: Specifies the type of resource to be registered. Supported values are ::NV_ENC_INPUT_RESOURCE_TYPE_DIRECTX, ::NV_ENC_INPUT_RESOURCE_TYPE_CUDADEVICEPTR.
        uint32_t    width               #[in]: Input buffer Width.
        uint32_t    height              #[in]: Input buffer Height.
        uint32_t    pitch               #[in]: Input buffer Pitch.
        uint32_t    subResourceIndex    #[in]: Subresource Index of the DirectX resource to be registered. Should eb set to 0 for other interfaces.
        void*       resourceToRegister  #[in]: Handle to the resource that is being registered.
        NV_ENC_REGISTERED_PTR   registeredResource  #[out]: Registered resource handle. This should be used in future interactions with the Nvidia Video Encoder Interface.
        NV_ENC_BUFFER_FORMAT    bufferFormat        #[in]: Buffer format of resource to be registered.
        uint32_t    reserved1[248]      #[in]: Reserved and must be set to 0.
        void*       reserved2[62]       #[in]: Reserved and must be set to NULL.

    ctypedef struct GUID:
        uint32_t Data1
        uint16_t Data2
        uint16_t Data3
        uint8_t  Data4[8]

    int NVENC_INFINITE_GOPLENGTH

    #Encode Codec GUIDS supported by the NvEncodeAPI interface.
    GUID NV_ENC_CODEC_H264_GUID
    GUID NV_ENC_CODEC_HEVC_GUID

    #Profiles:
    GUID NV_ENC_CODEC_PROFILE_AUTOSELECT_GUID
    GUID NV_ENC_H264_PROFILE_BASELINE_GUID
    GUID NV_ENC_H264_PROFILE_MAIN_GUID
    GUID NV_ENC_H264_PROFILE_HIGH_GUID
    GUID NV_ENC_H264_PROFILE_HIGH_444_GUID
    GUID NV_ENC_H264_PROFILE_STEREO_GUID
    GUID NV_ENC_H264_PROFILE_SVC_TEMPORAL_SCALABILTY
    GUID NV_ENC_H264_PROFILE_PROGRESSIVE_HIGH_GUID
    GUID NV_ENC_H264_PROFILE_CONSTRAINED_HIGH_GUID

    GUID NV_ENC_HEVC_PROFILE_MAIN_GUID

    #Presets:
    GUID NV_ENC_PRESET_DEFAULT_GUID
    GUID NV_ENC_PRESET_HP_GUID
    GUID NV_ENC_PRESET_HQ_GUID
    GUID NV_ENC_PRESET_BD_GUID
    #V3 ONLY PRESETS:
    GUID NV_ENC_PRESET_LOW_LATENCY_DEFAULT_GUID
    GUID NV_ENC_PRESET_LOW_LATENCY_HQ_GUID
    GUID NV_ENC_PRESET_LOW_LATENCY_HP_GUID
    #V4 ONLY PRESETS:
    GUID NV_ENC_PRESET_LOSSLESS_DEFAULT_GUID
    GUID NV_ENC_PRESET_LOSSLESS_HP_GUID

    ctypedef struct NV_ENC_CAPS_PARAM:
        uint32_t    version
        uint32_t    capsToQuery
        uint32_t    reserved[62]

    ctypedef struct NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS:
        uint32_t    version         #[in]: Struct version. Must be set to ::NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER.
        NV_ENC_DEVICE_TYPE deviceType   #[in]: (NV_ENC_DEVICE_TYPE) Specified the device Type
        void        *device         #[in]: Pointer to client device.
        GUID        *clientKeyPtr   #[in]: Pointer to a GUID key issued to the client.
        uint32_t    apiVersion      #[in]: API version. Should be set to NVENCAPI_VERSION.
        uint32_t    reserved1[253]  #[in]: Reserved and must be set to 0
        void        *reserved2[64]  #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_CREATE_INPUT_BUFFER:
        uint32_t    version         #[in]: Struct version. Must be set to ::NV_ENC_CREATE_INPUT_BUFFER_VER
        uint32_t    width           #[in]: Input buffer width
        uint32_t    height          #[in]: Input buffer width
        NV_ENC_MEMORY_HEAP memoryHeap       #[in]: Input buffer memory heap
        NV_ENC_BUFFER_FORMAT bufferFmt      #[in]: Input buffer format
        uint32_t    reserved        #[in]: Reserved and must be set to 0
        void        *inputBuffer    #[out]: Pointer to input buffer
        void        *pSysMemBuffer  #[in]: Pointer to existing sysmem buffer
        uint32_t    reserved1[57]   #[in]: Reserved and must be set to 0
        void        *reserved2[63]  #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_CREATE_BITSTREAM_BUFFER:
        uint32_t    version         #[in]: Struct version. Must be set to ::NV_ENC_CREATE_BITSTREAM_BUFFER_VER
        uint32_t    size            #[in]: Size of the bitstream buffer to be created
        NV_ENC_MEMORY_HEAP memoryHeap      #[in]: Output buffer memory heap
        uint32_t    reserved        #[in]: Reserved and must be set to 0
        void        *bitstreamBuffer#[out]: Pointer to the output bitstream buffer
        void        *bitstreamBufferPtr #[out]: Reserved and should not be used
        uint32_t    reserved1[58]   #[in]: Reserved and should be set to 0
        void*       reserved2[64]   #[in]: Reserved and should be set to NULL

    ctypedef struct NV_ENC_QP:
        uint32_t    qpInterP
        uint32_t    qpInterB
        uint32_t    qpIntra

    ctypedef struct NV_ENC_CONFIG_HEVC_VUI_PARAMETERS:
        uint32_t    overscanInfoPresentFlag         #[in]: if set to 1 , it specifies that the overscanInfo is present
        uint32_t    overscanInfo                    #[in]: Specifies the overscan info(as defined in Annex E of the ITU-T Specification).
        uint32_t    videoSignalTypePresentFlag      #[in]: If set to 1, it specifies  that the videoFormat, videoFullRangeFlag and colourDescriptionPresentFlag are present. */
        uint32_t    videoFormat                     #[in]: Specifies the source video format(as defined in Annex E of the ITU-T Specification).*/
        uint32_t    videoFullRangeFlag              #[in]: Specifies the output range of the luma and chroma samples(as defined in Annex E of the ITU-T Specification). */
        uint32_t    colourDescriptionPresentFlag    #[in]: If set to 1, it specifies that the colourPrimaries, transferCharacteristics and colourMatrix are present. */
        uint32_t    colourPrimaries                 #[in]: Specifies color primaries for converting to RGB(as defined in Annex E of the ITU-T Specification) */
        uint32_t    transferCharacteristics         #[in]: Specifies the opto-electronic transfer characteristics to use (as defined in Annex E of the ITU-T Specification) */
        uint32_t    colourMatrix                    #[in]: Specifies the matrix coefficients used in deriving the luma and chroma from the RGB primaries (as defined in Annex E of the ITU-T Specification). */
        uint32_t    chromaSampleLocationFlag        #[in]: if set to 1 , it specifies that the chromaSampleLocationTop and chromaSampleLocationBot are present.*/
        uint32_t    chromaSampleLocationTop         #[in]: Specifies the chroma sample location for top field(as defined in Annex E of the ITU-T Specification) */
        uint32_t    chromaSampleLocationBot         #[in]: Specifies the chroma sample location for bottom field(as defined in Annex E of the ITU-T Specification) */
        uint32_t    bitstreamRestrictionFlag        #[in]: if set to 1, it specifies the bitstream restriction parameters are present in the bitstream.*/
        uint32_t    reserved[15]

    ctypedef struct NV_ENC_CONFIG_H264_VUI_PARAMETERS:
        uint32_t    overscanInfoPresentFlag         #[in]: if set to 1 , it specifies that the overscanInfo is present
        uint32_t    overscanInfo                    #[in]: Specifies the overscan info(as defined in Annex E of the ITU-T Specification).
        uint32_t    videoSignalTypePresentFlag      #[in]: If set to 1, it specifies  that the videoFormat, videoFullRangeFlag and colourDescriptionPresentFlag are present.
        uint32_t    videoFormat                     #[in]: Specifies the source video format(as defined in Annex E of the ITU-T Specification).
        uint32_t    videoFullRangeFlag              #[in]: Specifies the output range of the luma and chroma samples(as defined in Annex E of the ITU-T Specification).
        uint32_t    colourDescriptionPresentFlag    #[in]: If set to 1, it specifies that the colourPrimaries, transferCharacteristics and colourMatrix are present.
        uint32_t    colourPrimaries                 #[in]: Specifies color primaries for converting to RGB(as defined in Annex E of the ITU-T Specification)
        uint32_t    transferCharacteristics         #[in]: Specifies the opto-electronic transfer characteristics to use (as defined in Annex E of the ITU-T Specification)
        uint32_t    colourMatrix                    #[in]: Specifies the matrix coefficients used in deriving the luma and chroma from the RGB primaries (as defined in Annex E of the ITU-T Specification).
        uint32_t    chromaSampleLocationFlag        #[in]: if set to 1 , it specifies that thechromaSampleLocationTop and chromaSampleLocationBot are present.
        uint32_t    chromaSampleLocationTop         #[in]: Specifies the chroma sample location for top field(as defined in Annex E of the ITU-T Specification)
        uint32_t    chromaSampleLocationBot         #[in]: Specifies the chroma sample location for bottom field(as defined in Annex E of the ITU-T Specification)
        uint32_t    bitstreamRestrictionFlag        #[in]: if set to 1, it speficies the bitstream restriction parameters are present in the bitstream.
        uint32_t    reserved[15]

    ctypedef struct NV_ENC_CONFIG_H264:
        uint32_t    enableTemporalSVC   #[in]: Set to 1 to enable SVC temporal
        uint32_t    enableStereoMVC     #[in]: Set to 1 to enable stereo MVC
        uint32_t    hierarchicalPFrames #[in]: Set to 1 to enable hierarchical PFrames
        uint32_t    hierarchicalBFrames #[in]: Set to 1 to enable hierarchical BFrames
        uint32_t    outputBufferingPeriodSEI    #[in]: Set to 1 to write SEI buffering period syntax in the bitstream
        uint32_t    outputPictureTimingSEI      #[in]: Set to 1 to write SEI picture timing syntax in the bitstream
        uint32_t    outputAUD                   #[in]: Set to 1 to write access unit delimiter syntax in bitstream
        uint32_t    disableSPSPPS               #[in]: Set to 1 to disable writing of Sequence and Picture parameter info in bitstream
        uint32_t    outputFramePackingSEI       #[in]: Set to 1 to enable writing of frame packing arrangement SEI messages to bitstream
        uint32_t    outputRecoveryPointSEI      #[in]: Set to 1 to enable writing of recovery point SEI message
        uint32_t    enableIntraRefresh          #[in]: Set to 1 to enable gradual decoder refresh or intra refresh. If the GOP structure uses B frames this will be ignored
        uint32_t    enableConstrainedEncoding   #[in]: Set this to 1 to enable constrainedFrame encoding where each slice in the constarined picture is independent of other slices
                                                #Check support for constrained encoding using ::NV_ENC_CAPS_SUPPORT_CONSTRAINED_ENCODING caps.
        uint32_t    repeatSPSPPS        #[in]: Set to 1 to enable writing of Sequence and Picture parameter for every IDR frame
        uint32_t    enableVFR           #[in]: Set to 1 to enable variable frame rate.
        uint32_t    enableLTR           #[in]: Currently this feature is not available and must be set to 0. Set to 1 to enable LTR support and auto-mark the first
        uint32_t    qpPrimeYZeroTransformBypassFlag #[in]  To enable lossless encode set this to 1, set QP to 0 and RC_mode to NV_ENC_PARAMS_RC_CONSTQP and profile to HIGH_444_PREDICTIVE_PROFILE
                                                    #Check support for lossless encoding using ::NV_ENC_CAPS_SUPPORT_LOSSLESS_ENCODE caps.
        uint32_t    useConstrainedIntraPred         #[in]: Set 1 to enable constrained intra prediction.
        uint32_t    reservedBitFields[15]       #[in]: Reserved bitfields and must be set to 0
        uint32_t    level               #[in]: Specifies the encoding level. Client is recommended to set this to NV_ENC_LEVEL_AUTOSELECT in order to enable the NvEncodeAPI interface to select the correct level.
        uint32_t    idrPeriod           #[in]: Specifies the IDR interval. If not set, this is made equal to gopLength in NV_ENC_CONFIG.Low latency application client can set IDR interval to NVENC_INFINITE_GOPLENGTH so that IDR frames are not inserted automatically.
        uint32_t    separateColourPlaneFlag     #[in]: Set to 1 to enable 4:4:4 separate colour planes
        uint32_t    disableDeblockingFilterIDC  #[in]: Specifies the deblocking filter mode. Permissible value range: [0,2]
        uint32_t    numTemporalLayers   #[in]: Specifies max temporal layers to be used for hierarchical coding. Valid value range is [1,::NV_ENC_CAPS_NUM_MAX_TEMPORAL_LAYERS]
        uint32_t    spsId               #[in]: Specifies the SPS id of the sequence header. Currently reserved and must be set to 0.
        uint32_t    ppsId               #[in]: Specifies the PPS id of the picture header. Currently reserved and must be set to 0.
        NV_ENC_H264_ADAPTIVE_TRANSFORM_MODE adaptiveTransformMode   #[in]: Specifies the AdaptiveTransform Mode. Check support for AdaptiveTransform mode using ::NV_ENC_CAPS_SUPPORT_ADAPTIVE_TRANSFORM caps.
        NV_ENC_H264_FMO_MODE fmoMode    #[in]: Specified the FMO Mode. Check support for FMO using ::NV_ENC_CAPS_SUPPORT_FMO caps.
        NV_ENC_H264_BDIRECT_MODE bdirectMode    #[in]: Specifies the BDirect mode. Check support for BDirect mode using ::NV_ENC_CAPS_SUPPORT_BDIRECT_MODE caps.
        NV_ENC_H264_ENTROPY_CODING_MODE entropyCodingMode   #[in]: Specifies the entropy coding mode. Check support for CABAC mode using ::NV_ENC_CAPS_SUPPORT_CABAC caps.
        NV_ENC_STEREO_PACKING_MODE stereoMode   #[in]: Specifies the stereo frame packing mode which is to be signalled in frame packing arrangement SEI
        uint32_t    intraRefreshPeriod  #[in]: Specifies the interval between successive intra refresh if enableIntrarefresh is set. Requires enableIntraRefresh to be set.
                                        #Will be disabled if NV_ENC_CONFIG::gopLength is not set to NVENC_INFINITE_GOPLENGTH.
        uint32_t    intraRefreshCnt     #[in]: Specifies the length of intra refresh in number of frames for periodic intra refresh. This value should be smaller than intraRefreshPeriod
        uint32_t    maxNumRefFrames     #[in]: Specifies the DPB size used for encoding. Setting it to 0 will let driver use the default dpb size.
                                        #The low latency application which wants to invalidate reference frame as an error resilience tool
                                        #is recommended to use a large DPB size so that the encoder can keep old reference frames which can be used if recent
                                        #frames are invalidated.
        uint32_t    sliceMode           #[in]: This parameter in conjunction with sliceModeData specifies the way in which the picture is divided into slices
                                        #sliceMode = 0 MB based slices, sliceMode = 1 Byte based slices, sliceMode = 2 MB row based slices, sliceMode = 3, numSlices in Picture
                                        #When forceIntraRefreshWithFrameCnt is set it will have priority over sliceMode setting
                                        #When sliceMode == 0 and sliceModeData == 0 whole picture will be coded with one slice
        uint32_t    sliceModeData       #[in]: Specifies the parameter needed for sliceMode. For:
                                        #sliceMode = 0, sliceModeData specifies # of MBs in each slice (except last slice)
                                        #sliceMode = 1, sliceModeData specifies maximum # of bytes in each slice (except last slice)
                                        #sliceMode = 2, sliceModeData specifies # of MB rows in each slice (except last slice)
                                        #sliceMode = 3, sliceModeData specifies number of slices in the picture. Driver will divide picture into slices optimally
        NV_ENC_CONFIG_H264_VUI_PARAMETERS h264VUIParameters   #[in]: Specifies the H264 video usability info pamameters
        uint32_t    ltrNumFrames        #[in]: Specifies the number of LTR frames used. Additionally, encoder will mark the first numLTRFrames base layer reference frames within each IDR interval as LTR
        uint32_t    ltrTrustMode        #[in]: Specifies the LTR operating mode. Set to 0 to disallow encoding using LTR frames until later specified. Set to 1 to allow encoding using LTR frames unless later invalidated.
        uint32_t    chromaFormatIDC     #[in]: Specifies the chroma format. Should be set to 1 for yuv420 input, 3 for yuv444 input.
                                        #Check support for YUV444 encoding using ::NV_ENC_CAPS_SUPPORT_YUV444_ENCODE caps.
        uint32_t    maxTemporalLayers   #[in]: Specifies the max temporal layer used for hierarchical coding.
        uint32_t    reserved1[270]      #[in]: Reserved and must be set to 0
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_CONFIG_HEVC:
        uint32_t    level               #[in]: Specifies the level of the encoded bitstream.
        uint32_t    tier                #[in]: Specifies the level tier of the encoded bitstream.
        NV_ENC_HEVC_CUSIZE minCUSize    #[in]: Specifies the minimum size of luma coding unit.
        NV_ENC_HEVC_CUSIZE maxCUSize    #[in]: Specifies the maximum size of luma coding unit. Currently NVENC SDK only supports maxCUSize equal to NV_ENC_HEVC_CUSIZE_32x32.
        uint32_t    useConstrainedIntraPred             #[in]: Set 1 to enable constrained intra prediction.
        uint32_t    disableDeblockAcrossSliceBoundary   #[in]: Set 1 to disable in loop filtering across slice boundary.
        uint32_t    outputBufferingPeriodSEI            #[in]: Set 1 to write SEI buffering period syntax in the bitstream
        uint32_t    outputPictureTimingSEI              #[in]: Set 1 to write SEI picture timing syntax in the bitstream
        uint32_t    outputAUD                           #[in]: Set 1 to write Access Unit Delimiter syntax.
        uint32_t    enableLTR                           #[in]: Set 1 to enable use of long term reference pictures for inter prediction.
        uint32_t    disableSPSPPS                       #[in]: Set 1 to disable VPS,SPS and PPS signalling in the bitstream.
        uint32_t    repeatSPSPPS                        #[in]: Set 1 to output VPS,SPS and PPS for every IDR frame.
        uint32_t    enableIntraRefresh                  #[in]: Set 1 to enable gradual decoder refresh or intra refresh. If the GOP structure uses B frames this will be ignored
        uint32_t    reserved                            #[in]: Reserved bitfields.
        uint32_t    idrPeriod                           #[in]: Specifies the IDR interval. If not set, this is made equal to gopLength in NV_ENC_CONFIG.Low latency application client can set IDR interval to NVENC_INFINITE_GOPLENGTH so that IDR frames are not inserted automatically.
        uint32_t    intraRefreshPeriod                  #[in]: Specifies the interval between successive intra refresh if enableIntrarefresh is set. Requires enableIntraRefresh to be set.
                                                        #Will be disabled if NV_ENC_CONFIG::gopLength is not set to NVENC_INFINITE_GOPLENGTH.
        uint32_t    intraRefreshCnt                     #[in]: Specifies the length of intra refresh in number of frames for periodic intra refresh. This value should be smaller than intraRefreshPeriod
        uint32_t    maxNumRefFramesInDPB                #[in]: Specifies the maximum number of references frames in the DPB.
        uint32_t    ltrNumFrames                        #[in]: Specifies the maximum number of long term references can be used for prediction
        uint32_t    vpsId                               #[in]: Specifies the VPS id of the video parameter set. Currently reserved and must be set to 0.
        uint32_t    spsId                               #[in]: Specifies the SPS id of the sequence header. Currently reserved and must be set to 0.
        uint32_t    ppsId                               #[in]: Specifies the PPS id of the picture header. Currently reserved and must be set to 0.
        uint32_t    sliceMode                           #[in]: This parameter in conjunction with sliceModeData specifies the way in which the picture is divided into slices
                                                        #sliceMode = 0 CTU based slices, sliceMode = 1 Byte based slices, sliceMode = 2 CTU row based slices, sliceMode = 3, numSlices in Picture
                                                        #When sliceMode == 0 and sliceModeData == 0 whole picture will be coded with one slice
        uint32_t    sliceModeData                       #[in]: Specifies the parameter needed for sliceMode. For:
                                                        #sliceMode = 0, sliceModeData specifies # of CTUs in each slice (except last slice)
                                                        #sliceMode = 1, sliceModeData specifies maximum # of bytes in each slice (except last slice)
                                                        #sliceMode = 2, sliceModeData specifies # of CTU rows in each slice (except last slice)
                                                        #sliceMode = 3, sliceModeData specifies number of slices in the picture. Driver will divide picture into slices optimally
        uint32_t    maxTemporalLayersMinus1             #[in]: Specifies the max temporal layer used for hierarchical coding.
        NV_ENC_CONFIG_HEVC_VUI_PARAMETERS hevcVUIParameters #Specifies the HEVC video usability info pamameters
        uint32_t    reserved1[218]                      #[in]: Reserved and must be set to 0.
        void*       reserved2[64]                       #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_CODEC_CONFIG:
        NV_ENC_CONFIG_H264  h264Config                  #[in]: Specifies the H.264-specific encoder configuration
        NV_ENC_CONFIG_HEVC  hevcConfig                  #[in]: Specifies the HEVC-specific encoder configuration. Currently unsupported and must not to be used.
        uint32_t            reserved[256]               #[in]: Reserved and must be set to 0

    ctypedef struct NV_ENC_RC_PARAMS:
        uint32_t    version
        NV_ENC_PARAMS_RC_MODE rateControlMode   #[in]: Specifies the rate control mode. Check support for various rate control modes using ::NV_ENC_CAPS_SUPPORTED_RATECONTROL_MODES caps.
        NV_ENC_QP   constQP             #[in]: Specifies the initial QP to be used for encoding, these values would be used for all frames if in Constant QP mode.
        uint32_t    averageBitRate      #[in]: Specifies the average bitrate(in bits/sec) used for encoding.
        uint32_t    maxBitRate          #[in]: Specifies the maximum bitrate for the encoded output. This is used for VBR and ignored for CBR mode.
        uint32_t    vbvBufferSize       #[in]: Specifies the VBV(HRD) buffer size. in bits. Set 0 to use the default VBV  buffer size.
        uint32_t    vbvInitialDelay     #[in]: Specifies the VBV(HRD) initial delay in bits. Set 0 to use the default VBV  initial delay
        uint32_t    enableMinQP         #[in]: Set this to 1 if minimum QP used for rate control.
        uint32_t    enableMaxQP         #[in]: Set this to 1 if maximum QP used for rate control.
        uint32_t    enableInitialRCQP   #[in]: Set this to 1 if user suppplied initial QP is used for rate control.
        uint32_t    enableAQ            #[in]: Set this to 1 to enable adaptive quantization.
        uint32_t    enableExtQPDeltaMap #[in]: Set this to 1 to enable additional QP modifier for each MB supplied by client though signed byte array pointed to by NV_ENC_PIC_PARAMS::qpDeltaMap
        uint32_t    reservedBitFields[27] #[in]: Reserved bitfields and must be set to 0
        NV_ENC_QP   minQP               #[in]: Specifies the minimum QP used for rate control. Client must set NV_ENC_CONFIG::enableMinQP to 1.
        NV_ENC_QP   maxQP               #[in]: Specifies the maximum QP used for rate control. Client must set NV_ENC_CONFIG::enableMaxQP to 1.
        NV_ENC_QP   initialRCQP         #[in]: Specifies the initial QP used for rate control. Client must set NV_ENC_CONFIG::enableInitialRCQP to 1.
        uint32_t    temporallayerIdxMask#[in]: Specifies the temporal layers (as a bitmask) whose QPs have changed. Valid max bitmask is [2^NV_ENC_CAPS_NUM_MAX_TEMPORAL_LAYERS - 1]
        uint8_t     temporalLayerQP[8]  #[in]: Specifies the temporal layer QPs used for rate control. Temporal layer index is used as as the array index
        uint32_t    reserved[10]

    ctypedef struct NV_ENC_CONFIG:
        uint32_t    version             #[in]: Struct version. Must be set to ::NV_ENC_CONFIG_VER.
        GUID        profileGUID         #[in]: Specifies the codec profile guid. If client specifies \p NV_ENC_CODEC_PROFILE_AUTOSELECT_GUID the NvEncodeAPI interface will select the appropriate codec profile.
        uint32_t    gopLength           #[in]: Specifies the number of pictures in one GOP. Low latency application client can set goplength to NVENC_INFINITE_GOPLENGTH so that keyframes are not inserted automatically.
        int32_t     frameIntervalP      #[in]: Specifies the GOP pattern as follows: \p frameIntervalP = 0: I, 1: IPP, 2: IBP, 3: IBBP  If goplength is set to NVENC_INFINITE_GOPLENGTH \p frameIntervalP should be set to 1.
        uint32_t    monoChromeEncoding  #[in]: Set this to 1 to enable monochrome encoding for this session.
        NV_ENC_PARAMS_FRAME_FIELD_MODE  frameFieldMode      #[in]: Specifies the frame/field mode. Check support for field encoding using ::NV_ENC_CAPS_SUPPORT_FIELD_ENCODING caps.
        NV_ENC_MV_PRECISION mvPrecision #[in]: Specifies the desired motion vector prediction precision.
        NV_ENC_RC_PARAMS    rcParams    #[in]: Specifies the rate control parameters for the current encoding session.
        NV_ENC_CODEC_CONFIG encodeCodecConfig   #[in]: Specifies the codec specific config parameters through this union.
        uint32_t    reserved[278]       #[in]: Reserved and must be set to 0
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL


    ctypedef struct NVENC_EXTERNAL_ME_HINT_COUNTS_PER_BLOCKTYPE:
        uint32_t    numCandsPerBlk16x16 #[in]: Specifies the number of candidates per 16x16 block.
        uint32_t    numCandsPerBlk16x8  #[in]: Specifies the number of candidates per 16x8 block.
        uint32_t    numCandsPerBlk8x16  #[in]: Specifies the number of candidates per 8x16 block.
        uint32_t    numCandsPerBlk8x8   #[in]: Specifies the number of candidates per 8x8 block.
        uint32_t    reserved            #[in]: Reserved for padding.
        uint32_t    reserved1[3]        #[in]: Reserved for future use.

    ctypedef struct NV_ENC_INITIALIZE_PARAMS:
        uint32_t    version             #[in]: Struct version. Must be set to ::NV_ENC_INITIALIZE_PARAMS_VER.
        GUID        encodeGUID          #[in]: Specifies the Encode GUID for which the encoder is being created. ::NvEncInitializeEncoder() API will fail if this is not set, or set to unsupported value.
        GUID        presetGUID          #[in]: Specifies the preset for encoding. If the preset GUID is set then , the preset configuration will be applied before any other parameter.
        uint32_t    encodeWidth         #[in]: Specifies the encode width. If not set ::NvEncInitializeEncoder() API will fail.
        uint32_t    encodeHeight        #[in]: Specifies the encode height. If not set ::NvEncInitializeEncoder() API will fail.
        uint32_t    darWidth            #[in]: Specifies the display aspect ratio Width.
        uint32_t    darHeight           #[in]: Specifies the display aspect ratio height.
        uint32_t    frameRateNum        #[in]: Specifies the numerator for frame rate used for encoding in frames per second ( Frame rate = frameRateNum / frameRateDen ).
        uint32_t    frameRateDen        #[in]: Specifies the denominator for frame rate used for encoding in frames per second ( Frame rate = frameRateNum / frameRateDen ).
        uint32_t    enableEncodeAsync   #[in]: Set this to 1 to enable asynchronous mode and is expected to use events to get picture completion notification.
        uint32_t    enablePTD           #[in]: Set this to 1 to enable the Picture Type Decision is be taken by the NvEncodeAPI interface.
        uint32_t    reportSliceOffsets  #[in]: Set this to 1 to enable reporting slice offsets in ::_NV_ENC_LOCK_BITSTREAM. Currently supported only for H264. Client must set this to 0 if NV_ENC_CONFIG_H264::sliceMode is 1
        uint32_t    enableSubFrameWrite #[in]: Set this to 1 to write out available bitstream to memory at subframe intervals
        uint32_t    enableExternalMEHints   #[in]: Set to 1 to enable external ME hints for the current frame. Currently this feature is supported only if NV_ENC_INITIALIZE_PARAMS::enablePTD to 0 or\p frameIntervalP = 1 (i.e no B frames).
        uint32_t    enableMEOnlyMode    #[in] Set to 1 to enable ME Only Mode
        uint32_t    reservedBitFields[28]   #[in]: Reserved bitfields and must be set to 0
        uint32_t    privDataSize        #[in]: Reserved private data buffer size and must be set to 0
        void        *privData           #[in]: Reserved private data buffer and must be set to NULL
        NV_ENC_CONFIG *encodeConfig     #[in]: Specifies the advanced codec specific structure. If client has sent a valid codec config structure, it will override parameters set by the NV_ENC_INITIALIZE_PARAMS::presetGUID parameter. If set to NULL the NvEncodeAPI interface will use the NV_ENC_INITIALIZE_PARAMS::presetGUID to set the codec specific parameters.
                                        #Client can also optionally query the NvEncodeAPI interface to get codec specific parameters for a presetGUID using ::NvEncGetEncodePresetConfig() API. It can then modify (if required) some of the codec config parameters and send down a custom config structure as part of ::_NV_ENC_INITIALIZE_PARAMS.
                                        #Even in this case client is recommended to pass the same preset guid it has used in ::NvEncGetEncodePresetConfig() API to query the config structure; as NV_ENC_INITIALIZE_PARAMS::presetGUID. This will not override the custom config structure but will be used to determine other Encoder HW specific parameters not exposed in the API.
        uint32_t    maxEncodeWidth      #[in]: Maximum encode width to be used for current Encode session.
                                        #Client should allocate output buffers according to this dimension for dynamic resolution change. If set to 0, Encoder will not allow dynamic resolution change.
        uint32_t    maxEncodeHeight     #[in]: Maximum encode height to be allowed for current Encode session.
                                        #Client should allocate output buffers according to this dimension for dynamic resolution change. If set to 0, Encode will not allow dynamic resolution change.
        NVENC_EXTERNAL_ME_HINT_COUNTS_PER_BLOCKTYPE maxMEHintCountsPerBlock[2]  #[in]: If Client wants to pass external motion vectors in NV_ENC_PIC_PARAMS::meExternalHints buffer it must specify the maximum number of hint candidates per block per direction for the encode session.
                                        #The NV_ENC_INITIALIZE_PARAMS::maxMEHintCountsPerBlock[0] is for L0 predictors and NV_ENC_INITIALIZE_PARAMS::maxMEHintCountsPerBlock[1] is for L1 predictors.
                                        #This client must also set NV_ENC_INITIALIZE_PARAMS::enableExternalMEHints to 1.
        uint32_t    reserved[289]       #[in]: Reserved and must be set to 0
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_RECONFIGURE_PARAMS:
        uint32_t    version             #[in]: Struct version. Must be set to ::NV_ENC_RECONFIGURE_PARAMS_VER.
        NV_ENC_INITIALIZE_PARAMS reInitEncodeParams
        uint32_t    resetEncoder        #[in]: This resets the rate control states and other internal encoder states. This should be used only with an IDR frame.
                                        #If NV_ENC_INITIALIZE_PARAMS::enablePTD is set to 1, encoder will force the frame type to IDR
        uint32_t    forceIDR            #[in]: Encode the current picture as an IDR picture. This flag is only valid when Picture type decision is taken by the Encoder
                                        #[_NV_ENC_INITIALIZE_PARAMS::enablePTD == 1].
        uint32_t    reserved

    ctypedef struct NV_ENC_PRESET_CONFIG:
        uint32_t    version             #[in]:  Struct version. Must be set to ::NV_ENC_PRESET_CONFIG_VER.
        NV_ENC_CONFIG presetCfg         #[out]: preset config returned by the Nvidia Video Encoder interface.
        uint32_t    reserved1[255]      #[in]: Reserved and must be set to 0
        void*       reserved2[64]       #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_H264_SEI_PAYLOAD:
        uint32_t    payloadSize         #[in] SEI payload size in bytes. SEI payload must be byte aligned, as described in Annex D
        uint32_t    payloadType         #[in] SEI payload types and syntax can be found in Annex D of the H.264 Specification.
        uint8_t     *payload            #[in] pointer to user data
    ctypedef NV_ENC_H264_SEI_PAYLOAD NV_ENC_SEI_PAYLOAD

    ctypedef struct NV_ENC_PIC_PARAMS_H264:
        uint32_t    displayPOCSyntax    #[in]: Specifies the display POC syntax This is required to be set if client is handling the picture type decision.
        uint32_t    reserved3           #[in]: Reserved and must be set to 0
        uint32_t    refPicFlag          #[in]: Set to 1 for a reference picture. This is ignored if NV_ENC_INITIALIZE_PARAMS::enablePTD is set to 1.
        uint32_t    colourPlaneId       #[in]: Specifies the colour plane ID associated with the current input.
        uint32_t    forceIntraRefreshWithFrameCnt   #[in]: Forces an intra refresh with duration equal to intraRefreshFrameCnt.
                                        #When outputRecoveryPointSEI is set this is value is used for recovery_frame_cnt in recovery point SEI message
                                        #forceIntraRefreshWithFrameCnt cannot be used if B frames are used in the GOP structure specified
        uint32_t    constrainedFrame    #[in]: Set to 1 if client wants to encode this frame with each slice completely independent of other slices in the frame.
                                        #NV_ENC_INITIALIZE_PARAMS::enableConstrainedEncoding should be set to 1
        uint32_t    sliceModeDataUpdate #[in]: Set to 1 if client wants to change the sliceModeData field to speficy new sliceSize Parameter
                                        #When forceIntraRefreshWithFrameCnt is set it will have priority over sliceMode setting
        uint32_t    ltrMarkFrame        #[in]: Set to 1 if client wants to mark this frame as LTR
        uint32_t    ltrUseFrames        #[in]: Set to 1 if client allows encoding this frame using the LTR frames specified in ltrFrameBitmap
        uint32_t    reservedBitFields   #[in]: Reserved bit fields and must be set to 0
        uint8_t*    sliceTypeData       #[in]: Array which specifies the slice type used to force intra slice for a particular slice. Currently supported only for NV_ENC_CONFIG_H264::sliceMode == 3.
                                        #Client should allocate array of size sliceModeData where sliceModeData is specified in field of ::_NV_ENC_CONFIG_H264
                                        #Array element with index n corresponds to nth slice. To force a particular slice to intra client should set corresponding array element to NV_ENC_SLICE_TYPE_I
                                        #all other array elements should be set to NV_ENC_SLICE_TYPE_DEFAULT
        uint32_t    sliceTypeArrayCnt   #[in]: Client should set this to the number of elements allocated in sliceTypeData array. If sliceTypeData is NULL then this should be set to 0
        uint32_t    seiPayloadArrayCnt  #[in]: Specifies the number of elements allocated in  seiPayloadArray array.
        NV_ENC_SEI_PAYLOAD *seiPayloadArray    #[in]: Array of SEI payloads which will be inserted for this frame.
        uint32_t    sliceMode           #[in]: This parameter in conjunction with sliceModeData specifies the way in which the picture is divided into slices
                                        #sliceMode = 0 MB based slices, sliceMode = 1 Byte based slices, sliceMode = 2 MB row based slices, sliceMode = 3, numSlices in Picture
                                        #When forceIntraRefreshWithFrameCnt is set it will have priority over sliceMode setting
                                        #When sliceMode == 0 and sliceModeData == 0 whole picture will be coded with one slice
        uint32_t    sliceModeData       #[in]: Specifies the parameter needed for sliceMode. For:
                                        #sliceMode = 0, sliceModeData specifies # of MBs in each slice (except last slice)
                                        #sliceMode = 1, sliceModeData specifies maximum # of bytes in each slice (except last slice)
                                        #sliceMode = 2, sliceModeData specifies # of MB rows in each slice (except last slice)
                                        #sliceMode = 3, sliceModeData specifies number of slices in the picture. Driver will divide picture into slices optimally
        uint32_t    ltrMarkFrameIdx     #[in]: Specifies the long term referenceframe index to use for marking this frame as LTR.
        uint32_t    ltrUseFrameBitmap   #[in]: Specifies the the associated bitmap of LTR frame indices when encoding this frame.
        uint32_t    ltrUsageMode        #[in]: Specifies additional usage constraints for encoding using LTR frames from this point further. 0: no constraints, 1: no short term refs older than current, no previous LTR frames.
        uint32_t    reserved[243]       #[in]: Reserved and must be set to 0.
        void*       reserved2[62]       #[in]: Reserved and must be set to NULL.

    ctypedef union NV_ENC_CODEC_PIC_PARAMS:
        NV_ENC_PIC_PARAMS_H264 h264PicParams    #[in]: H264 encode picture params.
        uint32_t               reserved[256]    #[in]: Reserved and must be set to 0.

    ctypedef struct NV_ENC_MEONLY_PARAMS:
        uint32_t    version             #[in]: Struct version. Must be set to NV_ENC_MEONLY_PARAMS_VER.
        uint32_t    inputWidth          #[in]: Specifies the input buffer width
        uint32_t    inputHeight         #[in]: Specifies the input buffer height
        NV_ENC_INPUT_PTR inputBuffer    #[in]: Specifies the input buffer pointer. Client must use a pointer obtained from NvEncCreateInputBuffer() or NvEncMapInputResource() APIs.
        NV_ENC_INPUT_PTR referenceFrame #[in]: Specifies the reference frame pointer
        NV_ENC_OUTPUT_PTR outputMV      #[in,out]: Specifies the pointer to output motion vector data buffer allocated by NvEncCreateMVBuffer.
        NV_ENC_BUFFER_FORMAT bufferFmt  #[in]: Specifies the input buffer format.
        uint32_t    reserved1[252]      #[in]: Reserved and must be set to 0
        void* reserved2[61]             #[in]: Reserved and must be set to NULL

    ctypedef struct NVENC_EXTERNAL_ME_HINT:
        int32_t     mvx                 #[in]: Specifies the x component of integer pixel MV (relative to current MB) S12.0.
        int32_t     mvy                 #[in]: Specifies the y component of integer pixel MV (relative to current MB) S10.0
        int32_t     refidx              #[in]: Specifies the reference index (31=invalid). Current we support only 1 reference frame per direction for external hints, so \p refidx must be 0.
        int32_t     dir                 #[in]: Specifies the direction of motion estimation . 0=L0 1=L1.
        int32_t     partType            #[in]: Specifies the bloack partition type.0=16x16 1=16x8 2=8x16 3=8x8 (blocks in partition must be consecutive).
        int32_t     lastofPart          #[in]: Set to 1 for the last MV of (sub) partition
        int32_t     lastOfMB            #[in]: Set to 1 for the last MV of macroblock.

    ctypedef struct NV_ENC_PIC_PARAMS:
        uint32_t    version             #[in]: Struct version. Must be set to ::NV_ENC_PIC_PARAMS_VER.
        uint32_t    inputWidth          #[in]: Specifies the input buffer width
        uint32_t    inputHeight         #[in]: Specifies the input buffer height
        uint32_t    inputPitch          #[in]: Specifies the input buffer pitch. If pitch value is not known, set this to inputWidth.
        uint32_t    encodePicFlags      #[in]: Specifies bit-wise OR`ed encode pic flags. See ::NV_ENC_PIC_FLAGS enum.
        uint32_t    frameIdx            #[in]: Specifies the frame index associated with the input frame [optional].
        uint64_t    inputTimeStamp      #[in]: Specifies presentation timestamp associated with the input picture.
        uint64_t    inputDuration       #[in]: Specifies duration of the input picture
        NV_ENC_INPUT_PTR  inputBuffer   #[in]: Specifies the input buffer pointer. Client must use a pointer obtained from ::NvEncCreateInputBuffer() or ::NvEncMapInputResource() APIs.
        NV_ENC_OUTPUT_PTR outputBitstream #[in]: Specifies the pointer to output buffer. Client should use a pointer obtained from ::NvEncCreateBitstreamBuffer() API.
        void*       completionEvent     #[in]: Specifies an event to be signalled on completion of encoding of this Frame [only if operating in Asynchronous mode]. Each output buffer should be associated with a distinct event pointer.
        NV_ENC_BUFFER_FORMAT bufferFmt  #[in]: Specifies the input buffer format.
        NV_ENC_PIC_STRUCT pictureStruct #[in]: Specifies structure of the input picture.
        NV_ENC_PIC_TYPE pictureType     #[in]: Specifies input picture type. Client required to be set explicitly by the client if the client has not set NV_ENC_INITALIZE_PARAMS::enablePTD to 1 while calling NvInitializeEncoder.
        NV_ENC_CODEC_PIC_PARAMS codecPicParams  #[in]: Specifies the codec specific per-picture encoding parameters.
        uint32_t    newEncodeWidth      #[in]: Specifies the new output width for current Encoding session, in case of dynamic resolution change. Client should only set this in combination with NV_ENC_PIC_FLAGS::NV_ENC_PIC_FLAG_DYN_RES_CHANGE.
                                        #Additionally, if Picture Type decision is handled by the Client [_NV_ENC_INITIALIZE_PARAMS::enablePTD == 0], the client should set the _NV_ENC_PIC_PARAMS::pictureType as ::NV_ENC_PIC_TYPE_IDR.
                                        #If _NV_ENC_INITIALIZE_PARAMS::enablePTD == 1, then the Encoder will generate an IDR frame corresponding to this input.
        uint32_t    newEncodeHeight     #[in]: Specifies the new output width for current Encoding session, in case of dynamic resolution change. Client should only set this in combination with NV_ENC_PIC_FLAGS::NV_ENC_PIC_FLAG_DYN_RES_CHANGE.
                                        #Additionally, if Picture Type decision is handled by the Client [_NV_ENC_INITIALIZE_PARAMS::enablePTD == 0], the client should set the _NV_ENC_PIC_PARAMS::pictureType as ::NV_ENC_PIC_TYPE_IDR.
                                        #If _NV_ENC_INITIALIZE_PARAMS::enablePTD == 1, then the Encoder will generate an IDR frame corresponding to this input.
        NV_ENC_RC_PARAMS rcParams       #[in]: Specifies the rate control parameters for the current encoding session.
        NVENC_EXTERNAL_ME_HINT_COUNTS_PER_BLOCKTYPE meHintCountsPerBlock[2] #[in]: Specifies the number of hint candidates per block per direction for the current frame. meHintCountsPerBlock[0] is for L0 predictors and meHintCountsPerBlock[1] is for L1 predictors.
                                        #The candidate count in NV_ENC_PIC_PARAMS::meHintCountsPerBlock[lx] must never exceed NV_ENC_INITIALIZE_PARAMS::maxMEHintCountsPerBlock[lx] provided during encoder intialization.
        NVENC_EXTERNAL_ME_HINT *meExternalHints     #[in]: Specifies the pointer to ME external hints for the current frame. The size of ME hint buffer should be equal to number of macroblocks multiplied by the total number of candidates per macroblock.
                                        #The total number of candidates per MB per direction = 1*meHintCountsPerBlock[Lx].numCandsPerBlk16x16 + 2*meHintCountsPerBlock[Lx].numCandsPerBlk16x8 + 2*meHintCountsPerBlock[Lx].numCandsPerBlk8x8
                                        # + 4*meHintCountsPerBlock[Lx].numCandsPerBlk8x8. For frames using bidirectional ME , the total number of candidates for single macroblock is sum of total number of candidates per MB for each direction (L0 and L1)
        uint32_t    newDarWidth         #[in]: Specifies the new disalay aspect ratio width for current Encoding session, in case of dynamic resolution change. Client should only set this in combination with NV_ENC_PIC_FLAGS::NV_ENC_PIC_FLAG_DYN_RES_CHANGE.
                                        #Additionally, if Picture Type decision is handled by the Client [_NV_ENC_INITIALIZE_PARAMS::enablePTD == 0], the client should set the _NV_ENC_PIC_PARAMS::pictureType as ::NV_ENC_PIC_TYPE_IDR.
                                        #If _NV_ENC_INITIALIZE_PARAMS::enablePTD == 1, then the Encoder will generate an IDR frame corresponding to this input.
        uint32_t    newDarHeight        #[in]: Specifies the new disalay aspect ratio height for current Encoding session, in case of dynamic resolution change. Client should only set this in combination with NV_ENC_PIC_FLAGS::NV_ENC_PIC_FLAG_DYN_RES_CHANGE.
                                        #If _NV_ENC_INITIALIZE_PARAMS::enablePTD == 1, then the Encoder will generate an IDR frame corresponding to this input.
        uint32_t    reserved1[259]      #[in]: Reserved and must be set to 0
        void*       reserved2[63]       #[in]: Reserved and must be set to NULL

    #NVENCSTATUS NvEncodeAPICreateInstance(NV_ENCODE_API_FUNCTION_LIST *functionList)

    ctypedef NVENCSTATUS (*PNVENCOPENENCODESESSION)         (void* device, uint32_t deviceType, void** encoder) nogil
    ctypedef NVENCSTATUS (*PNVENCGETENCODEGUIDCOUNT)        (void* encoder, uint32_t* encodeGUIDCount) nogil
    ctypedef NVENCSTATUS (*PNVENCGETENCODEGUIDS)            (void* encoder, GUID* GUIDs, uint32_t guidArraySize, uint32_t* GUIDCount) nogil
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPROFILEGUIDCOUNT) (void* encoder, GUID encodeGUID, uint32_t* encodeProfileGUIDCount) nogil
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPROFILEGUIDS)     (void* encoder, GUID encodeGUID, GUID* profileGUIDs, uint32_t guidArraySize, uint32_t* GUIDCount) nogil
    ctypedef NVENCSTATUS (*PNVENCGETINPUTFORMATCOUNT)       (void* encoder, GUID encodeGUID, uint32_t* inputFmtCount) nogil
    ctypedef NVENCSTATUS (*PNVENCGETINPUTFORMATS)           (void* encoder, GUID encodeGUID, NV_ENC_BUFFER_FORMAT* inputFmts, uint32_t inputFmtArraySize, uint32_t* inputFmtCount) nogil
    ctypedef NVENCSTATUS (*PNVENCGETENCODECAPS)             (void* encoder, GUID encodeGUID, NV_ENC_CAPS_PARAM* capsParam, int* capsVal) nogil
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPRESETCOUNT)      (void* encoder, GUID encodeGUID, uint32_t* encodePresetGUIDCount) nogil
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPRESETGUIDS)      (void* encoder, GUID encodeGUID, GUID* presetGUIDs, uint32_t guidArraySize, uint32_t* encodePresetGUIDCount) nogil
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPRESETCONFIG)     (void* encoder, GUID encodeGUID, GUID  presetGUID, NV_ENC_PRESET_CONFIG* presetConfig) nogil
    ctypedef NVENCSTATUS (*PNVENCINITIALIZEENCODER)         (void* encoder, NV_ENC_INITIALIZE_PARAMS* createEncodeParams) nogil
    ctypedef NVENCSTATUS (*PNVENCCREATEINPUTBUFFER)         (void* encoder, NV_ENC_CREATE_INPUT_BUFFER* createInputBufferParams) nogil
    ctypedef NVENCSTATUS (*PNVENCDESTROYINPUTBUFFER)        (void* encoder, NV_ENC_INPUT_PTR inputBuffer) nogil
    ctypedef NVENCSTATUS (*PNVENCCREATEBITSTREAMBUFFER)     (void* encoder, NV_ENC_CREATE_BITSTREAM_BUFFER* createBitstreamBufferParams) nogil
    ctypedef NVENCSTATUS (*PNVENCDESTROYBITSTREAMBUFFER)    (void* encoder, NV_ENC_OUTPUT_PTR bitstreamBuffer) nogil
    ctypedef NVENCSTATUS (*PNVENCENCODEPICTURE)             (void* encoder, NV_ENC_PIC_PARAMS* encodePicParams) nogil
    ctypedef NVENCSTATUS (*PNVENCLOCKBITSTREAM)             (void* encoder, NV_ENC_LOCK_BITSTREAM* lockBitstreamBufferParams) nogil
    ctypedef NVENCSTATUS (*PNVENCUNLOCKBITSTREAM)           (void* encoder, NV_ENC_OUTPUT_PTR bitstreamBuffer) nogil
    ctypedef NVENCSTATUS (*PNVENCLOCKINPUTBUFFER)           (void* encoder, NV_ENC_LOCK_INPUT_BUFFER* lockInputBufferParams) nogil
    ctypedef NVENCSTATUS (*PNVENCUNLOCKINPUTBUFFER)         (void* encoder, NV_ENC_INPUT_PTR inputBuffer) nogil
    ctypedef NVENCSTATUS (*PNVENCGETENCODESTATS)            (void* encoder, NV_ENC_STAT* encodeStats) nogil
    ctypedef NVENCSTATUS (*PNVENCGETSEQUENCEPARAMS)         (void* encoder, NV_ENC_SEQUENCE_PARAM_PAYLOAD* sequenceParamPayload) nogil
    ctypedef NVENCSTATUS (*PNVENCREGISTERASYNCEVENT)        (void* encoder, NV_ENC_EVENT_PARAMS* eventParams) nogil
    ctypedef NVENCSTATUS (*PNVENCUNREGISTERASYNCEVENT)      (void* encoder, NV_ENC_EVENT_PARAMS* eventParams) nogil
    ctypedef NVENCSTATUS (*PNVENCMAPINPUTRESOURCE)          (void* encoder, NV_ENC_MAP_INPUT_RESOURCE* mapInputResParams) nogil
    ctypedef NVENCSTATUS (*PNVENCUNMAPINPUTRESOURCE)        (void* encoder, NV_ENC_INPUT_PTR mappedInputBuffer) nogil
    ctypedef NVENCSTATUS (*PNVENCDESTROYENCODER)            (void* encoder) nogil
    ctypedef NVENCSTATUS (*PNVENCINVALIDATEREFFRAMES)       (void* encoder, uint64_t invalidRefFrameTimeStamp) nogil
    ctypedef NVENCSTATUS (*PNVENCOPENENCODESESSIONEX)       (NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS *openSessionExParams, void** encoder) nogil
    ctypedef NVENCSTATUS (*PNVENCREGISTERRESOURCE)          (void* encoder, NV_ENC_REGISTER_RESOURCE* registerResParams) nogil
    ctypedef NVENCSTATUS (*PNVENCUNREGISTERRESOURCE)        (void* encoder, NV_ENC_REGISTERED_PTR registeredRes) nogil
    ctypedef NVENCSTATUS (*PNVENCRECONFIGUREENCODER)        (void* encoder, NV_ENC_RECONFIGURE_PARAMS* reInitEncodeParams) nogil

    ctypedef struct NV_ENCODE_API_FUNCTION_LIST:
        uint32_t    version         #[in]: Client should pass NV_ENCODE_API_FUNCTION_LIST_VER.
        uint32_t    reserved        #[in]: Reserved and should be set to 0.
        PNVENCOPENENCODESESSION         nvEncOpenEncodeSession
        PNVENCGETENCODEGUIDCOUNT        nvEncGetEncodeGUIDCount
        PNVENCGETENCODEPROFILEGUIDCOUNT nvEncGetEncodeProfileGUIDCount
        PNVENCGETENCODEPROFILEGUIDS     nvEncGetEncodeProfileGUIDs
        PNVENCGETENCODEGUIDS            nvEncGetEncodeGUIDs
        PNVENCGETINPUTFORMATCOUNT       nvEncGetInputFormatCount
        PNVENCGETINPUTFORMATS           nvEncGetInputFormats
        PNVENCGETENCODECAPS             nvEncGetEncodeCaps
        PNVENCGETENCODEPRESETCOUNT      nvEncGetEncodePresetCount
        PNVENCGETENCODEPRESETGUIDS      nvEncGetEncodePresetGUIDs
        PNVENCGETENCODEPRESETCONFIG     nvEncGetEncodePresetConfig
        PNVENCINITIALIZEENCODER         nvEncInitializeEncoder
        PNVENCCREATEINPUTBUFFER         nvEncCreateInputBuffer
        PNVENCDESTROYINPUTBUFFER        nvEncDestroyInputBuffer
        PNVENCCREATEBITSTREAMBUFFER     nvEncCreateBitstreamBuffer
        PNVENCDESTROYBITSTREAMBUFFER    nvEncDestroyBitstreamBuffer
        PNVENCENCODEPICTURE             nvEncEncodePicture
        PNVENCLOCKBITSTREAM             nvEncLockBitstream
        PNVENCUNLOCKBITSTREAM           nvEncUnlockBitstream
        PNVENCLOCKINPUTBUFFER           nvEncLockInputBuffer
        PNVENCUNLOCKINPUTBUFFER         nvEncUnlockInputBuffer
        PNVENCGETENCODESTATS            nvEncGetEncodeStats
        PNVENCGETSEQUENCEPARAMS         nvEncGetSequenceParams
        PNVENCREGISTERASYNCEVENT        nvEncRegisterAsyncEvent
        PNVENCUNREGISTERASYNCEVENT      nvEncUnregisterAsyncEvent
        PNVENCMAPINPUTRESOURCE          nvEncMapInputResource
        PNVENCUNMAPINPUTRESOURCE        nvEncUnmapInputResource
        PNVENCDESTROYENCODER            nvEncDestroyEncoder
        PNVENCINVALIDATEREFFRAMES       nvEncInvalidateRefFrames
        PNVENCOPENENCODESESSIONEX       nvEncOpenEncodeSessionEx
        PNVENCREGISTERRESOURCE          nvEncRegisterResource
        PNVENCUNREGISTERRESOURCE        nvEncUnregisterResource
        PNVENCRECONFIGUREENCODER        nvEncReconfigureEncoder
        void*                           reserved2[285]                  #[in]:  Reserved and must be set to NULL

    #constants:
    unsigned int NVENCAPI_VERSION
    unsigned int NV_ENCODE_API_FUNCTION_LIST_VER
    unsigned int NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER
    unsigned int NV_ENC_INITIALIZE_PARAMS_VER
    unsigned int NV_ENC_PRESET_CONFIG_VER
    unsigned int NV_ENC_CONFIG_VER
    unsigned int NV_ENC_CREATE_INPUT_BUFFER_VER
    unsigned int NV_ENC_CREATE_BITSTREAM_BUFFER_VER
    unsigned int NV_ENC_CAPS_PARAM_VER
    unsigned int NV_ENC_LOCK_INPUT_BUFFER_VER
    unsigned int NV_ENC_LOCK_BITSTREAM_VER
    unsigned int NV_ENC_PIC_PARAMS_VER
    unsigned int NV_ENC_RC_PARAMS_VER
    unsigned int NV_ENC_REGISTER_RESOURCE_VER
    unsigned int NV_ENC_MAP_INPUT_RESOURCE_VER
    unsigned int NVENC_INFINITE_GOPLENGTH
    unsigned int NV_ENC_SUCCESS
    unsigned int NV_ENC_ERR_NO_ENCODE_DEVICE
    unsigned int NV_ENC_ERR_UNSUPPORTED_DEVICE
    unsigned int NV_ENC_ERR_INVALID_ENCODERDEVICE
    unsigned int NV_ENC_ERR_INVALID_DEVICE
    unsigned int NV_ENC_ERR_DEVICE_NOT_EXIST
    unsigned int NV_ENC_ERR_INVALID_PTR
    unsigned int NV_ENC_ERR_INVALID_EVENT
    unsigned int NV_ENC_ERR_INVALID_PARAM
    unsigned int NV_ENC_ERR_INVALID_CALL
    unsigned int NV_ENC_ERR_OUT_OF_MEMORY
    unsigned int NV_ENC_ERR_ENCODER_NOT_INITIALIZED
    unsigned int NV_ENC_ERR_UNSUPPORTED_PARAM
    unsigned int NV_ENC_ERR_LOCK_BUSY
    unsigned int NV_ENC_ERR_NOT_ENOUGH_BUFFER
    unsigned int NV_ENC_ERR_INVALID_VERSION
    unsigned int NV_ENC_ERR_MAP_FAILED
    unsigned int NV_ENC_ERR_NEED_MORE_INPUT
    unsigned int NV_ENC_ERR_ENCODER_BUSY
    unsigned int NV_ENC_ERR_EVENT_NOT_REGISTERD
    unsigned int NV_ENC_ERR_GENERIC
    unsigned int NV_ENC_ERR_INCOMPATIBLE_CLIENT_KEY
    unsigned int NV_ENC_ERR_UNIMPLEMENTED
    unsigned int NV_ENC_ERR_RESOURCE_REGISTER_FAILED
    unsigned int NV_ENC_ERR_RESOURCE_NOT_REGISTERED
    unsigned int NV_ENC_ERR_RESOURCE_NOT_MAPPED

    unsigned int NV_ENC_CAPS_MB_PER_SEC_MAX
    unsigned int NV_ENC_CAPS_SUPPORT_YUV444_ENCODE
    unsigned int NV_ENC_CAPS_SUPPORT_LOSSLESS_ENCODE
    unsigned int NV_ENC_RECONFIGURE_PARAMS_VER


NV_ENC_STATUS_TXT = {
    NV_ENC_SUCCESS : "This indicates that API call returned with no errors.",
    NV_ENC_ERR_NO_ENCODE_DEVICE       : "This indicates that no encode capable devices were detected",
    NV_ENC_ERR_UNSUPPORTED_DEVICE     : "This indicates that devices pass by the client is not supported.",
    NV_ENC_ERR_INVALID_ENCODERDEVICE  : "This indicates that the encoder device supplied by the client is not valid.",
    NV_ENC_ERR_INVALID_DEVICE         : "This indicates that device passed to the API call is invalid.",
    NV_ENC_ERR_DEVICE_NOT_EXIST       : """This indicates that device passed to the API call is no longer available and
 needs to be reinitialized. The clients need to destroy the current encoder
 session by freeing the allocated input output buffers and destroying the device
 and create a new encoding session.""",
    NV_ENC_ERR_INVALID_PTR            : "This indicates that one or more of the pointers passed to the API call is invalid.",
    NV_ENC_ERR_INVALID_EVENT          : "This indicates that completion event passed in ::NvEncEncodePicture() call is invalid.",
    NV_ENC_ERR_INVALID_PARAM          : "This indicates that one or more of the parameter passed to the API call is invalid.",
    NV_ENC_ERR_INVALID_CALL           : "This indicates that an API call was made in wrong sequence/order.",
    NV_ENC_ERR_OUT_OF_MEMORY          : "This indicates that the API call failed because it was unable to allocate enough memory to perform the requested operation.",
    NV_ENC_ERR_ENCODER_NOT_INITIALIZED: """This indicates that the encoder has not been initialized with
::NvEncInitializeEncoder() or that initialization has failed.
The client cannot allocate input or output buffers or do any encoding
related operation before successfully initializing the encoder.""",
    NV_ENC_ERR_UNSUPPORTED_PARAM      : "This indicates that an unsupported parameter was passed by the client.",
    NV_ENC_ERR_LOCK_BUSY              : """This indicates that the ::NvEncLockBitstream() failed to lock the output
buffer. This happens when the client makes a non blocking lock call to
access the output bitstream by passing NV_ENC_LOCK_BITSTREAM::doNotWait flag.
This is not a fatal error and client should retry the same operation after
few milliseconds.""",
    NV_ENC_ERR_NOT_ENOUGH_BUFFER      : "This indicates that the size of the user buffer passed by the client is insufficient for the requested operation.",
    NV_ENC_ERR_INVALID_VERSION        : "This indicates that an invalid struct version was used by the client.",
    NV_ENC_ERR_MAP_FAILED             : "This indicates that ::NvEncMapInputResource() API failed to map the client provided input resource.",
    NV_ENC_ERR_NEED_MORE_INPUT        : """
This indicates encode driver requires more input buffers to produce an output
bitstream. If this error is returned from ::NvEncEncodePicture() API, this
is not a fatal error. If the client is encoding with B frames then,
::NvEncEncodePicture() API might be buffering the input frame for re-ordering.
A client operating in synchronous mode cannot call ::NvEncLockBitstream()
API on the output bitstream buffer if ::NvEncEncodePicture() returned the
::NV_ENC_ERR_NEED_MORE_INPUT error code.
The client must continue providing input frames until encode driver returns
::NV_ENC_SUCCESS. After receiving ::NV_ENC_SUCCESS status the client can call
::NvEncLockBitstream() API on the output buffers in the same order in which
it has called ::NvEncEncodePicture().
""",
    NV_ENC_ERR_ENCODER_BUSY : """This indicates that the HW encoder is busy encoding and is unable to encode
the input. The client should call ::NvEncEncodePicture() again after few milliseconds.""",
    NV_ENC_ERR_EVENT_NOT_REGISTERD : """This indicates that the completion event passed in ::NvEncEncodePicture()
API has not been registered with encoder driver using ::NvEncRegisterAsyncEvent().""",
    NV_ENC_ERR_GENERIC : "This indicates that an unknown internal error has occurred.",
    NV_ENC_ERR_INCOMPATIBLE_CLIENT_KEY  : "This indicates that the client is attempting to use a feature that is not available for the license type for the current system.",
    NV_ENC_ERR_UNIMPLEMENTED : "This indicates that the client is attempting to use a feature that is not implemented for the current version.",
    NV_ENC_ERR_RESOURCE_REGISTER_FAILED : "This indicates that the ::NvEncRegisterResource API failed to register the resource.",
    NV_ENC_ERR_RESOURCE_NOT_REGISTERED : "This indicates that the client is attempting to unregister a resource that has not been successfuly registered.",
    NV_ENC_ERR_RESOURCE_NOT_MAPPED : "This indicates that the client is attempting to unmap a resource that has not been successfuly mapped.",
      }

CAPS_NAMES = {
        NV_ENC_CAPS_NUM_MAX_BFRAMES             : "NUM_MAX_BFRAMES",
        NV_ENC_CAPS_SUPPORTED_RATECONTROL_MODES : "SUPPORTED_RATECONTROL_MODES",
        NV_ENC_CAPS_SUPPORT_FIELD_ENCODING      : "SUPPORT_FIELD_ENCODING",
        NV_ENC_CAPS_SUPPORT_MONOCHROME          : "SUPPORT_MONOCHROME",
        NV_ENC_CAPS_SUPPORT_FMO                 : "SUPPORT_FMO",
        NV_ENC_CAPS_SUPPORT_QPELMV              : "SUPPORT_QPELMV",
        NV_ENC_CAPS_SUPPORT_BDIRECT_MODE        : "SUPPORT_BDIRECT_MODE",
        NV_ENC_CAPS_SUPPORT_CABAC               : "SUPPORT_CABAC",
        NV_ENC_CAPS_SUPPORT_ADAPTIVE_TRANSFORM  : "SUPPORT_ADAPTIVE_TRANSFORM",
        NV_ENC_CAPS_NUM_MAX_TEMPORAL_LAYERS     : "NUM_MAX_TEMPORAL_LAYERS",
        NV_ENC_CAPS_SUPPORT_HIERARCHICAL_PFRAMES: "SUPPORT_HIERARCHICAL_PFRAMES",
        NV_ENC_CAPS_SUPPORT_HIERARCHICAL_BFRAMES: "SUPPORT_HIERARCHICAL_BFRAMES",
        NV_ENC_CAPS_LEVEL_MAX                   : "LEVEL_MAX",
        NV_ENC_CAPS_LEVEL_MIN                   : "LEVEL_MIN",
        NV_ENC_CAPS_SEPARATE_COLOUR_PLANE       : "SEPARATE_COLOUR_PLANE",
        NV_ENC_CAPS_WIDTH_MAX                   : "WIDTH_MAX",
        NV_ENC_CAPS_HEIGHT_MAX                  : "HEIGHT_MAX",
        NV_ENC_CAPS_SUPPORT_TEMPORAL_SVC        : "SUPPORT_TEMPORAL_SVC",
        NV_ENC_CAPS_SUPPORT_DYN_RES_CHANGE      : "SUPPORT_DYN_RES_CHANGE",
        NV_ENC_CAPS_SUPPORT_DYN_BITRATE_CHANGE  : "SUPPORT_DYN_BITRATE_CHANGE",
        NV_ENC_CAPS_SUPPORT_DYN_FORCE_CONSTQP   : "SUPPORT_DYN_FORCE_CONSTQP",
        NV_ENC_CAPS_SUPPORT_DYN_RCMODE_CHANGE   : "SUPPORT_DYN_RCMODE_CHANGE",
        NV_ENC_CAPS_SUPPORT_SUBFRAME_READBACK   : "SUPPORT_SUBFRAME_READBACK",
        NV_ENC_CAPS_SUPPORT_CONSTRAINED_ENCODING: "SUPPORT_CONSTRAINED_ENCODING",
        NV_ENC_CAPS_SUPPORT_INTRA_REFRESH       : "SUPPORT_INTRA_REFRESH",
        NV_ENC_CAPS_SUPPORT_CUSTOM_VBV_BUF_SIZE : "SUPPORT_CUSTOM_VBV_BUF_SIZE",
        NV_ENC_CAPS_SUPPORT_DYNAMIC_SLICE_MODE  : "SUPPORT_DYNAMIC_SLICE_MODE",
        NV_ENC_CAPS_SUPPORT_REF_PIC_INVALIDATION: "SUPPORT_REF_PIC_INVALIDATION",
        NV_ENC_CAPS_PREPROC_SUPPORT             : "PREPROC_SUPPORT",
        NV_ENC_CAPS_ASYNC_ENCODE_SUPPORT        : "ASYNC_ENCODE_SUPPORT",
        NV_ENC_CAPS_MB_NUM_MAX                  : "MB_NUM_MAX",
        NV_ENC_CAPS_EXPOSED_COUNT               : "EXPOSED_COUNT",
        NV_ENC_CAPS_SUPPORT_YUV444_ENCODE       : "SUPPORT_YUV444_ENCODE",
        NV_ENC_CAPS_SUPPORT_LOSSLESS_ENCODE     : "SUPPORT_LOSSLESS_ENCODE",
        }


NvEncodeAPICreateInstance = None
cuCtxGetCurrent = None

def init_nvencode_library():
    global NvEncodeAPICreateInstance, cuCtxGetCurrent
    if sys.platform.startswith("win"):
        load = ctypes.WinDLL
        nvenc_libname = "nvencodeapi.dll"
        cuda_libname = "nvcuda.dll"
    else:
        #assert os.name=="posix"
        load = ctypes.cdll.LoadLibrary
        nvenc_libname = "libnvidia-encode.so"
        cuda_libname = "libcuda.so"
    #CUDA:
    log("init_nvencode_library() will try to load %s", cuda_libname)
    try:
        x = load(cuda_libname)
        log("init_nvencode_library() %s(%s)=%s", load, cuda_libname, x)
    except Exception as e:
        raise Exception("nvenc: the required library %s cannot be loaded: %s" % (cuda_libname, e))
    cuCtxGetCurrent = x.cuCtxGetCurrent
    cuCtxGetCurrent.restype = ctypes.c_int          # CUresult == int
    cuCtxGetCurrent.argtypes = [POINTER(CUcontext)] # CUcontext *pctx
    log("init_nvencode_library() %s.cuCtxGetCurrent=%s", os.path.splitext(cuda_libname)[0], cuCtxGetCurrent)
    #nvidia-encode:
    log("init_nvencode_library() will try to load %s", nvenc_libname)
    try:
        x = load(nvenc_libname)
        log("init_nvencode_library() %s(%s)=%s", load, nvenc_libname, x)
    except Exception as e:
        raise Exception("nvenc: the required library %s cannot be loaded: %s" % (nvenc_libname, e))
    NvEncodeAPICreateInstance = x.NvEncodeAPICreateInstance
    NvEncodeAPICreateInstance.restype = ctypes.c_int
    NvEncodeAPICreateInstance.argtypes = [ctypes.c_void_p]
    log("init_nvencode_library() NvEncodeAPICreateInstance=%s", NvEncodeAPICreateInstance)
    #NVENCSTATUS NvEncodeAPICreateInstance(NV_ENCODE_API_FUNCTION_LIST *functionList)


cdef guidstr(GUID guid):
    #really ugly! (surely there's a way using struct.unpack ?)
    #is this even endian safe? do we care? (always on the same system)
    parts = []
    for v, s in ((guid.Data1, 4), (guid.Data2, 2), (guid.Data3, 2)):
        b = array.array('B', [0 for _ in range(s)])
        for j in range(s):
            b[s-j-1] = v % 256
            v = v / 256
        parts.append(b.tostring())
    parts.append(array.array('B', (guid.get("Data4")[:2])).tostring())
    parts.append(array.array('B', (guid.get("Data4")[2:8])).tostring())
    s = "-".join([binascii.hexlify(b).upper() for b in parts])
    #log.info("guidstr(%s)=%s", guid, s)
    return s

cdef GUID c_parseguid(src) except *:
    #just as ugly as above - shoot me now
    #only this format is allowed:
    sample_guid = "CE788D20-AAA9-4318-92BB-AC7E858C8D36"
    if len(src)!=len(sample_guid):
        raise Exception("invalid GUID format: expected %s characters but got %s" % (len(sample_guid), len(src)))
    for i in range(len(sample_guid)):
        if sample_guid[i]=="-":
            #dash must be in the same place:
            if src[i]!="-":
                raise Exception("invalid GUID format: character at position %s is not '-'" % i)
        else:
            #must be an hex number:
            if src.upper()[i] not in ("0123456789ABCDEF"):
                raise Exception("invalid GUID format: character at position %s is not in hex" % i)
    parts = src.split("-")    #ie: ["CE788D20", "AAA9", ...]
    nparts = []
    for i, s in (0, 4), (1, 2), (2, 2), (3, 2), (4, 6):
        b = array.array('B', (binascii.unhexlify(parts[i]))).tostring()
        v = 0
        for j in range(s):
            v += ord(b[j])<<((s-j-1)*8)
        nparts.append(v)
    cdef GUID guid
    guid.Data1 = nparts[0]
    guid.Data2 = nparts[1]
    guid.Data3 = nparts[2]
    v = (nparts[3]<<48) + nparts[4]
    for i in range(8):
        guid.Data4[i] = (v>>((7-i)*8)) % 256
    return guid

def parseguid(s):
    return c_parseguid(s)

def test_parse():
    sample_guid = "CE788D20-AAA9-4318-92BB-AC7E858C8D36"
    x = c_parseguid(sample_guid)
    v = guidstr(x)
    assert v==sample_guid, "expected %s but got %s" % (sample_guid, v)
test_parse()


CODEC_GUIDS = {
    guidstr(NV_ENC_CODEC_H264_GUID)         : "H264",
    guidstr(NV_ENC_CODEC_HEVC_GUID)         : "HEVC",
    }

cdef codecstr(GUID guid):
    s = guidstr(guid)
    return CODEC_GUIDS.get(s, s)


CODEC_PROFILES_GUIDS = {
    guidstr(NV_ENC_CODEC_H264_GUID) : {
        guidstr(NV_ENC_CODEC_PROFILE_AUTOSELECT_GUID)       : "auto",
        guidstr(NV_ENC_H264_PROFILE_BASELINE_GUID)          : "baseline",
        guidstr(NV_ENC_H264_PROFILE_MAIN_GUID)              : "main",
        guidstr(NV_ENC_H264_PROFILE_HIGH_GUID)              : "high",
        guidstr(NV_ENC_H264_PROFILE_STEREO_GUID)            : "stereo",
        guidstr(NV_ENC_H264_PROFILE_SVC_TEMPORAL_SCALABILTY): "temporal",
        guidstr(NV_ENC_H264_PROFILE_PROGRESSIVE_HIGH_GUID)  : "progressive-high",
        guidstr(NV_ENC_H264_PROFILE_CONSTRAINED_HIGH_GUID)  : "constrained-high",
        #new in SDK v4:
        guidstr(NV_ENC_H264_PROFILE_HIGH_444_GUID)          : "high-444",
        },
    guidstr(NV_ENC_CODEC_HEVC_GUID) : {
        guidstr(NV_ENC_HEVC_PROFILE_MAIN_GUID)              : "main",
        },
    }

#this one is not defined anywhere but in the OBS source
#(I think they have access to information we do not have):
#GUID NV_ENC_PRESET_STREAMING = c_parseguid("7ADD423D-D035-4F6F-AEA5-50885658643C")

CODEC_PRESETS_GUIDS = {
    guidstr(NV_ENC_PRESET_DEFAULT_GUID)                     : "default",
    guidstr(NV_ENC_PRESET_HP_GUID)                          : "hp",
    guidstr(NV_ENC_PRESET_HQ_GUID)                          : "hq",
    guidstr(NV_ENC_PRESET_BD_GUID)                          : "bd",
    guidstr(NV_ENC_PRESET_LOW_LATENCY_DEFAULT_GUID)         : "low-latency",
    guidstr(NV_ENC_PRESET_LOW_LATENCY_HQ_GUID)              : "low-latency-hq",
    guidstr(NV_ENC_PRESET_LOW_LATENCY_HP_GUID)              : "low-latency-hp",
    #new in SDK4:
    guidstr(NV_ENC_PRESET_LOSSLESS_DEFAULT_GUID)            : "lossless",
    guidstr(NV_ENC_PRESET_LOSSLESS_HP_GUID)                 : "lossless-hp",
    "7ADD423D-D035-4F6F-AEA5-50885658643C"                  : "streaming",
    }

YUV444_PRESETS = ("high-444", "lossless", "lossless-hp",)
LOSSLESS_PRESETS = ("lossless", "lossless-hp",)

cdef presetstr(GUID preset):
    s = guidstr(preset)
    return CODEC_PRESETS_GUIDS.get(s, s)


#try to map preset names to a "speed" value:
PRESET_SPEED = {
    "lossless"      : 0,
    "lossless-hp"   : 30,
    "bd"            : 40,
    "hq"            : 50,
    "default"       : 50,
    "hp"            : 60,
    "low-latency-hq": 70,
    "low-latency"   : 80,
    "low-latency-hp": 100,
    "streaming"     : -1000,    #disabled for now
    }
PRESET_QUALITY = {
    "lossless"      : 100,
    "lossless-hp"   : 100,
    "bd"            : 80,
    "hq"            : 70,
    "default"       : 50,
    "hp"            : 40,
    "low-latency-hq": 30,
    "low-latency"   : 20,
    "low-latency-hp": 0,
    "streaming"     : -1000,    #disabled for now
    }



BUFFER_FORMAT = {
        NV_ENC_BUFFER_FORMAT_UNDEFINED              : "undefined",
        NV_ENC_BUFFER_FORMAT_NV12_PL                : "NV12_PL",
        NV_ENC_BUFFER_FORMAT_YV12_PL                : "YV12_PL",
        NV_ENC_BUFFER_FORMAT_IYUV_PL                : "IYUV_PL",
        NV_ENC_BUFFER_FORMAT_YUV444_PL              : "YUV444_PL",
        NV_ENC_BUFFER_FORMAT_ARGB                   : "ARGB",
        NV_ENC_BUFFER_FORMAT_ARGB10                 : "ARGB10",
        NV_ENC_BUFFER_FORMAT_AYUV                   : "AYUV",
        }


YUV444_ENABLED = None

def get_COLORSPACES():
    global YUV444_ENABLED
    if YUV444_ENABLED:
        COLORSPACES = {"BGRX" : ("YUV420P", "YUV444P")}
    else:
        COLORSPACES = {"BGRX" : ("YUV420P",)}
    return COLORSPACES

def get_input_colorspaces(encoding):
    assert encoding in get_encodings()
    return get_COLORSPACES().keys()

def get_output_colorspaces(encoding, input_colorspace):
    assert encoding in get_encodings()
    out = get_COLORSPACES().get(input_colorspace)
    assert out, "invalid input colorspace: %s (must be one of: %s)" % (input_colorspace, get_COLORSPACES())
    #the output will actually be in one of those two formats once decoded
    #because internally that's what we convert to before encoding
    #(well, NV12... which is equivallent to YUV420P here...)
    return out


WIDTH_MASK = 0xFFFE
HEIGHT_MASK = 0xFFFE

#Note: these counters should be per-device, but
#when we call get_runtime_factor(), we don't know which device is going to get used!
#since we have load balancing, using an overall factor isn't too bad
context_counter = AtomicInteger()
context_gen_counter = AtomicInteger()
last_context_failure = 0

def get_runtime_factor():
    global last_context_failure, context_counter
    device_count = len(init_all_devices())
    max_contexts = 32 * device_count
    cc = context_counter.get()
    #try to avoid using too many contexts
    #(usually, we can have up to 32 contexts per card)
    low_limit = 16
    f = max(0, 1.0 - (max(0, cc-low_limit)/max(1, max_contexts-low_limit)))
    #if we have had errors recently, lower our chances further:
    failure_elapsed = time.time()-last_context_failure
    #discount factor gradually for 1 minute:
    f /= 61-min(60, failure_elapsed)
    log("nvenc.get_runtime_factor()=%s", f)
    return f

def get_spec(encoding, colorspace):
    assert encoding in get_encodings(), "invalid format: %s (must be one of %s" % (encoding, get_encodings())
    assert colorspace in get_COLORSPACES(), "invalid colorspace: %s (must be one of %s)" % (colorspace, get_COLORSPACES())
    #ratings: quality, speed, setup cost, cpu cost, gpu cost, latency, max_w, max_h
    cs = video_spec(encoding=encoding, output_colorspaces=get_COLORSPACES()[colorspace], has_lossless_mode=LOSSLESS_ENABLED,
                      codec_class=Encoder, codec_type=get_type(),
                      quality=80, speed=100, setup_cost=80, cpu_cost=10, gpu_cost=100,
                      #using a hardware encoder for something this small is silly:
                      min_w=32, min_h=32,
                      max_w=4096, max_h=4096,
                      can_scale=True,
                      width_mask=WIDTH_MASK, height_mask=HEIGHT_MASK)
    cs.get_runtime_factor = get_runtime_factor
    return cs

#ie: NVENCAPI_VERSION=0x30 -> PRETTY_VERSION = [3, 0]
PRETTY_VERSION = [int(NVENCAPI_VERSION), int(NVENCAPI_VERSION)>>24, 0]

def get_version():
    return ".".join((str(x) for x in PRETTY_VERSION))

def get_type():
    return "nvenc"

def get_info():
    info = {"version"          : PRETTY_VERSION}
    #only show the version if we have it already (don't probe now)
    v = get_nvidia_module_version(False)
    if v:
        info["kernel_module_version"] = v
    if sys.platform.startswith("linux"):
        from xpra.scripts.config import python_platform
        info["kernel_version"] = python_platform.uname()[2]
    global last_context_failure, context_counter, context_gen_counter
    info["device_count"] = len(get_devices() or [])
    info["context_count"] = context_counter.get()
    info["generation"] = context_gen_counter.get()
    if last_context_failure>0:
        info["last_failure"] = int(time.time()-last_context_failure)
    return info


def get_encodings():
    return ["h264"]

cdef inline int roundup(int n, int m):
    return (n + m - 1) & ~(m - 1)


cdef nvencStatusInfo(NVENCSTATUS ret):
    if ret in NV_ENC_STATUS_TXT:
        return "%s: %s" % (ret, NV_ENC_STATUS_TXT[ret])
    return str(ret)

class NVENCException(Exception):
    def __init__(self, code, fn):
        self.function = fn
        self.code = code
        msg = "%s - returned %s" % (fn, nvencStatusInfo(code))
        Exception.__init__(self, msg)

cdef raiseNVENC(NVENCSTATUS ret, msg):
    if ret!=0:
        raise NVENCException(ret, msg)

cpdef get_CUDA_CSC_function(int device_id, function_name):
    return function_name, get_CUDA_function(device_id, function_name)


cpdef get_BGRA2NV12(int device_id):
    return get_CUDA_CSC_function(device_id, "BGRA_to_NV12")

cpdef get_BGRA2YUV444(int device_id):
    return get_CUDA_CSC_function(device_id, "BGRA_to_YUV444")


cdef class Encoder:
    cdef unsigned int width
    cdef unsigned int height
    cdef unsigned int input_width
    cdef unsigned int input_height
    cdef unsigned int encoder_width
    cdef unsigned int encoder_height
    cdef object src_format
    cdef object dst_formats
    cdef object scaling
    cdef int speed
    cdef int quality
    cdef uint32_t target_bitrate
    cdef uint32_t max_bitrate
    #PyCUDA:
    cdef object driver
    cdef int cuda_device_id
    cdef object cuda_info
    cdef object pycuda_info
    cdef object cuda_device_info
    cdef object cuda_device
    cdef object cuda_context
    cdef void *cuda_context_ptr
    cdef object kernel
    cdef object kernel_name
    cdef object max_block_sizes
    cdef object max_grid_sizes
    cdef unsigned long max_threads_per_block
    cdef uint64_t free_memory
    cdef uint64_t total_memory
    #NVENC:
    cdef NV_ENCODE_API_FUNCTION_LIST *functionList               #@DuplicatedSignature
    cdef void *context
    cdef GUID codec
    cdef NV_ENC_REGISTERED_PTR inputHandle
    cdef object inputBuffer
    cdef object cudaInputBuffer
    cdef object cudaOutputBuffer
    cdef unsigned int inputPitch                    #note: this isn't the pitch (aka rowstride) we actually use!
                                                    #just the value returned from the allocation call
    cdef unsigned int outputPitch
    cdef void *bitstreamBuffer
    cdef NV_ENC_BUFFER_FORMAT bufferFmt
    cdef object codec_name
    cdef object preset_name
    cdef object pixel_format
    cdef uint8_t lossless
    #statistics, etc:
    cdef double time
    cdef uint64_t first_frame_timestamp
    cdef unsigned long frames
    cdef unsigned long index
    cdef object last_frame_times
    cdef uint64_t bytes_in
    cdef uint64_t bytes_out

    cdef object __weakref__

    cdef GUID init_codec(self) except *:
        codecs = self.query_codecs()
        #codecs={'H264': '6BC82762-4E63-4CA4-AA85-1E50F321F6BF'}
        internal_name = self.codec_name.upper()
        guid_str = codecs.get(internal_name)
        assert guid_str, "%s not supported! (only available: %s)" % (self.codec_name, csv(codecs.keys()))
        self.codec = c_parseguid(guid_str)
        return self.codec

    cdef GUID get_codec(self):
        return self.codec

    cdef GUID get_preset(self, GUID codec) except *:
        presets = self.query_presets(codec)
        #import traceback
        #traceback.print_stack()
        options = {}
        #if a preset was specified, give it the best score possible (-1):
        if DESIRED_PRESET:
            options[-1] = DESIRED_PRESET
        #add all presets ranked by how far they are from the target speed and quality:
        for x in CODEC_PRESETS_GUIDS.values():
            preset_speed = PRESET_SPEED.get(x, 50)
            preset_quality = PRESET_QUALITY.get(x, 50)
            #log("%s speed=%s, quality=%s, lossless=%s", x, preset_speed, preset_quality, x in LOSSLESS_PRESETS)
            if x in LOSSLESS_PRESETS and self.pixel_format!="YUV444P":
                continue
            if self.lossless and (x not in LOSSLESS_PRESETS):
                continue
            if not self.lossless and (x in LOSSLESS_PRESETS):
                continue
            if preset_speed>=0 and preset_quality>=0:
                #quality (3) weighs more than speed (2):
                v = 2 * abs(preset_speed-self.speed) + 3 * abs(preset_quality-self.quality)
                l = options.setdefault(v, [])
                if x not in l:
                    l.append(x)
        log("get_preset(%s) speed=%s, quality=%s, lossless=%s, pixel_format=%s, options=%s", codecstr(codec), self.speed, self.quality, bool(self.lossless), self.pixel_format, options)
        for v in sorted(options.keys()):
            for preset in options.get(v):
                if preset and (preset in presets):
                    log("using preset '%s' for quality=%s, speed=%s, lossless=%s, pixel_format=%s", preset, self.speed, self.quality, self.lossless, self.pixel_format)
                    return c_parseguid(presets.get(preset))
        raise Exception("no matching presets available for '%s'!?" % self.codec_name)

    def init_context(self, int width, int height, src_format, dst_formats, encoding, int quality, int speed, scaling, options={}):    #@DuplicatedSignature
        assert NvEncodeAPICreateInstance is not None, "encoder module is not initialized"
        log("init_context%s", (width, height, src_format, dst_formats, encoding, quality, speed, scaling, options))
        self.width = width
        self.height = height
        self.speed = speed
        self.quality = quality
        self.scaling = scaling or (1, 1)
        v, u = self.scaling
        self.input_width = roundup(width, 32)
        self.input_height = roundup(height, 32)
        self.encoder_width = roundup(width*v//u, 32)
        self.encoder_height = roundup(height*v//u, 32)
        self.src_format = src_format
        self.dst_formats = dst_formats
        self.codec_name = encoding.upper()      #ie: "H264"
        self.preset_name = None
        self.frames = 0
        self.cuda_device = None
        self.cuda_context = None
        self.pixel_format = ""
        self.last_frame_times = deque(maxlen=200)
        self.update_bitrate()
        start = time.time()

        self.pixel_format = self.get_target_pixel_format(self.quality)
        self.lossless = self.get_target_lossless(self.pixel_format, self.quality)
        self.cuda_device_id, self.cuda_device = select_device(options.get("cuda_device", -1), min_compute=MIN_COMPUTE)
        log("using %s %s compression at %s%% quality with pixel format %s on device %s", ["lossy","lossless"][self.lossless], encoding, self.quality, self.pixel_format, device_info(self.cuda_device))
        try:
            self.init_cuda()

            #the example code accesses the cuda context after a context.pop()
            #(which is weird)
            self.init_nvenc()

            record_device_success(self.cuda_device_id)
        except Exception as e:
            log("init_cuda failed", exc_info=True)
            record_device_failure(self.cuda_device_id)
            raise

        end = time.time()
        log("init_context%s took %1.fms", (width, height, src_format, quality, speed, options), (end-start)*1000.0)

    def get_target_pixel_format(self, quality):
        global YUV444_ENABLED, LOSSLESS_ENABLED
        x,y = self.scaling
        if YUV444_ENABLED and ("YUV444P" in self.dst_formats) and ((quality>=YUV444_THRESHOLD and x==1 and y==1) or ("YUV420P" not in self.dst_formats)):
            return "YUV444P"
        elif "YUV420P" in self.dst_formats:
            return "NV12"
        else:
            raise Exception("no compatible formats found!")

    def get_target_lossless(self, pixel_format, quality):
        #log("get_target_lossless(%s, %s) LOSSLESS_ENABLED=%s", pixel_format, quality, LOSSLESS_ENABLED)
        if pixel_format!="YUV444P":
            return False
        if LOSSLESS_ENABLED and quality>=LOSSLESS_THRESHOLD:
            return True
        return False

    cdef init_cuda(self):
        cdef unsigned int plane_size_div
        cdef unsigned int max_input_stride
        cdef int result

        assert self.cuda_device_id>=0 and self.cuda_device, "no NVENC device found!"
        global last_context_failure
        d = self.cuda_device
        cf = driver.ctx_flags
        log("init_cuda() pixel format=%s, device_id=%s", self.pixel_format, self.cuda_device_id)
        try:
            log("init_cuda cuda_device=%s (%s)", d, device_info(d))
            self.cuda_context = d.make_context(flags=cf.SCHED_AUTO | cf.MAP_HOST)
            log("init_cuda cuda_context=%s", self.cuda_context)
            self.cuda_info = get_cuda_info()
            log("init_cuda cuda info=%s", self.cuda_info)
            self.pycuda_info = get_pycuda_info()
            log("init_cuda pycuda info=%s", self.pycuda_info)
            self.cuda_device_info = {
                "device.name"       : d.name(),
                "device.pci_bus_id" : d.pci_bus_id(),
                "device.memory"     : d.total_memory()/1024/1024,
                "api_version"       : self.cuda_context.get_api_version()}
        except driver.MemoryError as e:
            last_context_failure = time.time()
            log("init_cuda %s", e)
            raise TransientCodecException("could not initialize cuda: %s" % e)
        #use alias to make code easier to read:
        da = driver.device_attribute
        try:
            #if supported (separate plane flag), use YUV444P:
            if self.pixel_format=="YUV444P":
                kernel_gen = get_BGRA2YUV444
                self.bufferFmt = NV_ENC_BUFFER_FORMAT_YUV444_PL
                #3 full planes:
                plane_size_div = 1
            elif self.pixel_format=="NV12":
                kernel_gen = get_BGRA2NV12
                self.bufferFmt = NV_ENC_BUFFER_FORMAT_NV12_PL
                #1 full Y plane and 2 U+V planes subsampled by 4:
                plane_size_div = 2
            else:
                raise Exception("BUG: invalid dst format: %s" % self.pixel_format)

            #load the kernel:
            self.kernel_name, self.kernel = kernel_gen(self.cuda_device_id)
            assert self.kernel, "failed to load %s for device %i" % (self.kernel_name, self.cuda_device_id)

            #allocate CUDA input buffer (on device) 32-bit RGB
            #(and make it bigger just in case - subregions from XShm can have a huge rowstride):
            max_input_stride = MAX(2560, self.input_width)*4
            self.cudaInputBuffer, self.inputPitch = driver.mem_alloc_pitch(max_input_stride, self.input_height, 16)
            log("CUDA Input Buffer=%#x, pitch=%s", int(self.cudaInputBuffer), self.inputPitch)
            #allocate CUDA output buffer (on device):
            self.cudaOutputBuffer, self.outputPitch = driver.mem_alloc_pitch(self.encoder_width, self.encoder_height*3//plane_size_div, 16)
            log("CUDA Output Buffer=%#x, pitch=%s", int(self.cudaOutputBuffer), self.outputPitch)
            #allocate input buffer on host:
            self.inputBuffer = driver.pagelocked_zeros(self.inputPitch*self.input_height, dtype=numpy.byte)
            log("inputBuffer=%s (size=%s)", self.inputBuffer, self.inputPitch*self.input_height)

            self.max_block_sizes = d.get_attribute(da.MAX_BLOCK_DIM_X), d.get_attribute(da.MAX_BLOCK_DIM_Y), d.get_attribute(da.MAX_BLOCK_DIM_Z)
            self.max_grid_sizes = d.get_attribute(da.MAX_GRID_DIM_X), d.get_attribute(da.MAX_GRID_DIM_Y), d.get_attribute(da.MAX_GRID_DIM_Z)
            log("max_block_sizes=%s", self.max_block_sizes)
            log("max_grid_sizes=%s", self.max_grid_sizes)

            self.max_threads_per_block = self.kernel.get_attribute(driver.function_attribute.MAX_THREADS_PER_BLOCK)
            log("max_threads_per_block=%s", self.max_threads_per_block)

            #get the CUDA context (C pointer):
            #a bit of magic to pass a cython pointer to ctypes:
            context_pointer = <unsigned long> (&self.cuda_context_ptr)
            result = cuCtxGetCurrent(ctypes.cast(context_pointer, POINTER(ctypes.c_void_p)))
            if DEBUG_API:
                log("cuCtxGetCurrent() returned %s, cuda context pointer=%#x", CUDA_ERRORS_INFO.get(result, result), <unsigned long> self.cuda_context_ptr)
            assert result==0, "failed to get current cuda context, cuCtxGetCurrent returned %s" % CUDA_ERRORS_INFO.get(result, result)
        finally:
            self.cuda_context.pop()

    cdef init_nvenc(self):
        cdef NV_ENC_INITIALIZE_PARAMS *params = NULL
        cdef NVENCSTATUS r

        self.functionList = <NV_ENCODE_API_FUNCTION_LIST*> malloc(sizeof(NV_ENCODE_API_FUNCTION_LIST))
        assert memset(self.functionList, 0, sizeof(NV_ENCODE_API_FUNCTION_LIST))!=NULL
        log("init_nvenc() functionList=%#x", <unsigned long> self.functionList)

        self.open_encode_session()

        cdef GUID codec = self.init_codec()
        params = <NV_ENC_INITIALIZE_PARAMS*> malloc(sizeof(NV_ENC_INITIALIZE_PARAMS))
        assert params!=NULL
        assert memset(params, 0, sizeof(NV_ENC_INITIALIZE_PARAMS))!=NULL
        try:
            self.init_params(codec, params)
            if DEBUG_API:
                log("nvEncInitializeEncoder using encode=%s", codecstr(codec))
            with nogil:
                r = self.functionList.nvEncInitializeEncoder(self.context, params)
            raiseNVENC(r, "initializing encoder")
            log("NVENC initialized with '%s' codec and '%s' preset" % (self.codec_name, self.preset_name))

            self.dump_caps(self.codec_name, codec)
            self.init_buffers()
        finally:
            if params.encodeConfig!=NULL:
                free(params.encodeConfig)
            free(params)

    cdef dump_caps(self, codec_name, GUID codec):
        #test all caps:
        caps = {}
        for cap, descr in CAPS_NAMES.items():
            if cap!=NV_ENC_CAPS_EXPOSED_COUNT:
                v = self.query_encoder_caps(codec, cap)
                caps[descr] = v
        log("caps(%s)=%s", codec_name, caps)

    cdef NV_ENC_INITIALIZE_PARAMS *init_params(self, GUID codec, NV_ENC_INITIALIZE_PARAMS *params) except *:
        #caller must free the config!
        cdef GUID preset
        cdef NV_ENC_CONFIG *config = NULL
        cdef NV_ENC_PRESET_CONFIG *presetConfig = NULL  #@DuplicatedSignature

        preset = self.get_preset(self.codec)
        self.preset_name = CODEC_PRESETS_GUIDS.get(guidstr(preset), guidstr(preset))
        log("init_params(%s) using preset=%s", codecstr(codec), presetstr(preset))

        input_format = BUFFER_FORMAT[self.bufferFmt]
        input_formats = self.query_input_formats(codec)
        assert input_format in input_formats, "%s does not support %s (only: %s)" %  (self.codec_name, input_format, input_formats)

        assert memset(params, 0, sizeof(NV_ENC_INITIALIZE_PARAMS))!=NULL
        config = <NV_ENC_CONFIG*> malloc(sizeof(NV_ENC_CONFIG))

        presetConfig = self.get_preset_config(self.preset_name, codec, preset)
        assert presetConfig!=NULL, "could not find preset %s" % self.preset_name
        try:
            assert memcpy(config, &presetConfig.presetCfg, sizeof(NV_ENC_CONFIG))!=NULL

            params.version = NV_ENC_INITIALIZE_PARAMS_VER
            params.encodeGUID = codec
            params.presetGUID = preset
            params.encodeWidth = self.encoder_width
            params.encodeHeight = self.encoder_height
            params.darWidth = self.encoder_width
            params.darHeight = self.encoder_height
            params.enableEncodeAsync = 0            #not supported on Linux
            params.enablePTD = 0                    #not supported in sync mode!?
            params.frameRateNum = 1
            params.frameRateDen = 30
            params.encodeConfig = config

            #3 for YUV444, 1 for NV12:
            config.encodeCodecConfig.h264Config.chromaFormatIDC = 1+int(self.pixel_format=="YUV444P") * 2
            #config.rcParams.rateControlMode = NV_ENC_PARAMS_RC_VBR     #FIXME: check NV_ENC_CAPS_SUPPORTED_RATECONTROL_MODES caps
            #config.rcParams.enableMinQP = 1
            #config.rcParams.enableMaxQP = 1
            config.frameIntervalP = 1
            config.gopLength = NVENC_INFINITE_GOPLENGTH
            #0=max quality, 63 lowest quality
            #qmin = QP_MAX_VALUE-min(QP_MAX_VALUE, int(QP_MAX_VALUE*(self.quality+20)//100))
            #qmax = QP_MAX_VALUE-max(0, int(QP_MAX_VALUE*self.quality//100))
            #if self.lossless:
            #    config.encodeCodecConfig.h264Config.qpPrimeYZeroTransformBypassFlag = 1
            #    config.rcParams.rateControlMode = NV_ENC_PARAMS_RC_CONSTQP
            #    qmin = 0
            #    qmax = 0
            #config.rcParams.minQP.qpInterB = qmin
            #config.rcParams.minQP.qpInterP = qmin
            #config.rcParams.minQP.qpIntra = qmin
            #config.rcParams.maxQP.qpInterB = qmax
            #config.rcParams.maxQP.qpInterP = qmax
            #config.rcParams.maxQP.qpIntra = qmax
            #config.rcParams.averageBitRate = self.target_bitrate
            #config.rcParams.maxBitRate = self.max_bitrate
            config.frameFieldMode = NV_ENC_PARAMS_FRAME_FIELD_MODE_FRAME

            config.encodeCodecConfig.h264Config.h264VUIParameters.colourDescriptionPresentFlag = 0
            config.encodeCodecConfig.h264Config.h264VUIParameters.videoSignalTypePresentFlag = 0
            #config.encodeCodecConfig.h264Config.h264VUIParameters.colourMatrix = 1      #AVCOL_SPC_BT709 ?
            #config.encodeCodecConfig.h264Config.h264VUIParameters.colourPrimaries = 1   #AVCOL_PRI_BT709 ?
            #config.encodeCodecConfig.h264Config.h264VUIParameters.transferCharacteristics = 1   #AVCOL_TRC_BT709 ?
            #config.encodeCodecConfig.h264Config.h264VUIParameters.videoFullRangeFlag = 1
            return params
        finally:
            if presetConfig!=NULL:
                free(presetConfig)

    def init_buffers(self):
        cdef NV_ENC_REGISTER_RESOURCE registerResource
        cdef NV_ENC_CREATE_INPUT_BUFFER createInputBufferParams
        cdef NV_ENC_CREATE_BITSTREAM_BUFFER createBitstreamBufferParams
        cdef long resource
        cdef Py_ssize_t size
        cdef unsigned char* cptr = NULL
        cdef NVENCSTATUS r                  #
        #register CUDA input buffer:
        memset(&registerResource, 0, sizeof(NV_ENC_REGISTER_RESOURCE))
        registerResource.version = NV_ENC_REGISTER_RESOURCE_VER
        registerResource.resourceType = NV_ENC_INPUT_RESOURCE_TYPE_CUDADEVICEPTR
        resource = int(self.cudaOutputBuffer)
        registerResource.resourceToRegister = <void *> resource
        registerResource.width = self.encoder_width
        registerResource.height = self.encoder_height
        registerResource.pitch = self.outputPitch
        registerResource.bufferFormat = self.bufferFmt
        if DEBUG_API:
            log("nvEncRegisterResource(%#x)", <unsigned long> &registerResource)
        with nogil:
            r = self.functionList.nvEncRegisterResource(self.context, &registerResource)
        raiseNVENC(r, "registering CUDA input buffer")
        self.inputHandle = registerResource.registeredResource
        log("input handle for CUDA buffer: %#x", <unsigned long> self.inputHandle)

        #allocate output buffer:
        memset(&createBitstreamBufferParams, 0, sizeof(NV_ENC_CREATE_BITSTREAM_BUFFER))
        createBitstreamBufferParams.version = NV_ENC_CREATE_BITSTREAM_BUFFER_VER
        #this is the uncompressed size - must be big enough for the compressed stream:
        createBitstreamBufferParams.size = min(1024*1024*2, self.encoder_width*self.encoder_height*3//2)
        createBitstreamBufferParams.memoryHeap = NV_ENC_MEMORY_HEAP_SYSMEM_CACHED
        if DEBUG_API:
            log("nvEncCreateBitstreamBuffer(%#x)", <unsigned long> &createBitstreamBufferParams)
        with nogil:
            r = self.functionList.nvEncCreateBitstreamBuffer(self.context, &createBitstreamBufferParams)
        raiseNVENC(r, "creating output buffer")
        self.bitstreamBuffer = createBitstreamBufferParams.bitstreamBuffer
        log("output bitstream buffer=%#x", <unsigned long> self.bitstreamBuffer)


    def get_info(self):                     #@DuplicatedSignature
        cdef double pps
        info = get_info()
        info.update({
                "width"     : self.width,
                "height"    : self.height,
                "frames"    : self.frames,
                "codec"     : self.codec_name,
                "encoder_width"     : self.encoder_width,
                "encoder_height"    : self.encoder_height,
                "bitrate"           : self.target_bitrate,
                "quality"           : self.quality,
                "speed"             : self.speed,
                "lossless"          : self.lossless,
                "lossless.supported": LOSSLESS_ENABLED,
                "lossless.threshold": LOSSLESS_THRESHOLD,
                "yuv444.supported"  : YUV444_ENABLED,
                "yuv444.threshold"  : YUV444_THRESHOLD,
                })
        if self.scaling!=(1,1):
            info.update({
                "input_width"       : self.input_width,
                "input_height"      : self.input_height,
                "scaling"           : self.scaling})
        updict(info, "cuda", self.cuda_device_info)
        updict(info, "cuda", self.cuda_info)
        updict(info, "pycuda", self.pycuda_info)
        if self.src_format:
            info["src_format"] = self.src_format
        if self.pixel_format:
            info["pixel_format"] = self.pixel_format
        cdef unsigned long long b = self.bytes_in
        if b>0 and self.bytes_out>0:
            info.update({
                "bytes_in"  : self.bytes_in,
                "bytes_out" : self.bytes_out,
                "ratio_pct" : int(100.0 * self.bytes_out // b)})
        if self.preset_name:
            info["preset"] = self.preset_name
        cdef double t = self.time
        info["total_time_ms"] = int(self.time*1000.0)
        if self.frames>0 and t>0:
            pps = float(self.width) * float(self.height) * float(self.frames) / t
            info["pixels_per_second"] = int(pps)
        info["free_memory"] = int(self.free_memory)
        info["total_memory"] = int(self.total_memory)
        cdef uint64_t m = self.total_memory
        if m>0:
            info["free_memory_pct"] = int(100.0*self.free_memory/m)
        #calculate fps:
        cdef int f = 0
        cdef double now = time.time()
        cdef double last_time = now
        cdef double cut_off = now-10.0
        cdef double ms_per_frame = 0
        for start,end in list(self.last_frame_times):
            if end>cut_off:
                f += 1
                last_time = min(last_time, end)
                ms_per_frame += (end-start)
        if f>0 and last_time<now:
            info["fps"] = int(0.5+f/(now-last_time))
            info["ms_per_frame"] = int(1000.0*ms_per_frame/f)
        return info

    def __repr__(self):
        return "nvenc(%s/%s - %s - %sx%s)" % (self.src_format, self.pixel_format, self.preset_name, self.width, self.height)

    def is_closed(self):
        return self.context==NULL

    def __dealloc__(self):
        self.clean()

    def clean(self):                        #@DuplicatedSignature
        log("clean() cuda_context=%s, encoder context=%#x", self.cuda_context, <unsigned long> self.context)
        if self.cuda_context:
            self.cuda_context.push()
            try:
                self.cuda_clean()
            finally:
                self.cuda_context.pop()
                self.cuda_context.detach()
                self.cuda_context = None
        self.width = 0
        self.height = 0
        self.input_width = 0
        self.input_height = 0
        self.encoder_width = 0
        self.encoder_height = 0
        self.src_format = ""
        self.dst_formats = []
        self.scaling = None
        self.speed = 0
        self.quality = 0
        #PyCUDA:
        self.driver = 0
        self.cuda_device_id = -1
        self.cuda_info = None
        self.pycuda_info = None
        self.cuda_device_info = None
        self.cuda_device = None
        self.kernel = None
        self.kernel_name = None
        self.max_block_sizes = 0
        self.max_grid_sizes = 0
        self.max_threads_per_block = 0
        self.free_memory = 0
        self.total_memory = 0
        #NVENC (mostly already cleaned up in cuda_clean):
        self.inputPitch = 0
        self.outputPitch = 0
        self.bitstreamBuffer = NULL
        self.bufferFmt = NV_ENC_BUFFER_FORMAT_UNDEFINED
        self.codec_name = ""
        self.preset_name = ""
        self.pixel_format = ""
        #statistics, etc:
        self.time = 0
        self.frames = 0
        self.first_frame_timestamp = 0
        self.last_frame_times = []
        self.bytes_in = 0
        self.bytes_out = 0


    cdef cuda_clean(self):
        cdef NVENCSTATUS r                  #@DuplicatedSignature
        if self.context!=NULL and self.frames>0:
            self.flushEncoder()
        if self.inputHandle!=NULL and self.context!=NULL:
            log("cuda_clean() unregistering CUDA output buffer input handle %#x", <unsigned long> self.inputHandle)
            if DEBUG_API:
                log("nvEncUnregisterResource(%#x)", <unsigned long> self.inputHandle)
            with nogil:
                r = self.functionList.nvEncUnregisterResource(self.context, self.inputHandle)
            raiseNVENC(r, "unregistering CUDA input buffer")
            self.inputHandle = NULL
        if self.inputBuffer is not None:
            log("cuda_clean() freeing CUDA host buffer %s", self.inputBuffer)
            self.inputBuffer = None
        if self.cudaInputBuffer is not None:
            log("cuda_clean() freeing CUDA input buffer %#x", int(self.cudaInputBuffer))
            self.cudaInputBuffer.free()
            self.cudaInputBuffer = None
        if self.cudaOutputBuffer is not None:
            log("cuda_clean() freeing CUDA output buffer %#x", int(self.cudaOutputBuffer))
            self.cudaOutputBuffer.free()
            self.cudaOutputBuffer = None
        if self.context!=NULL:
            if self.bitstreamBuffer!=NULL:
                log("cuda_clean() destroying output bitstream buffer %#x", <unsigned long> self.bitstreamBuffer)
                if DEBUG_API:
                    log("nvEncDestroyBitstreamBuffer(%#x)", <unsigned long> self.bitstreamBuffer)
                with nogil:
                    r = self.functionList.nvEncDestroyBitstreamBuffer(self.context, self.bitstreamBuffer)
                raiseNVENC(r, "destroying output buffer")
                self.bitstreamBuffer = NULL
            log("cuda_clean() destroying encoder %#x", <unsigned long> self.context)
            if DEBUG_API:
                log("nvEncDestroyEncoder(%#x)", <unsigned long> self.context)
            with nogil:
                r = self.functionList.nvEncDestroyEncoder(self.context)
            raiseNVENC(r, "destroying context")
            self.context = NULL
            global context_counter
            context_counter.decrease()
            log("cuda_clean() (still %s context%s in use)", context_counter, engs(context_counter))
        self.cuda_context_ptr = <void *> 0

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def get_type(self):                     #@DuplicatedSignature
        return  "nvenc"

    def get_encoding(self):                     #@DuplicatedSignature
        return  "h264"

    def get_src_format(self):
        return self.src_format

    def set_encoding_speed(self, speed):
        if self.speed!=speed:
            self.speed = speed
            self.update_bitrate()

    def set_encoding_quality(self, quality):
        cdef NV_ENC_RECONFIGURE_PARAMS reconfigure_params
        cdef NV_ENC_INITIALIZE_PARAMS *params = NULL
        if self.quality==quality:
            return
        log("set_encoding_quality(%s) current quality=%s", quality, self.quality)
        if quality<LOSSLESS_THRESHOLD:
            #edge resistance:
            raw_delta = quality-self.quality
            max_delta = max(-1, min(1, raw_delta))*10
            if abs(raw_delta)<abs(max_delta):
                delta = raw_delta
            else:
                delta = max_delta
            target_quality = quality-delta
        else:
            target_quality = 100
        self.quality = quality
        log("set_encoding_quality(%s) target quality=%s", quality, target_quality)
        new_pixel_format = self.get_target_pixel_format(target_quality)
        new_lossless = self.get_target_lossless(new_pixel_format, target_quality)
        if new_pixel_format==self.pixel_format and new_lossless==self.lossless:
            log("set_encoding_quality(%s) keeping current: %s / %s", quality, self.pixel_format, self.lossless)
            return
        start = time.time()
        memset(&reconfigure_params, 0, sizeof(NV_ENC_RECONFIGURE_PARAMS))
        reconfigure_params.version = NV_ENC_RECONFIGURE_PARAMS_VER
        #FIXME: actually call nvEncReconfigureEncoder
        #cdef int r
        try:
            params = self.init_params(self.codec, &reconfigure_params.reInitEncodeParams)
            reconfigure_params.resetEncoder = 1
            reconfigure_params.forceIDR = 1
            #with nogil:
            #    r = self.functionList.nvEncReconfigureEncoder(self.context, &reconfigure_params.reInitEncodeParams)
            #raiseNVENC(r, "flushing encoder buffer")
        finally:
            if params.encodeConfig!=NULL:
                free(params.encodeConfig)
        end = time.time()
        log("set_encoding_quality(%s) reconfigured from %s / %s to: %s / %s (took %ims)", quality, self.pixel_format, bool(self.lossless), new_pixel_format, new_lossless, int(1000*(end-start)))
        self.pixel_format = new_pixel_format
        self.lossless = new_lossless


    def update_bitrate(self):
        #use an exponential scale so for a 1Kx1K image (after scaling), roughly:
        #speed=0   -> 1Mbit/s
        #speed=50  -> 10Mbit/s
        #speed=90  -> 66Mbit/s
        #speed=100 -> 100Mbit/s
        MPixels = (self.encoder_width * self.encoder_height) / (1000.0 * 1000.0)
        if self.pixel_format=="NV12":
            #subsampling halves the input size:
            mult = 0.5
        else:
            #yuv444p preserves it:
            mult = 1.0
        lim = 100*1000000
        self.target_bitrate = min(lim, max(1000000, int(((0.5+self.speed/200.0)**8)*lim*MPixels*mult)))
        self.max_bitrate = 2*self.target_bitrate


    cdef flushEncoder(self):
        cdef NV_ENC_PIC_PARAMS picParams
        cdef NVENCSTATUS r                          #@DuplicatedSignature
        memset(&picParams, 0, sizeof(NV_ENC_PIC_PARAMS))
        picParams.version = NV_ENC_PIC_PARAMS_VER
        picParams.encodePicFlags = NV_ENC_PIC_FLAG_EOS
        if DEBUG_API:
            log("nvEncEncodePicture(%#x)", <unsigned long> &picParams)
        with nogil:
            r = self.functionList.nvEncEncodePicture(self.context, &picParams)
        raiseNVENC(r, "flushing encoder buffer")

    def compress_image(self, image, quality=-1, speed=-1, options={}, retry=0):
        self.cuda_context.push()
        try:
            try:
                self.set_encoding_quality(quality)
                self.set_encoding_speed(speed)
                return self.do_compress_image(image, options)
            finally:
                self.cuda_context.pop()
        except driver.LogicError as e:
            if retry>0:
                raise
            log.warn("PyCUDA error: %s", e)
            self.clean()
            self.init_cuda()
            return self.convert_image(image, options, retry+1)

    cdef do_compress_image(self, image, options={}):
        cdef NV_ENC_PIC_PARAMS picParams            #@DuplicatedSignature
        cdef NV_ENC_MAP_INPUT_RESOURCE mapInputResource
        cdef NV_ENC_LOCK_BITSTREAM lockOutputBuffer
        cdef size_t size
        cdef unsigned int x, y, stride
        cdef unsigned int i
        cdef NVENCSTATUS r                          #@DuplicatedSignature

        start = time.time()
        log("compress_image(%s, %s)", image, options)
        cdef unsigned int w = image.get_width()
        cdef unsigned int h = image.get_height()
        pixels = image.get_pixels()
        cdef unsigned int image_stride = image.get_rowstride()
        cdef unsigned long input_size = self.inputPitch * self.input_height
        assert pixels is not None, "failed to get pixels from %s" % image
        assert self.context!=NULL, "context is not initialized"
        assert image.get_planes()==ImageWrapper.PACKED, "invalid number of planes: %s" % image.get_planes()
        assert (w & WIDTH_MASK)<=self.input_width, "invalid width: %s" % w
        assert (h & HEIGHT_MASK)<=self.input_height, "invalid height: %s" % h
        assert self.inputBuffer is not None, "BUG: encoder is closed?"

        if self.frames==0:
            #first frame, record pts:
            self.first_frame_timestamp = image.get_timestamp()

        #copy to input buffer:
        cdef object buf
        if _memoryview and isinstance(pixels, _memoryview):
            #copy memoryview to inputBuffer directly:
            buf = self.inputBuffer
        else:
            #this is a numpy.ndarray type:
            buf = self.inputBuffer.data
        cdef unsigned long pix_len = len(pixels)
        if image_stride<=self.inputPitch:
            stride = image_stride
            assert pix_len<=input_size, "too many pixels (expected %s max, got %s) image: %sx%s stride=%s, input buffer: stride=%s, height=%s" % (input_size, pix_len, w, h, stride, self.inputPitch, self.input_height)
            log("copying %s pixels from %s into %s, in one shot", pix_len, type(pixels), type(self.inputBuffer))
            buf[:pix_len] = pixels
        else:
            #ouch, we need to copy the source pixels into the smaller buffer
            #before uploading to the device... this is probably costly!
            stride = self.inputPitch
            log("copying %s pixels from %s into %s, %i stride at a time", stride*h, type(pixels), type(self.inputBuffer), stride)
            for i in range(h):
                x = i*stride
                y = i*image_stride
                buf[x:x+stride] = pixels[y:y+stride]

        #copy input buffer to CUDA buffer:
        memcpy_htod(self.cudaInputBuffer, self.inputBuffer)
        self.bytes_in += input_size
        log("compress_image(..) %i bytes of input buffer copied to device", input_size)

        cdef uint8_t dx, dy
        if self.pixel_format=="NV12":
            #(these values are derived from the kernel code - which we should know nothing about here..)
            #divide each dimension by 2 since we process 4 pixels at a time:
            dx, dy = 2, 2
        elif self.pixel_format=="YUV444P":
            #one pixel at a time:
            dx, dy = 1, 1
        else:
            raise Exception("bug: invalid pixel format '%s'" % self.pixel_format)

        #FIXME: find better values and validate against max_block/max_grid:
        #calculate grids/blocks:
        #a block is a group of threads: (blockw * blockh) threads
        #a grid is a group of blocks: (gridw * gridh) blocks
        cdef uint32_t blockw = 16
        cdef uint32_t blockh = 16
        cdef uint32_t gridw = MAX(1, w//(blockw*dx))
        cdef uint32_t gridh = MAX(1, h//(blockh*dy))
        #if dx or dy made us round down, add one:
        if gridw*dx*blockw<w:
            gridw += 1
        if gridh*dy*blockh<h:
            gridh += 1
        cdef unsigned int in_w = self.input_width
        cdef unsigned int in_h = self.input_height
        if self.scaling!=(1,1):
            #scaling so scale exact dimensions, not padded input dimensions:
            in_w, in_h = w, h

        csc_start = time.time()
        args = (self.cudaInputBuffer, numpy.int32(in_w), numpy.int32(in_h), numpy.int32(stride),
               self.cudaOutputBuffer, numpy.int32(self.encoder_width), numpy.int32(self.encoder_height), numpy.int32(self.outputPitch),
               numpy.int32(w), numpy.int32(h))
        log("calling %s%s with block=%s, grid=%s", self.kernel, args, (blockw,blockh,1), (gridw, gridh))
        self.kernel(*args, block=(blockw,blockh,1), grid=(gridw, gridh))
        csc_end = time.time()
        log("compress_image(..) kernel %s took %.1f ms", self.kernel_name, (csc_end - csc_start)*1000.0)

        #map buffer so nvenc can access it:
        memset(&mapInputResource, 0, sizeof(NV_ENC_MAP_INPUT_RESOURCE))
        mapInputResource.version = NV_ENC_MAP_INPUT_RESOURCE_VER
        mapInputResource.registeredResource  = self.inputHandle
        mapInputResource.mappedBufferFmt = self.bufferFmt
        if DEBUG_API:
            log("nvEncMapInputResource(%#x)", <unsigned long> &mapInputResource)
        with nogil:
            r = self.functionList.nvEncMapInputResource(self.context, &mapInputResource)
        raiseNVENC(r, "mapping input resource")
        if DEBUG_API:
            log("compress_image(..) device buffer mapped to %#x", <unsigned long> mapInputResource.mappedResource)

        try:
            memset(&picParams, 0, sizeof(NV_ENC_PIC_PARAMS))
            picParams.version = NV_ENC_PIC_PARAMS_VER
            picParams.bufferFmt = self.bufferFmt
            picParams.pictureStruct = NV_ENC_PIC_STRUCT_FRAME
            picParams.inputWidth = self.encoder_width
            picParams.inputHeight = self.encoder_height
            picParams.inputPitch = self.outputPitch
            picParams.inputBuffer = mapInputResource.mappedResource
            picParams.outputBitstream = self.bitstreamBuffer
            #picParams.pictureType: required when enablePTD is disabled
            if self.frames==0:
                #only the first frame needs to be IDR (as we never lose frames)
                picParams.pictureType = NV_ENC_PIC_TYPE_IDR
                picParams.encodePicFlags = NV_ENC_PIC_FLAG_OUTPUT_SPSPPS
            else:
                picParams.pictureType = NV_ENC_PIC_TYPE_P
            picParams.codecPicParams.h264PicParams.displayPOCSyntax = 2*self.frames
            picParams.codecPicParams.h264PicParams.refPicFlag = self.frames==0
            picParams.codecPicParams.h264PicParams.sliceMode = 3            #sliceModeData specifies the number of slices
            picParams.codecPicParams.h264PicParams.sliceModeData = 1        #1 slice!
            picParams.codecPicParams.h264PicParams.colourPlaneId = 0
            picParams.frameIdx = self.frames
            picParams.inputTimeStamp = image.get_timestamp()-self.first_frame_timestamp
            #inputDuration = 0      #FIXME: use frame delay?
            #picParams.rcParams.rateControlMode = NV_ENC_PARAMS_RC_VBR     #FIXME: check NV_ENC_CAPS_SUPPORTED_RATECONTROL_MODES caps
            #picParams.rcParams.enableMinQP = 1
            #picParams.rcParams.enableMaxQP = 1
            #0=max quality, 63 lowest quality
            #qmin = QP_MAX_VALUE-min(QP_MAX_VALUE, int(QP_MAX_VALUE*(self.quality+20)/100))
            #qmax = QP_MAX_VALUE-max(0, int(QP_MAX_VALUE*(self.quality-20)/100))
            #picParams.rcParams.minQP.qpInterB = qmin
            #picParams.rcParams.minQP.qpInterP = qmin
            #picParams.rcParams.minQP.qpIntra = qmin
            #picParams.rcParams.maxQP.qpInterB = qmax
            #picParams.rcParams.maxQP.qpInterP = qmax
            #picParams.rcParams.maxQP.qpIntra = qmax
            #picParams.rcParams.averageBitRate = self.target_bitrate
            #picParams.rcParams.maxBitRate = self.max_bitrate

            if DEBUG_API:
                log("nvEncEncodePicture(%#x)", <unsigned long> &picParams)
            with nogil:
                r = self.functionList.nvEncEncodePicture(self.context, &picParams)
            raiseNVENC(r, "error during picture encoding")
            encode_end = time.time()
            log("compress_image(..) encoded in %.1f ms", (encode_end-csc_end)*1000.0)

            #lock output buffer:
            memset(&lockOutputBuffer, 0, sizeof(NV_ENC_LOCK_BITSTREAM))
            lockOutputBuffer.version = NV_ENC_LOCK_BITSTREAM_VER
            lockOutputBuffer.doNotWait = 0
            lockOutputBuffer.outputBitstream = self.bitstreamBuffer
            if DEBUG_API:
                log("nvEncLockBitstream(%#x) bitstreamBufferPtr=%#x", <unsigned long> &lockOutputBuffer, <unsigned long> lockOutputBuffer.bitstreamBufferPtr)
            with nogil:
                r = self.functionList.nvEncLockBitstream(self.context, &lockOutputBuffer)
            raiseNVENC(r, "locking output buffer")
            log("compress_image(..) output buffer locked, bitstreamBufferPtr=%#x", <unsigned long> lockOutputBuffer.bitstreamBufferPtr)

            #copy to python buffer:
            size = lockOutputBuffer.bitstreamSizeInBytes
            self.bytes_out += size
            data = (<char *> lockOutputBuffer.bitstreamBufferPtr)[:size]
        finally:
            if DEBUG_API:
                log("nvEncUnlockBitstream(%#x)", <unsigned long> self.bitstreamBuffer)
            with nogil:
                r = self.functionList.nvEncUnlockBitstream(self.context, self.bitstreamBuffer)
            raiseNVENC(r, "unlocking output buffer")
            if DEBUG_API:
                log("nvEncUnmapInputResource(%#x)", <unsigned long> self.bitstreamBuffer)
            with nogil:
                r = self.functionList.nvEncUnmapInputResource(self.context, mapInputResource.mappedResource)
            raiseNVENC(r, "unmapping input resource")

        download_end = time.time()
        log("compress_image(..) download took %.1f ms", (download_end-encode_end)*1000.0)
        #update info:
        self.free_memory, self.total_memory = driver.mem_get_info()

        client_options = {
                    "frame"     : int(self.frames),
                    "pts"       : int(image.get_timestamp()-self.first_frame_timestamp),
                    "speed"     : self.speed,
                    }
        if self.lossless:
            client_options["quality"] = 100
            client_options["lossless"] = True
        else:
            client_options["quality"] = min(99, self.quality)   #ensure we cap it at 99 because this is lossy
        if self.scaling!=(1,1):
            client_options["scaled_size"] = self.encoder_width, self.encoder_height
        end = time.time()
        self.frames += 1
        self.last_frame_times.append((start, end))
        self.time += end-start
        log("compress_image(..) %s %s returning %s bytes (%.1f%%), complete compression for frame %s took %.1fms",
            get_type(), get_version(),
            size, 100.0*size/input_size, self.frames, 1000.0*(end-start))
        return data, client_options


    cdef NV_ENC_PRESET_CONFIG *get_preset_config(self, name, GUID encode_GUID, GUID preset_GUID) except *:
        """ you must free it after use! """
        cdef NV_ENC_PRESET_CONFIG *presetConfig     #@DuplicatedSignature
        cdef NVENCSTATUS r                          #@DuplicatedSignature
        presetConfig = <NV_ENC_PRESET_CONFIG*> malloc(sizeof(NV_ENC_PRESET_CONFIG))
        assert presetConfig!=NULL, "failed to allocate memory for preset config"
        memset(presetConfig, 0, sizeof(NV_ENC_PRESET_CONFIG))
        presetConfig.version = NV_ENC_PRESET_CONFIG_VER
        presetConfig.presetCfg.version = NV_ENC_CONFIG_VER
        if DEBUG_API:
            log.info("nvEncGetEncodePresetConfig(%s, %s)", codecstr(encode_GUID), presetstr(preset_GUID))
        r = self.functionList.nvEncGetEncodePresetConfig(self.context, encode_GUID, preset_GUID, presetConfig)
        if r!=0:
            log.warn("failed to get preset config for %s (%s / %s): %s", name, guidstr(encode_GUID), guidstr(preset_GUID), NV_ENC_STATUS_TXT.get(r, r))
            return NULL
        return presetConfig

    cdef object query_presets(self, GUID encode_GUID):
        cdef uint32_t presetCount
        cdef uint32_t presetsRetCount
        cdef GUID* preset_GUIDs
        cdef GUID preset_GUID
        cdef NV_ENC_PRESET_CONFIG *presetConfig
        cdef NV_ENC_CONFIG *encConfig
        cdef NVENCSTATUS r                          #@DuplicatedSignature

        presets = {}
        if DEBUG_API:
            log.info("nvEncGetEncodePresetCount(%s, %#x)", codecstr(encode_GUID), <unsigned long> &presetCount)
        with nogil:
            r = self.functionList.nvEncGetEncodePresetCount(self.context, encode_GUID, &presetCount)
        raiseNVENC(r, "getting preset count for %s" % guidstr(encode_GUID))
        log("found %s preset%s:", presetCount, engs(presetCount))
        assert presetCount<2**8
        preset_GUIDs = <GUID*> malloc(sizeof(GUID) * presetCount)
        assert preset_GUIDs!=NULL, "could not allocate memory for %s preset GUIDs!" % (presetCount)
        try:
            if DEBUG_API:
                log("nvEncGetEncodePresetGUIDs(%s, %#x)", codecstr(encode_GUID), <unsigned long> &presetCount)
            with nogil:
                r = self.functionList.nvEncGetEncodePresetGUIDs(self.context, encode_GUID, preset_GUIDs, presetCount, &presetsRetCount)
            raiseNVENC(r, "getting encode presets")
            assert presetsRetCount==presetCount
            unknowns = []
            for x in range(presetCount):
                preset_GUID = preset_GUIDs[x]
                preset_name = CODEC_PRESETS_GUIDS.get(guidstr(preset_GUID))
                if DEBUG_API:
                    log("* %s : %s", guidstr(preset_GUID), preset_name or "unknown!")
                presetConfig = self.get_preset_config(preset_name, encode_GUID, preset_GUID)
                if presetConfig!=NULL:
                    try:
                        encConfig = &presetConfig.presetCfg
                        if DEBUG_API:
                            log("presetConfig.presetCfg=%s", <unsigned long> encConfig)
                        gop = {NVENC_INFINITE_GOPLENGTH : "infinite"}.get(encConfig.gopLength, encConfig.gopLength)
                        log("* %-20s P frame interval=%i, gop length=%-10s", preset_name or "unknown!", encConfig.frameIntervalP, gop)
                    finally:
                        free(presetConfig)
                if preset_name is None:
                    unknowns.append(guidstr(preset_GUID))
                else:
                    presets[preset_name] = guidstr(preset_GUID)
            if len(unknowns)>0:
                log.warn("nvenc: found some unknown presets: %s", ", ".join(unknowns))
        finally:
            free(preset_GUIDs)
        if DEBUG_API:
            log("query_presets(%s)=%s", codecstr(encode_GUID), presets)
        return presets

    cdef object query_profiles(self, GUID encode_GUID):
        cdef uint32_t profileCount
        cdef uint32_t profilesRetCount
        cdef GUID* profile_GUIDs
        cdef GUID profile_GUID
        cdef NVENCSTATUS r                          #@DuplicatedSignature

        profiles = {}
        if DEBUG_API:
            log("nvEncGetEncodeProfileGUIDCount(%s, %#x)", codecstr(encode_GUID), <unsigned long> &profileCount)
        with nogil:
            r = self.functionList.nvEncGetEncodeProfileGUIDCount(self.context, encode_GUID, &profileCount)
        raiseNVENC(r, "getting profile count")
        log("%s profiles:", profileCount)
        assert profileCount<2**8
        profile_GUIDs = <GUID*> malloc(sizeof(GUID) * profileCount)
        assert profile_GUIDs!=NULL, "could not allocate memory for %s profile GUIDs!" % (profileCount)
        PROFILES_GUIDS = CODEC_PROFILES_GUIDS.get(guidstr(encode_GUID), {})
        try:
            if DEBUG_API:
                log("nvEncGetEncodeProfileGUIDs(%s, %#x, %#x)", codecstr(encode_GUID), <unsigned long> profile_GUIDs, <unsigned long> &profileCount)
            with nogil:
                r = self.functionList.nvEncGetEncodeProfileGUIDs(self.context, encode_GUID, profile_GUIDs, profileCount, &profilesRetCount)
            raiseNVENC(r, "getting encode profiles")
            #(void* encoder, GUID encodeGUID, GUID* profileGUIDs, uint32_t guidArraySize, uint32_t* GUIDCount)
            assert profilesRetCount==profileCount
            for x in range(profileCount):
                profile_GUID = profile_GUIDs[x]
                profile_name = PROFILES_GUIDS.get(guidstr(profile_GUID))
                log("* %s : %s", guidstr(profile_GUID), profile_name)
                profiles[profile_name] = guidstr(profile_GUID)
        finally:
            free(profile_GUIDs)
        return profiles

    cdef object query_input_formats(self, GUID encode_GUID):
        cdef uint32_t inputFmtCount
        cdef NV_ENC_BUFFER_FORMAT* inputFmts
        cdef uint32_t inputFmtsRetCount
        cdef NV_ENC_BUFFER_FORMAT inputFmt
        cdef NVENCSTATUS r                          #@DuplicatedSignature

        input_formats = {}
        if DEBUG_API:
            log("nvEncGetInputFormatCount(%s, %#x)", codecstr(encode_GUID), <unsigned long> &inputFmtCount)
        with nogil:
            r = self.functionList.nvEncGetInputFormatCount(self.context, encode_GUID, &inputFmtCount)
        raiseNVENC(r, "getting input format count")
        log("%s input format type%s:", inputFmtCount, engs(inputFmtCount))
        assert inputFmtCount>0 and inputFmtCount<2**8
        inputFmts = <NV_ENC_BUFFER_FORMAT*> malloc(sizeof(int) * inputFmtCount)
        assert inputFmts!=NULL, "could not allocate memory for %s input formats!" % (inputFmtCount)
        try:
            if DEBUG_API:
                log("nvEncGetInputFormats(%s, %#x, %i, %#x)", codecstr(encode_GUID), <unsigned long> inputFmts, inputFmtCount, <unsigned long> &inputFmtsRetCount)
            with nogil:
                r = self.functionList.nvEncGetInputFormats(self.context, encode_GUID, inputFmts, inputFmtCount, &inputFmtsRetCount)
            raiseNVENC(r, "getting input formats")
            assert inputFmtsRetCount==inputFmtCount
            for x in range(inputFmtCount):
                inputFmt = inputFmts[x]
                log("* %#x", inputFmt)
                for format_mask in sorted(BUFFER_FORMAT.keys()):
                    if format_mask>0 and (format_mask & inputFmt)>0:
                        format_name = BUFFER_FORMAT.get(format_mask)
                        log(" + %#x : %s", format_mask, format_name)
                        input_formats[format_name] = hex(format_mask)
        finally:
            free(inputFmts)
        return input_formats

    cdef int query_encoder_caps(self, GUID encode_GUID, NV_ENC_CAPS caps_type) except *:
        cdef int val
        cdef NV_ENC_CAPS_PARAM encCaps
        cdef NVENCSTATUS r                          #@DuplicatedSignature
        memset(&encCaps, 0, sizeof(NV_ENC_CAPS_PARAM))
        encCaps.version = NV_ENC_CAPS_PARAM_VER
        encCaps.capsToQuery = caps_type

        with nogil:
            r = self.functionList.nvEncGetEncodeCaps(self.context, encode_GUID, &encCaps, &val)
        raiseNVENC(r, "getting encode caps for %s" % CAPS_NAMES.get(caps_type, caps_type))
        if DEBUG_API:
            log("query_encoder_caps(%s, %s) %s=%s", codecstr(encode_GUID), caps_type, CAPS_NAMES.get(caps_type, caps_type), val)
        return val

    cdef query_codecs(self, full_query=False):
        cdef uint32_t GUIDCount
        cdef uint32_t GUIDRetCount
        cdef GUID* encode_GUIDs
        cdef GUID encode_GUID
        cdef NVENCSTATUS r                          #@DuplicatedSignature

        if DEBUG_API:
            log("nvEncGetEncodeGUIDCount(%#x, %#x)", <unsigned long> self.context, <unsigned long> &GUIDCount)
        with nogil:
            r = self.functionList.nvEncGetEncodeGUIDCount(self.context, &GUIDCount)
        raiseNVENC(r, "getting encoder count")
        log("found %i encoder%s:", GUIDCount, engs(GUIDCount))
        assert GUIDCount<2**8
        encode_GUIDs = <GUID*> malloc(sizeof(GUID) * GUIDCount)
        assert encode_GUIDs!=NULL, "could not allocate memory for %s encode GUIDs!" % (GUIDCount)
        codecs = {}
        try:
            if DEBUG_API:
                log("nvEncGetEncodeGUIDs(%#x, %i, %#x)", <unsigned long> encode_GUIDs, GUIDCount, <unsigned long> &GUIDRetCount)
            with nogil:
                r = self.functionList.nvEncGetEncodeGUIDs(self.context, encode_GUIDs, GUIDCount, &GUIDRetCount)
            raiseNVENC(r, "getting list of encode GUIDs")
            assert GUIDRetCount==GUIDCount, "expected %s items but got %s" % (GUIDCount, GUIDRetCount)
            for x in range(GUIDRetCount):
                encode_GUID = encode_GUIDs[x]
                codec_name = CODEC_GUIDS.get(guidstr(encode_GUID))
                if not codec_name:
                    log("[%s] unknown codec GUID: %s", x, guidstr(encode_GUID))
                else:
                    log("[%s] %s", x, codec_name)
                codecs[codec_name] = guidstr(encode_GUID)

                maxw = self.query_encoder_caps(encode_GUID, NV_ENC_CAPS_WIDTH_MAX)
                maxh = self.query_encoder_caps(encode_GUID, NV_ENC_CAPS_HEIGHT_MAX)
                async = self.query_encoder_caps(encode_GUID, NV_ENC_CAPS_ASYNC_ENCODE_SUPPORT)
                log(" max dimensions: %sx%s (async=%s)", maxw, maxh, async)
                rate_countrol = self.query_encoder_caps(encode_GUID, NV_ENC_CAPS_SUPPORTED_RATECONTROL_MODES)
                log(" rate control:   %s", rate_countrol)

                if full_query:
                    presets = self.query_presets(encode_GUID)
                    log("  presets=%s", presets)

                    profiles = self.query_profiles(encode_GUID)
                    log("  profiles=%s", profiles)

                    input_formats = self.query_input_formats(encode_GUID)
                    log("  input formats=%s", input_formats)
        finally:
            free(encode_GUIDs)
        log("codecs=%s", csv(codecs.keys()))
        return codecs


    cdef open_encode_session(self):
        global context_counter, context_gen_counter, last_context_failure
        cdef NVENCSTATUS r                          #@DuplicatedSignature
        cdef int t
        cdef NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS params
        #params = <NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS*> malloc(sizeof(NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS))
        log("open_encode_session() cuda_context=%s, cuda_context_ptr=%#x", self.cuda_context, <unsigned long> self.cuda_context_ptr)

        #get NVENC function pointers:
        memset(self.functionList, 0, sizeof(NV_ENCODE_API_FUNCTION_LIST))
        self.functionList.version = NV_ENCODE_API_FUNCTION_LIST_VER
        if DEBUG_API:
            log("NvEncodeAPICreateInstance(%#x)", <unsigned long> self.functionList)
        r = NvEncodeAPICreateInstance(<unsigned long> self.functionList)
        raiseNVENC(r, "getting API function list")
        assert self.functionList.nvEncOpenEncodeSessionEx!=NULL, "looks like NvEncodeAPICreateInstance failed!"

        #NVENC init:
        memset(&params, 0, sizeof(NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS))
        params.version = NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER
        params.deviceType = NV_ENC_DEVICE_TYPE_CUDA
        params.device = self.cuda_context_ptr
        #params.clientKeyPtr = &CLIENT_KEY_GUID
        params.apiVersion = NVENCAPI_VERSION
        cstr = <unsigned char*> &params
        pstr = cstr[:sizeof(NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS)]
        if DEBUG_API:
            log("calling nvEncOpenEncodeSessionEx @ %#x", <unsigned long> self.functionList.nvEncOpenEncodeSessionEx)
        self.context = NULL
        with nogil:
            r = self.functionList.nvEncOpenEncodeSessionEx(&params, &self.context)
        if r==NV_ENC_ERR_UNSUPPORTED_DEVICE:
            last_context_failure = time.time()
            msg = "NV_ENC_ERR_UNSUPPORTED_DEVICE: could not open encode session (out of resources / no more codec contexts?)"
            log(msg)
            raise TransientCodecException(msg)
        log("encoding context=%s", <unsigned long> self.context)
        if self.context==NULL:
            raise TransientCodecException("cannot open encoding session, context is NULL")
        raiseNVENC(r, "opening session")
        context_counter.increase()
        context_gen_counter.increase()
        log("success, encoder context=%#x (%s context%s in use)", <unsigned long> self.context, context_counter, engs(context_counter))


def init_module():
    global YUV444_ENABLED, LOSSLESS_ENABLED
    YUV444_ENABLED, LOSSLESS_ENABLED = True, True
    log("nvenc.init_module()")
    #TODO: this should be a build time check:
    if NVENCAPI_VERSION!=0x6:
        raise Exception("unsupported version of NVENC: %#x" % NVENCAPI_VERSION)
    log("NVENC encoder API version %s", ".".join([str(x) for x in PRETTY_VERSION]))

    #this should log the kernel module version
    v = get_nvidia_module_version()
    if not v:
        log.warn("Warning: unknown NVidia driver version")
        bl = None
    else:
        bl = is_blacklisted()
    if bl is True:
        raise Exception("NVidia driver version %s is blacklisted, it does not work with NVENC" % pver(v))
    elif bl is None:
        log.warn("Warning: NVidia driver version %s may or may not work", pver(v))
        log.warn(" recommended driver versions: up to 350 only")
        if os.environ.get("XPRA_NVENC_YUV444P", "0")!="1":
            log.warn(" disabling YUV444P and lossless mode")
            log.warn(" use XPRA_NVENC_YUV444P=1 to force enable it")
            YUV444_ENABLED = False
            LOSSLESS_ENABLED = False

    #load the library / DLL:
    init_nvencode_library()

    test_encoder = Encoder()
    for encoding in get_encodings():
        colorspaces = get_input_colorspaces(encoding)
        assert colorspaces, "cannot use NVENC: no colorspaces available for %s" % encoding
        src_format = colorspaces[0]
        dst_formats = get_output_colorspaces(encoding, src_format)
        try:
            try:
                test_encoder.init_context(1920, 1080, src_format, dst_formats, encoding, 50, 50, (1,1), {})
                #check for YUV444 support
                if YUV444_ENABLED and not test_encoder.query_encoder_caps(test_encoder.get_codec(), <NV_ENC_CAPS> NV_ENC_CAPS_SUPPORT_YUV444_ENCODE):
                    log.warn("Warning: hardware or nvenc library version does not support YUV444")
                    YUV444_ENABLED = False
                    LOSSLESS_ENABLED = False
                log("%s YUV444 support: %s", encoding, YUV444_ENABLED)
                #check for lossless:
                if LOSSLESS_ENABLED and not test_encoder.query_encoder_caps(test_encoder.get_codec(), <NV_ENC_CAPS> NV_ENC_CAPS_SUPPORT_LOSSLESS_ENCODE):
                    log.warn("Warning: hardware or nvenc library version does not support lossless mode")
                    LOSSLESS_ENABLED = False
                log("%s lossless support: %s", encoding, LOSSLESS_ENABLED)
            except NVENCException as e:
                log("encoder %s failed: %s", test_encoder, e)
                #special handling for license key issues:
                if e.code==NV_ENC_ERR_INCOMPATIBLE_CLIENT_KEY:
                    raise Exception("unsupported card, the API claims a license is required")
                elif e.code==NV_ENC_ERR_INVALID_VERSION:
                    #we can bail out already:
                    raise Exception("version mismatch, you need a newer/older codec build or newer/older drivers")
                raise Exception("error initializing a test context: %s" % e)
        finally:
            test_encoder.clean()
    log.info("NVENC v6 successfully initialized")

def cleanup_module():
    log("nvenc.cleanup_module()")
    reset_state()

def selftest(full=False):
    get_nvidia_module_version(True)
    #this is expensive, so don't run it unless "full" is set:
    if full:
        from xpra.codecs.codec_checks import get_encoder_max_sizes
        from xpra.codecs.nvenc5 import encoder
        init_module()
        log.info("%s max dimensions: %s", encoder, get_encoder_max_sizes(encoder))
