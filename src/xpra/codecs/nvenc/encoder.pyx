# This file is part of Xpra.
# Copyright (C) 2013, 2014 Antoine Martin <antoine@devloop.org.uk>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import binascii
import time
import os
import numpy

import pycuda
from pycuda import driver
from pycuda.compiler import compile

from xpra.util import AtomicInteger
from xpra.deque import maxdeque
from xpra.codecs.codec_constants import codec_spec, TransientCodecException
from xpra.codecs.image_wrapper import ImageWrapper
from xpra.log import Logger
log = Logger("encoder", "nvenc")

from libc.stdint cimport uint8_t, uint16_t, uint32_t, int32_t, uint64_t

FORCE = os.environ.get("XPRA_NVENC_FORCE", "0")=="1"
CLIENT_KEY = os.environ.get("XPRA_NVENC_CLIENT_KEY", "")
DESIRED_PRESET = os.environ.get("XPRA_NVENC_PRESET", "")
DEFAULT_CUDA_DEVICE_ID = int(os.environ.get("XPRA_CUDA_DEVICE", "0"))

#API is undocumented and broken:
USE_YUV444P = False


cdef extern from "Python.h":
    ctypedef int Py_ssize_t
    int PyObject_AsWriteBuffer(object obj,
                               void ** buffer,
                               Py_ssize_t * buffer_len) except -1
    int PyObject_AsReadBuffer(object obj,
                              void ** buffer,
                              Py_ssize_t * buffer_len) except -1

cdef extern from "string.h":
    void * memcpy ( void * destination, void * source, size_t num )
    void * memset ( void * ptr, int value, size_t num )

cdef extern from "stdlib.h":
    void* malloc(size_t __size)
    void free(void* mem)

cdef extern from "cuda.h":
    ctypedef int CUresult
    ctypedef void* CUcontext
    CUresult cuCtxGetCurrent(CUcontext *pctx)

cdef extern from "NvTypes.h":
    pass


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
        NV_ENC_CAPS_SUPPORT_STEREO_MVC
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
        NV_ENC_BUFFER_FORMAT_NV12_TILED16x16
        NV_ENC_BUFFER_FORMAT_NV12_TILED64x16
        NV_ENC_BUFFER_FORMAT_YV12_PL
        NV_ENC_BUFFER_FORMAT_YV12_TILED16x16
        NV_ENC_BUFFER_FORMAT_YV12_TILED64x16
        NV_ENC_BUFFER_FORMAT_IYUV_PL
        NV_ENC_BUFFER_FORMAT_IYUV_TILED16x16
        NV_ENC_BUFFER_FORMAT_IYUV_TILED64x16
        NV_ENC_BUFFER_FORMAT_YUV444_PL
        NV_ENC_BUFFER_FORMAT_YUV444_TILED16x16
        NV_ENC_BUFFER_FORMAT_YUV444_TILED64x16

    ctypedef enum NV_ENC_PIC_FLAGS:
        NV_ENC_PIC_FLAG_FORCEINTRA
        NV_ENC_PIC_FLAG_FORCEIDR
        NV_ENC_PIC_FLAG_OUTPUT_SPSPPS
        NV_ENC_PIC_FLAG_EOS
        NV_ENC_PIC_FLAG_DYN_RES_CHANGE
        NV_ENC_PIC_FLAG_DYN_BITRATE_CHANGE
        NV_ENC_PIC_FLAG_USER_FORCE_CONSTQP
        NV_ENC_PIC_FLAG_DYN_RCMODE_CHANGE
        NV_ENC_PIC_FLAG_REINIT_ENCODER

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
        NV_ENC_LEVEL_MPEG2_LOW
        NV_ENC_LEVEL_MPEG2_MAIN
        NV_ENC_LEVEL_MPEG2_HIGH
        NV_ENC_LEVEL_MPEG2_HIGH1440
        NV_ENC_LEVEL_VC1_LOW
        NV_ENC_LEVEL_VC1_MEDIAN
        NV_ENC_LEVEL_VC1_HIGH
        NV_ENC_LEVEL_VC1_0
        NV_ENC_LEVEL_VC1_1
        NV_ENC_LEVEL_VC1_2
        NV_ENC_LEVEL_VC1_3
        NV_ENC_LEVEL_VC1_4

    ctypedef enum NV_ENC_PARAMS_RC_MODE:
        NV_ENC_PARAMS_RC_CONSTQP            #Constant QP mode
        NV_ENC_PARAMS_RC_VBR                #Variable bitrate mode
        NV_ENC_PARAMS_RC_CBR                #Constant bitrate mode
        NV_ENC_PARAMS_RC_VBR_MINQP          #Variable bitrate mode with MinQP
        NV_ENC_PARAMS_RC_2_PASS_QUALITY     #Multi pass encoding optimized for image quality and works only with low latency mode
        NV_ENC_PARAMS_RC_2_PASS_FRAMESIZE_CAP   #Multi pass encoding optimized for maintaining frame size and works only with low latency mode
        NV_ENC_PARAMS_RC_CBR2               #Constant bitrate mode using two pass for IDR frame only

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
        uint32_t    reserved1[249]      #[in]: Reserved and must be set to 0.
        void*       reserved2[62]       #[in]: Reserved and must be set to NULL.

    ctypedef struct GUID:
        uint32_t Data1
        uint16_t Data2
        uint16_t Data3
        uint8_t  Data4[8]

    int NVENC_INFINITE_GOPLENGTH

    #Encode Codec GUIDS supported by the NvEncodeAPI interface.
    GUID NV_ENC_CODEC_H264_GUID
    GUID NV_ENC_CODEC_MPEG2_GUID
    GUID NV_ENC_CODEC_VC1_GUID
    GUID NV_ENC_CODEC_JPEG_GUID
    GUID NV_ENC_CODEC_VP8_GUID

    #Profiles:
    GUID NV_ENC_CODEC_PROFILE_AUTOSELECT_GUID
    GUID NV_ENC_H264_PROFILE_BASELINE_GUID
    GUID NV_ENC_H264_PROFILE_MAIN_GUID
    GUID NV_ENC_H264_PROFILE_HIGH_GUID
    GUID NV_ENC_H264_PROFILE_STEREO_GUID
    GUID NV_ENC_H264_PROFILE_SVC_TEMPORAL_SCALABILTY
    GUID NV_ENC_H264_PROFILE_CONSTRAINED_HIGH_GUID
    GUID NV_ENC_MPEG2_PROFILE_SIMPLE_GUID
    GUID NV_ENC_MPEG2_PROFILE_MAIN_GUID
    GUID NV_ENC_MPEG2_PROFILE_HIGH_GUID
    GUID NV_ENC_VP8_GUID
    GUID NV_ENC_VC1_PROFILE_SIMPLE_GUID
    GUID NV_ENC_VC1_PROFILE_MAIN_GUID
    GUID NV_ENC_VC1_PROFILE_ADVANCED_GUID
    GUID NV_ENC_JPEG_PROFILE_BASELINE_GUID

    #Presets:
    GUID NV_ENC_PRESET_DEFAULT_GUID
    GUID NV_ENC_PRESET_HP_GUID
    GUID NV_ENC_PRESET_HQ_GUID
    GUID NV_ENC_PRESET_BD_GUID
    #V3 ONLY PRESETS:
    GUID NV_ENC_PRESET_LOW_LATENCY_DEFAULT_GUID
    GUID NV_ENC_PRESET_LOW_LATENCY_HQ_GUID
    GUID NV_ENC_PRESET_LOW_LATENCY_HP_GUID
    #NV_ENC_CODEC_MPEG2_GUID, etc..

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

    ctypedef struct NV_ENC_CONFIG_SVC_TEMPORAL:
        uint32_t    numTemporalLayers   #[in]: Max temporal layers. Valid value range is [1,::NV_ENC_CAPS_NUM_MAX_TEMPORAL_LAYERS]
        uint32_t    basePriorityID      #[in]: Priority id of the base layer. Default is 0. Priority Id is increased by 1 for each consecutive temporal layers.
        uint32_t    reserved1[254]      #[in]: Reserved and should be set to 0
        void*       reserved2[64]       #[in]: Reserved and should be set to NULL

    ctypedef struct NV_ENC_CONFIG_MVC:
        uint32_t    reserved1[256]      #[in]: Reserved and should be set to 0
        void*       reserved2[64]       #[in]: Reserved and should be set to NULL

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

    ctypedef union NV_ENC_CONFIG_H264_EXT:
        NV_ENC_CONFIG_SVC_TEMPORAL  svcTemporalConfig   #[in]: SVC encode config
        NV_ENC_CONFIG_MVC           mvcConfig           #[in]: MVC encode config
        uint32_t                    reserved1[254]      #[in]: Reserved and should be set to 0
        void*                       reserved2[64]       #[in]: Reserved and should be set to NULL

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
        uint32_t    enableLTR           #[in]: Set to 1 to enable LTR support and auto-mark the first
        uint32_t    reservedBitFields   #[in]: Reserved bitfields and must be set to 0
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
        NV_ENC_CONFIG_H264_EXT h264Extension    #[in]: Specifies the H264 extension config
        uint32_t    intraRefreshPeriod  #[in]: Specifies the interval between successive intra refresh if enableIntrarefresh is set and one time intraRefresh configuration is desired.
                                        #When this is specified only first IDR will be encoded and no more key frames will be encoded. Client should set PIC_TYPE = NV_ENC_PIC_TYPE_INTRA_REFRESH
                                        #for first picture of every intra refresh period.
        uint32_t    intraRefreshCnt     #[in]: Specifies the number of frames over which intra refresh will happen
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
        uint32_t    reserved1[272]      #[in]: Reserved and must be set to 0
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_CONFIG_MPEG2:
        uint32_t    profile             #[in]: Specifies the encoding profile
        uint32_t    level               #[in]: Specifies the encoding level
        uint32_t    alternateScanValue  #[in]: Specifies the AlternateScan value
        uint32_t    quantScaleType      #[in]: Specifies the QuantScale value
        uint32_t    intraDCPrecision    #[in]: Specifies the intra DC precision
        uint32_t    frameDCT            #[in]: Specifies the frame Discrete Cosine Transform
        uint32_t    reserved[250]       #[in]: Reserved and must be set to 0
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_CONFIG_JPEG:
        uint32_t    reserved[256]       #[in]: Reserved and must be set to 0
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_CONFIG_VC1:
        uint32_t    level               #[in]: Specifies the encoding level
        uint32_t    disableOverlapSmooth#[in]: Set this to 1 for disabling overlap smoothing
        uint32_t    disableFastUVMC     #[in]: Set this to 1 for disabling fastUVMC mode
        uint32_t    disableInloopFilter #[in]: Set this to 1 for disabling in-loop filtering
        uint32_t    disable4MV          #[in]: Set this to 1 for disabling 4MV mode
        uint32_t    reservedBitFields   #[in]: Reserved bitfields and must be set to 0
        uint32_t    numSlices           #[in]: Specifies number of slices to encode. This field is applicable only for Advanced Profile.
                                        #If set to 0, NvEncodeAPI interface will choose optimal number of slices. Currently we support only a maximum of three slices
        uint32_t    reserved[253]       #[in]: Reserved and must be set to 0
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_CONFIG_VP8:
        uint32_t    reserved[256]       #[in]: Reserved and must be set to 0
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_CODEC_CONFIG:
        NV_ENC_CONFIG_H264  h264Config  #[in]: Specifies the H.264-specific encoder configuration
        NV_ENC_CONFIG_VC1   vc1Config   #[in]: Specifies the VC1-specific encoder configuration. Currently unsupported and must not to be used.
        NV_ENC_CONFIG_JPEG  jpegConfig  #[in]: Specifies the JPEG-specific encoder configuration. Currently unsupported and must not to be used.
        NV_ENC_CONFIG_MPEG2 mpeg2Config #[in]: Specifies the MPEG2-specific encoder configuration. Currently unsupported and must not to be used.
        NV_ENC_CONFIG_VP8   vp8Config   #[in]: Specifies the VP8-specific encoder configuration. Currently unsupported and must not to be used.
        uint32_t            reserved[256]       #[in]: Reserved and must be set to 0

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
        uint32_t    reservedBitFields   #[in]: Reserved bitfields and must be set to 0
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
        uint32_t    reservedBitFields   #[in]: Reserved bitfields and must be set to 0
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

    ctypedef struct NV_ENC_PIC_PARAMS_MVC:
        uint32_t    viewID              #[in]: Specifies the view ID associated with the current input view.
        uint32_t    temporalID          #[in]: Specifies the temporal ID associated with the current input view.
        uint32_t    priorityID          #[in]: Specifies the priority ID associated with the current input view. Reserved and ignored by the NvEncodeAPI interface.
        uint32_t    reserved1[253]      #[in]: Reserved and must be set to 0.
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL.

    ctypedef struct NV_ENC_PIC_PARAMS_SVC:
        uint32_t    priorityID          #[in]: Specifies the priority id associated with the current input.
        uint32_t    temporalID          #[in]: Specifies the temporal id associated with the current input.
        uint32_t    dependencyID        #[in]: Specifies the dependency id  associated with the current input.
        uint32_t    qualityID           #[in]: Specifies the quality id associated with the current input.
        uint32_t    reserved1[252]      #[in]: Reserved and must be set to 0.
        void        *reserved2[64]      #[in]: Reserved and must be set to NULL.

    ctypedef struct NV_ENC_PIC_PARAMS_H264_EXT:
        NV_ENC_PIC_PARAMS_MVC mvcPicParams   #[in]: Specifies the MVC picture parameters.
        NV_ENC_PIC_PARAMS_SVC svcPicParams   #[in]: Specifies the SVC picture parameters.
        uint32_t    reserved1[256]      #[in]: Reserved and must be set to 0.

    ctypedef struct NV_ENC_H264_SEI_PAYLOAD:
        uint32_t    payloadSize         #[in] SEI payload size in bytes. SEI payload must be byte aligned, as described in Annex D
        uint32_t    payloadType         #[in] SEI payload types and syntax can be found in Annex D of the H.264 Specification.
        uint8_t     *payload            #[in] pointer to user data

    ctypedef struct NV_ENC_PIC_PARAMS_H264:
        uint32_t    displayPOCSyntax    #[in]: Specifies the display POC syntax This is required to be set if client is handling the picture type decision.
        uint32_t    reserved3           #[in]: Reserved and must be set to 0
        NV_ENC_PIC_PARAMS_H264_EXT h264ExtPicParams     #[in]: Specifies the H264 extension config parameters using this config.
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
        NV_ENC_H264_SEI_PAYLOAD *seiPayloadArray    #[in]: Array of SEI payloads which will be inserted for this frame.
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

    ctypedef struct NV_ENC_PIC_PARAMS_MPEG:
        uint32_t    displayPOC          #[in]: Specifies the input display POC for current picture.
        uint32_t    reserved[255]       #[in]: Reserved and must be set to 0.
        void*       reserved2[64]       #[in]: Reserved and must be set to NULL.

    ctypedef struct NV_ENC_PIC_PARAMS_VC1:
        uint32_t    gopUserDataSize     #[in]: Specifies the size of the private data to be inserted in GOP header.
        uint8_t*    gopUserData         #[in]: Specifies the private data to be inserted in GOP header. It is the client's responsibility to allocate and manage the struct memory.
        uint8_t*    picUserData         #[in]: Specifies the private data to be inserted in picture header. It is the client's responsibility to allocate and manage the struct memory.
        uint32_t    picUserDataSize     #[in]: Specifies the size of the private data to be inserted in picture header.
        uint32_t    reserved[252]       #[in]: Reserved and must be set to 0.
        void*       reserved2[64]       #[in]: Reserved and must be set to NULL.

    ctypedef struct NV_ENC_PIC_PARAMS_VP8:
        uint32_t    reserved[256]       #[in]: Reserved and must be set to 0
        void*       reserved2[64]       #[in]: Reserved and must be set to NULL

    ctypedef struct NV_ENC_PIC_PARAMS_JPEG:
        uint32_t    exifBlobSize        #[in]: Specifies the size of the EXIF data blob to be added
        uint8_t*    exifBlob            #[in]: Specifies the EXIF data blob to be added. It is the client's responsibility to allocate and manage the struct memory.
        uint32_t    reserved[255]       #[in]: Reserved and must be set to 0
        void*       reserved2[63]       #[in]: Reserved and must be set to NULL

    ctypedef union NV_ENC_CODEC_PIC_PARAMS:
        NV_ENC_PIC_PARAMS_H264 h264PicParams    #[in]: H264 encode picture params.
        NV_ENC_PIC_PARAMS_MPEG mpegPicParams    #[in]: MPEG2 encode picture params. Currently unsupported and must not to be used.
        NV_ENC_PIC_PARAMS_VC1  vc1PicParams     #[in]: VC1 encode picture params. Currently unsupported and must not to be used.
        NV_ENC_PIC_PARAMS_JPEG jpegPicParams    #[in]: JPEG encode picture params. Currently unsupported and must not to be used.
        NV_ENC_PIC_PARAMS_VP8  vp8PicParams     #[in]: VP8 encode picture params. Currently unsupported and must not to be used.
        uint32_t               reserved[256]    #[in]: Reserved and must be set to 0.

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

    NVENCSTATUS NvEncodeAPICreateInstance(NV_ENCODE_API_FUNCTION_LIST *functionList)

    ctypedef NVENCSTATUS (*PNVENCOPENENCODESESSION)         (void* device, uint32_t deviceType, void** encoder)
    ctypedef NVENCSTATUS (*PNVENCGETENCODEGUIDCOUNT)        (void* encoder, uint32_t* encodeGUIDCount)
    ctypedef NVENCSTATUS (*PNVENCGETENCODEGUIDS)            (void* encoder, GUID* GUIDs, uint32_t guidArraySize, uint32_t* GUIDCount)
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPROFILEGUIDCOUNT) (void* encoder, GUID encodeGUID, uint32_t* encodeProfileGUIDCount)
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPROFILEGUIDS)     (void* encoder, GUID encodeGUID, GUID* profileGUIDs, uint32_t guidArraySize, uint32_t* GUIDCount)
    ctypedef NVENCSTATUS (*PNVENCGETINPUTFORMATCOUNT)       (void* encoder, GUID encodeGUID, uint32_t* inputFmtCount)
    ctypedef NVENCSTATUS (*PNVENCGETINPUTFORMATS)           (void* encoder, GUID encodeGUID, NV_ENC_BUFFER_FORMAT* inputFmts, uint32_t inputFmtArraySize, uint32_t* inputFmtCount)
    ctypedef NVENCSTATUS (*PNVENCGETENCODECAPS)             (void* encoder, GUID encodeGUID, NV_ENC_CAPS_PARAM* capsParam, int* capsVal)
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPRESETCOUNT)      (void* encoder, GUID encodeGUID, uint32_t* encodePresetGUIDCount)
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPRESETGUIDS)      (void* encoder, GUID encodeGUID, GUID* presetGUIDs, uint32_t guidArraySize, uint32_t* encodePresetGUIDCount)
    ctypedef NVENCSTATUS (*PNVENCGETENCODEPRESETCONFIG)     (void* encoder, GUID encodeGUID, GUID  presetGUID, NV_ENC_PRESET_CONFIG* presetConfig)
    ctypedef NVENCSTATUS (*PNVENCINITIALIZEENCODER)         (void* encoder, NV_ENC_INITIALIZE_PARAMS* createEncodeParams)
    ctypedef NVENCSTATUS (*PNVENCCREATEINPUTBUFFER)         (void* encoder, NV_ENC_CREATE_INPUT_BUFFER* createInputBufferParams)
    ctypedef NVENCSTATUS (*PNVENCDESTROYINPUTBUFFER)        (void* encoder, NV_ENC_INPUT_PTR inputBuffer)
    ctypedef NVENCSTATUS (*PNVENCCREATEBITSTREAMBUFFER)     (void* encoder, NV_ENC_CREATE_BITSTREAM_BUFFER* createBitstreamBufferParams)
    ctypedef NVENCSTATUS (*PNVENCDESTROYBITSTREAMBUFFER)    (void* encoder, NV_ENC_OUTPUT_PTR bitstreamBuffer)
    ctypedef NVENCSTATUS (*PNVENCENCODEPICTURE)             (void* encoder, NV_ENC_PIC_PARAMS* encodePicParams)
    ctypedef NVENCSTATUS (*PNVENCLOCKBITSTREAM)             (void* encoder, NV_ENC_LOCK_BITSTREAM* lockBitstreamBufferParams)
    ctypedef NVENCSTATUS (*PNVENCUNLOCKBITSTREAM)           (void* encoder, NV_ENC_OUTPUT_PTR bitstreamBuffer)
    ctypedef NVENCSTATUS (*PNVENCLOCKINPUTBUFFER)           (void* encoder, NV_ENC_LOCK_INPUT_BUFFER* lockInputBufferParams)
    ctypedef NVENCSTATUS (*PNVENCUNLOCKINPUTBUFFER)         (void* encoder, NV_ENC_INPUT_PTR inputBuffer)
    ctypedef NVENCSTATUS (*PNVENCGETENCODESTATS)            (void* encoder, NV_ENC_STAT* encodeStats)
    ctypedef NVENCSTATUS (*PNVENCGETSEQUENCEPARAMS)         (void* encoder, NV_ENC_SEQUENCE_PARAM_PAYLOAD* sequenceParamPayload)
    ctypedef NVENCSTATUS (*PNVENCREGISTERASYNCEVENT)        (void* encoder, NV_ENC_EVENT_PARAMS* eventParams)
    ctypedef NVENCSTATUS (*PNVENCUNREGISTERASYNCEVENT)      (void* encoder, NV_ENC_EVENT_PARAMS* eventParams)
    ctypedef NVENCSTATUS (*PNVENCMAPINPUTRESOURCE)          (void* encoder, NV_ENC_MAP_INPUT_RESOURCE* mapInputResParams)
    ctypedef NVENCSTATUS (*PNVENCUNMAPINPUTRESOURCE)        (void* encoder, NV_ENC_INPUT_PTR mappedInputBuffer)
    ctypedef NVENCSTATUS (*PNVENCDESTROYENCODER)            (void* encoder)
    ctypedef NVENCSTATUS (*PNVENCINVALIDATEREFFRAMES)       (void* encoder, uint64_t invalidRefFrameTimeStamp)
    ctypedef NVENCSTATUS (*PNVENCOPENENCODESESSIONEX)       (NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS *openSessionExParams, void** encoder)
    ctypedef NVENCSTATUS (*PNVENCREGISTERRESOURCE)          (void* encoder, NV_ENC_REGISTER_RESOURCE* registerResParams)
    ctypedef NVENCSTATUS (*PNVENCUNREGISTERRESOURCE)        (void* encoder, NV_ENC_REGISTERED_PTR registeredRes)
    ctypedef NVENCSTATUS (*PNVENCRECONFIGUREENCODER)        (void* encoder, NV_ENC_RECONFIGURE_PARAMS* reInitEncodeParams)

    ctypedef struct NV_ENCODE_API_FUNCTION_LIST:
        uint32_t    version         #[in]: Client should pass NV_ENCODE_API_FUNCTION_LIST_VER.
        uint32_t    reserved        #[in]: Reserved and should be set to 0.
        PNVENCOPENENCODESESSION         nvEncOpenEncodeSession          #[out]: Client should access ::NvEncOpenEncodeSession() API through this pointer.
        PNVENCGETENCODEGUIDCOUNT        nvEncGetEncodeGUIDCount         #[out]: Client should access ::NvEncGetEncodeGUIDCount() API through this pointer.
        PNVENCGETENCODEPROFILEGUIDCOUNT nvEncGetEncodeProfileGUIDCount  #[out]: Client should access ::NvEncGetEncodeProfileGUIDCount() API through this pointer.
        PNVENCGETENCODEPROFILEGUIDS     nvEncGetEncodeProfileGUIDs      #[out]: Client should access ::NvEncGetEncodeProfileGUIDs() API through this pointer.
        PNVENCGETENCODEGUIDS            nvEncGetEncodeGUIDs             #[out]: Client should access ::NvEncGetEncodeGUIDs() API through this pointer.
        PNVENCGETINPUTFORMATCOUNT       nvEncGetInputFormatCount        #[out]: Client should access ::NvEncGetInputFormatCount() API through this pointer.
        PNVENCGETINPUTFORMATS           nvEncGetInputFormats            #[out]: Client should access ::NvEncGetInputFormats() API through this pointer.
        PNVENCGETENCODECAPS             nvEncGetEncodeCaps              #[out]: Client should access ::NvEncGetEncodeCaps() API through this pointer.
        PNVENCGETENCODEPRESETCOUNT      nvEncGetEncodePresetCount       #[out]: Client should access ::NvEncGetEncodePresetCount() API through this pointer.
        PNVENCGETENCODEPRESETGUIDS      nvEncGetEncodePresetGUIDs       #[out]: Client should access ::NvEncGetEncodePresetGUIDs() API through this pointer.
        PNVENCGETENCODEPRESETCONFIG     nvEncGetEncodePresetConfig      #[out]: Client should access ::NvEncGetEncodePresetConfig() API through this pointer.
        PNVENCINITIALIZEENCODER         nvEncInitializeEncoder          #[out]: Client should access ::NvEncInitializeEncoder() API through this pointer.
        PNVENCCREATEINPUTBUFFER         nvEncCreateInputBuffer          #[out]: Client should access ::NvEncCreateInputBuffer() API through this pointer.
        PNVENCDESTROYINPUTBUFFER        nvEncDestroyInputBuffer         #[out]: Client should access ::NvEncDestroyInputBuffer() API through this pointer.
        PNVENCCREATEBITSTREAMBUFFER     nvEncCreateBitstreamBuffer      #[out]: Client should access ::NvEncCreateBitstreamBuffer() API through this pointer.
        PNVENCDESTROYBITSTREAMBUFFER    nvEncDestroyBitstreamBuffer     #[out]: Client should access ::NvEncDestroyBitstreamBuffer() API through this pointer.
        PNVENCENCODEPICTURE             nvEncEncodePicture              #[out]: Client should access ::NvEncEncodePicture() API through this pointer.
        PNVENCLOCKBITSTREAM             nvEncLockBitstream              #[out]: Client should access ::NvEncLockBitstream() API through this pointer.
        PNVENCUNLOCKBITSTREAM           nvEncUnlockBitstream            #[out]: Client should access ::NvEncUnlockBitstream() API through this pointer.
        PNVENCLOCKINPUTBUFFER           nvEncLockInputBuffer            #[out]: Client should access ::NvEncLockInputBuffer() API through this pointer.
        PNVENCUNLOCKINPUTBUFFER         nvEncUnlockInputBuffer          #[out]: Client should access ::NvEncUnlockInputBuffer() API through this pointer.
        PNVENCGETENCODESTATS            nvEncGetEncodeStats             #[out]: Client should access ::NvEncGetEncodeStats() API through this pointer.
        PNVENCGETSEQUENCEPARAMS         nvEncGetSequenceParams          #[out]: Client should access ::NvEncGetSequenceParams() API through this pointer.
        PNVENCREGISTERASYNCEVENT        nvEncRegisterAsyncEvent         #[out]: Client should access ::NvEncRegisterAsyncEvent() API through this pointer.
        PNVENCUNREGISTERASYNCEVENT      nvEncUnregisterAsyncEvent       #[out]: Client should access ::NvEncUnregisterAsyncEvent() API through this pointer.
        PNVENCMAPINPUTRESOURCE          nvEncMapInputResource           #[out]: Client should access ::NvEncMapInputResource() API through this pointer.
        PNVENCUNMAPINPUTRESOURCE        nvEncUnmapInputResource         #[out]: Client should access ::NvEncUnmapInputResource() API through this pointer.
        PNVENCDESTROYENCODER            nvEncDestroyEncoder             #[out]: Client should access ::NvEncDestroyEncoder() API through this pointer.
        PNVENCINVALIDATEREFFRAMES       nvEncInvalidateRefFrames        #[out]: Client should access ::NvEncInvalidateRefFrames() API through this pointer.
        PNVENCOPENENCODESESSIONEX       nvEncOpenEncodeSessionEx        #[out]: Client should access ::NvEncOpenEncodeSession() API through this pointer.
        PNVENCREGISTERRESOURCE          nvEncRegisterResource           #[out]: Client should access ::NvEncRegisterResource() API through this pointer.
        PNVENCUNREGISTERRESOURCE        nvEncUnregisterResource         #[out]: Client should access ::NvEncUnregisterResource() API through this pointer.
        PNVENCRECONFIGUREENCODER        nvEncReconfigureEncoder         #[out]: Client should access ::NvEncReconfigureEncoder() API through this pointer.
        void*                           reserved2[285]                  #[in]:  Reserved and must be set to NULL

include "constants.pxi"

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
log("NV_ENC_STATUS=%s", NV_ENC_STATUS_TXT)

CODEC_PROFILES = {
                  #NV_ENC_H264_PROFILE_BASELINE_GUID
                  "baseline"    : 66,
                  #NV_ENC_H264_PROFILE_MAIN_GUID
                  "main"        : 77,
                  #NV_ENC_H264_PROFILE_HIGH_GUID
                  "high"        : 100,
                  #NV_ENC_H264_PROFILE_STEREO_GUID
                  "stereo"      : 128,
                  }

cdef guidstr(GUID guid):
    #really ugly! (surely there's a way using struct.unpack ?)
    #is this even endian safe? do we care? (always on the same system)
    parts = []
    for v, s in ((guid.Data1, 4), (guid.Data2, 2), (guid.Data3, 2)):
        b = bytearray(s)
        for j in range(s):
            b[s-j-1] = v % 256
            v = v / 256
        parts.append(b)
    parts.append(bytearray(guid.get("Data4")[:2]))
    parts.append(bytearray(guid.get("Data4")[2:8]))
    s = "-".join([binascii.hexlify(str(b)).upper() for b in parts])
    #log.info("guidstr(%s)=%s", guid, s)
    return s

cdef GUID c_parseguid(src):
    #just as ugly as above - shoot me now
    #only this format is allowed:
    sample_key = "CE788D20-AAA9-4318-92BB-AC7E858C8D36"
    if len(src)!=len(sample_key):
        raise Exception("invalid GUID format: expected %s characters but got %s" % (len(sample_key), len(src)))
    for i in range(len(sample_key)):
        if sample_key[i]=="-":
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
        b = bytearray(binascii.unhexlify(parts[i]))
        v = 0
        for j in range(s):
            v += b[j]<<((s-j-1)*8)
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
    sample_key = "CE788D20-AAA9-4318-92BB-AC7E858C8D36"
    x = c_parseguid(sample_key)
    v = guidstr(x)
    assert v==sample_key, "expected %s but got %s" % (sample_key, v)
test_parse()

cdef GUID CLIENT_KEY_GUID
memset(&CLIENT_KEY_GUID, 0, sizeof(GUID))
if CLIENT_KEY:
    try:
        CLIENT_KEY_GUID = c_parseguid(CLIENT_KEY)
    except Exception, e:
        log.error("invalid client key specified: %s", e)


CODEC_GUIDS = {
    guidstr(NV_ENC_CODEC_H264_GUID)     : "H264",
    guidstr(NV_ENC_CODEC_MPEG2_GUID)    : "MPEG2",
    guidstr(NV_ENC_CODEC_VC1_GUID)      : "VC1",
    guidstr(NV_ENC_CODEC_JPEG_GUID)     : "JPEG",
    guidstr(NV_ENC_CODEC_VP8_GUID)      : "VP8",
    }

CODEC_PROFILES_GUIDS = {
    guidstr(NV_ENC_CODEC_H264_GUID) : {
        guidstr(NV_ENC_CODEC_PROFILE_AUTOSELECT_GUID)       : "auto",
        guidstr(NV_ENC_H264_PROFILE_BASELINE_GUID)          : "baseline",
        guidstr(NV_ENC_H264_PROFILE_MAIN_GUID)              : "main",
        guidstr(NV_ENC_H264_PROFILE_HIGH_GUID)              : "high",
        guidstr(NV_ENC_H264_PROFILE_STEREO_GUID)            : "stereo",
        guidstr(NV_ENC_H264_PROFILE_SVC_TEMPORAL_SCALABILTY): "temporal",
        guidstr(NV_ENC_H264_PROFILE_CONSTRAINED_HIGH_GUID)  : "constrained-high",
        },
    guidstr(NV_ENC_CODEC_MPEG2_GUID) : {
        guidstr(NV_ENC_CODEC_PROFILE_AUTOSELECT_GUID)       : "auto",
        guidstr(NV_ENC_MPEG2_PROFILE_SIMPLE_GUID)           : "simple",
        guidstr(NV_ENC_MPEG2_PROFILE_MAIN_GUID)             : "main",
        guidstr(NV_ENC_MPEG2_PROFILE_HIGH_GUID)             : "high",
        },
    guidstr(NV_ENC_CODEC_VC1_GUID) : {
        guidstr(NV_ENC_CODEC_PROFILE_AUTOSELECT_GUID)       : "auto",
        guidstr(NV_ENC_VC1_PROFILE_SIMPLE_GUID)             : "simple",
        guidstr(NV_ENC_VC1_PROFILE_MAIN_GUID)               : "main",
        guidstr(NV_ENC_VC1_PROFILE_ADVANCED_GUID)           : "advanced",
        },
    guidstr(NV_ENC_CODEC_JPEG_GUID) : {
        guidstr(NV_ENC_CODEC_PROFILE_AUTOSELECT_GUID)       : "auto",
        guidstr(NV_ENC_JPEG_PROFILE_BASELINE_GUID)          : "baseline"
        },
    guidstr(NV_ENC_CODEC_VP8_GUID) : {
        guidstr(NV_ENC_CODEC_PROFILE_AUTOSELECT_GUID)       : "auto",
        },
    }


CODEC_PRESETS_GUIDS = {
    guidstr(NV_ENC_PRESET_DEFAULT_GUID)                     : "default",
    guidstr(NV_ENC_PRESET_HP_GUID)                          : "hp",
    guidstr(NV_ENC_PRESET_HQ_GUID)                          : "hq",
    guidstr(NV_ENC_PRESET_BD_GUID)                          : "bd",
    guidstr(NV_ENC_PRESET_LOW_LATENCY_DEFAULT_GUID)         : "low-latency",
    guidstr(NV_ENC_PRESET_LOW_LATENCY_HQ_GUID)              : "low-latency-hq",
    guidstr(NV_ENC_PRESET_LOW_LATENCY_HP_GUID)              : "low-latency-hp",
    }

#try to map preset names to a "speed" value:
PRESET_SPEED = {
    "bd"            : 0,
    "hq"            : 20,
    "default"       : 40,
    "hp"            : 50,
    "low-latency-hq": 60,
    "low-latency"   : 80,
    "low-latency-hp": 100,
    }
PRESET_QUALITY = {
    "bd"            : 100,
    "hq"            : 80,
    "default"       : 60,
    "hp"            : 50,
    "low-latency-hq": 40,
    "low-latency"   : 20,
    "low-latency-hp": 0,
    }



BUFFER_FORMAT = {
        NV_ENC_BUFFER_FORMAT_UNDEFINED              : "undefined",
        NV_ENC_BUFFER_FORMAT_NV12_PL                : "NV12_PL",
        NV_ENC_BUFFER_FORMAT_NV12_TILED16x16        : "NV12_TILED16x16",
        NV_ENC_BUFFER_FORMAT_NV12_TILED64x16        : "NV12_TILED64x16",
        NV_ENC_BUFFER_FORMAT_YV12_PL                : "YV12_PL",
        NV_ENC_BUFFER_FORMAT_YV12_TILED16x16        : "YV12_TILED16x16",
        NV_ENC_BUFFER_FORMAT_YV12_TILED64x16        : "YV12_TILED64x16",
        NV_ENC_BUFFER_FORMAT_IYUV_PL                : "IYUV_PL",
        NV_ENC_BUFFER_FORMAT_IYUV_TILED16x16        : "IYUV_TILED16x16",
        NV_ENC_BUFFER_FORMAT_IYUV_TILED64x16        : "IYUV_TILED64x16",
        NV_ENC_BUFFER_FORMAT_YUV444_PL              : "YUV444_PL",
        NV_ENC_BUFFER_FORMAT_YUV444_TILED16x16      : "YUV444_TILED16x16",
        NV_ENC_BUFFER_FORMAT_YUV444_TILED64x16      : "YUV444_TILED64x16",
        }


COLORSPACES = ("BGRX", )
def get_colorspaces():
    return COLORSPACES

WIDTH_MASK = 0xFFFE
HEIGHT_MASK = 0xFFFE

#Note: these counters should be per-device, but:
#1) although the support code is there, we currently only use one device
#2) when we call get_runtime_factor(), we don't know which device is going to get used!
context_counter = AtomicInteger()
context_failures_history = maxdeque(100)

free_memory = 0
total_memory = 0

def get_runtime_factor():
    global context_failures_history, context_counter, free_memory, total_memory
    cc = context_counter.get()
    fm_pct = 100
    if total_memory>0:
        fm_pct = int(100.0*free_memory/total_memory)
    if len(context_failures_history)==0 and cc<8 and fm_pct>=25:
        #no problems!
        log("nvenc.get_runtime_factor()=%s", 1.0)
        return 1.0
    #try to avoid using too many contexts
    #(usually, we can have up to 32 contexts per card)
    f = max(0, 1.0 - (max(0, cc-8)/64.0))
    #if we have had errors recently, lower our chances further:
    now = time.time()
    recent_errors = [c for t,c in context_failures_history if (now-t)<60]
    if len(recent_errors)>0:
        last_count = recent_errors[-1]
        if last_count<=cc:
            #the last recent error had as many contexts available
            #so this is unlikely to work, lower more:
            f /= 2.0
    #if we are low on free memory, reduce further:
    if fm_pct<30:
        f *= fm_pct/30.0
    log("nvenc.get_runtime_factor()=%s", f)
    return f

def get_spec(encoding, colorspace):
    assert encoding in get_encodings(), "invalid format: %s (must be one of %s" % (format, get_encodings())
    assert colorspace in COLORSPACES, "invalid colorspace: %s (must be one of %s)" % (colorspace, COLORSPACES)
    #ratings: quality, speed, setup cost, cpu cost, gpu cost, latency, max_w, max_h
    cs = codec_spec(Encoder, codec_type=get_type(), encoding=encoding,
                      quality=80, speed=100, setup_cost=80, cpu_cost=10, gpu_cost=100,
                      #using a hardware encoder for something this small is silly:
                      min_w=32, min_h=32,
                      max_w=4096, max_h=4096,
                      can_scale=True,
                      width_mask=WIDTH_MASK, height_mask=HEIGHT_MASK)
    cs.get_runtime_factor = get_runtime_factor
    return cs


def get_version():
    return NVENCAPI_VERSION

def get_type():
    return "nvenc"

def get_info():
    return  {"version"          : get_version()}

def get_encodings():
    return ["h264"]

cdef int roundup(int n, int m):
    return (n + m - 1) & ~(m - 1)


def device_info(d):
    return "%s @ %s" % (d.name(), d.pci_bus_id())

cdef cuda_init_devices():
    start = time.time()
    log.info("PyCUDA initialization (this may take a few seconds)")
    driver.init()
    ngpus = driver.Device.count()
    log("PyCUDA found %s devices:", ngpus)
    devices = {}
    da = driver.device_attribute
    for i in range(ngpus):
        d = driver.Device(i)
        mem = d.total_memory()
        host_mem = d.get_attribute(da.CAN_MAP_HOST_MEMORY)
        log(" max block sizes: (%s, %s, %s)", d.get_attribute(da.MAX_BLOCK_DIM_X), d.get_attribute(da.MAX_BLOCK_DIM_Y), d.get_attribute(da.MAX_BLOCK_DIM_Z))
        log(" max grid sizes: (%s, %s, %s)", d.get_attribute(da.MAX_GRID_DIM_X), d.get_attribute(da.MAX_GRID_DIM_Y), d.get_attribute(da.MAX_GRID_DIM_Z))
        SMmajor, SMminor = d.compute_capability()
        has_nvenc = ((SMmajor<<4) + SMminor) >= 0x30
        pre = "-"
        if host_mem and (has_nvenc or FORCE):
            pre = "+"
            devices[i] = device_info(d)
        log(" %s %s (%sMB)", pre, device_info(d), mem/1024/1024)
        max_width = d.get_attribute(da.MAXIMUM_TEXTURE2D_WIDTH)
        max_height = d.get_attribute(da.MAXIMUM_TEXTURE2D_HEIGHT)
        log(" Can Map Host Memory: %s, Compute Mode: %s, max dimensions: %sx%s", host_mem, (SMmajor, SMminor), max_width, max_height)
    end = time.time()
    log("cuda_init_devices() took %.1fms", 1000.0*(end-start))
    return devices
cuda_devices = None
def get_cuda_devices():
    global cuda_devices
    if cuda_devices is None:
        cuda_devices = cuda_init_devices()
        if len(cuda_devices)>1:
            log.info(" found %s CUDA devices:", len(cuda_devices))
            for device_id in sorted(cuda_devices.keys()):
                log.info(" + %s", cuda_devices.get(device_id))
        else:
            log.info(" using GPU device: %s", cuda_devices.values()[0])
    return cuda_devices


def cuda_check():
    global DEFAULT_CUDA_DEVICE_ID
    devices = get_cuda_devices()
    if len(devices)==0:
        raise ImportError("no CUDA devices found!")
    assert DEFAULT_CUDA_DEVICE_ID in cuda_devices.keys(), "specified CUDA device ID %s not found in %s" % (DEFAULT_CUDA_DEVICE_ID, devices)
    #create context for testing:
    d = driver.Device(DEFAULT_CUDA_DEVICE_ID)
    context = d.make_context(flags=driver.ctx_flags.SCHED_AUTO | driver.ctx_flags.MAP_HOST)
    log("cuda_check created test context, api_version=%s", context.get_api_version())
    context.pop()
    context.detach()


cdef nvencStatusInfo(NVENCSTATUS ret):
    if ret in NV_ENC_STATUS_TXT:
        return "%s: %s" % (ret, NV_ENC_STATUS_TXT[ret])
    return str(ret)

cdef raiseNVENC(NVENCSTATUS ret, msg=""):
    if ret!=0:
        raise Exception("%s - returned %s" % (msg, nvencStatusInfo(ret)))


#cache the cubin files for each device_id:
KERNEL_cubins = {}
cdef get_CUDA_kernel(device_id, kernel_name, kernel_source):
    start = time.time()
    global KERNEL_cubins
    cubin = KERNEL_cubins.get((device_id, kernel_name))
    if cubin is None:
        log("compiling for device %s: %s=%s", device_id, kernel_name, kernel_source)
        cubin = compile(kernel_source)
        KERNEL_cubins[(device_id, kernel_name)] = cubin
    #now load from cubin:
    mod = driver.module_from_buffer(cubin)
    kernel_function = mod.get_function(kernel_name)
    end = time.time()
    log("compilation of %s took %.1fms", kernel_name, 1000.0*(end-start))
    return kernel_name, kernel_function

cpdef get_BGRA2YUV444P(device_id):
    from xpra.codecs.nvenc.CUDA_rgb2yuv444p import BGRA2YUV444P_kernel
    return get_CUDA_kernel(device_id, "BGRA2YUV444P", BGRA2YUV444P_kernel)

cpdef get_BGRA2NV12(device_id):
    from xpra.codecs.nvenc.CUDA_rgb2nv12 import BGRA2NV12_kernel
    return get_CUDA_kernel(device_id, "BGRA2NV12", BGRA2NV12_kernel)


API_V2_WARNING = False


cdef class Encoder:
    cdef int width
    cdef int height
    cdef int input_width
    cdef int input_height
    cdef int encoder_width
    cdef int encoder_height
    cdef object src_format
    cdef object scaling
    cdef int speed
    cdef int quality
    #PyCUDA:
    cdef int device_id
    cdef object driver
    cdef object cuda_device_info
    cdef object cuda_device
    cdef object cuda_context
    cdef object kernel
    cdef object kernel_name
    cdef object max_block_sizes
    cdef object max_grid_sizes
    cdef int max_threads_per_block
    #NVENC:
    cdef NV_ENCODE_API_FUNCTION_LIST functionList               #@DuplicatedSignature
    cdef void *context
    cdef NV_ENC_REGISTERED_PTR inputHandle
    cdef object inputBuffer
    cdef object cudaInputBuffer
    cdef object cudaOutputBuffer
    cdef int inputPitch                     #note: this isn't the pitch (aka rowstride) we actually use!
                                            #just the value returned from the allocation call
    cdef int outputPitch
    cdef void *bitstreamBuffer
    cdef NV_ENC_BUFFER_FORMAT bufferFmt
    cdef object codec_name
    cdef object preset_name
    cdef object pixel_format
    #statistics, etc:
    cdef double time
    cdef int frames
    cdef object last_frame_times
    cdef long long bytes_in
    cdef long long bytes_out
    cdef int api_warning

    cdef GUID get_codec(self):
        codecs = self.query_codecs()
        #codecs={'H264': '6BC82762-4E63-4CA4-AA85-1E50F321F6BF'}
        assert self.codec_name in codecs, "%s not supported!?" % self.codec_name
        return c_parseguid(codecs.get(self.codec_name))

    cdef GUID get_preset(self, GUID codec):
        #PRESET:
        presets = self.query_presets(codec)
        #presets={'low-latency': '49DF21C5-6DFA-4FEB-9787-6ACC9EFFB726', 'bd': '82E3E450-BDBB-4E40-989C-82A90DF9EF32', 'default': 'B2DFB705-4EBD-4C49-9B5F-24A777D3E587', 'hp': '60E4C59F-E846-4484-A56D-CD45BE9FDDF6', 'hq': '34DBA71D-A77B-4B8F-9C3E-B6D5DA24C012', 'low-latency-hp': '67082A44-4BAD-48FA-98EA-93056D150A58', 'low-latency-hq': 'C5F733B9-EA97-4CF9-BEC2-BF78A74FD105'}
        self.preset_name = None

        options = {}
        #if a preset was specified, give it the best score possible (-1):
        if DESIRED_PRESET:
            options[-1] = DESIRED_PRESET
        #add all presets ranked by how far they are from the target speed and quality:
        for x in CODEC_PRESETS_GUIDS.values():
            preset_speed = PRESET_SPEED.get(x, 50)
            preset_quality = PRESET_QUALITY.get(x, 50)
            v = abs(preset_speed-self.speed) + abs(preset_quality-self.quality)
            options.setdefault(v, []).append(x)
        log("get_preset(%s) speed=%s, quality=%s, options=%s", guidstr(codec), self.speed, self.quality, options)
        for v in sorted(options.keys()):
            for preset in options.get(v):
                if preset and (preset in presets):
                    log("using preset '%s' for quality=%s, speed=%s", preset, self.speed, self.quality)
                    self.preset_name = preset
                    return c_parseguid(presets.get(preset))
        raise Exception("no low-latency presets available for '%s'!?" % self.codec_name)

    def init_context(self, int width, int height, src_format, encoding, int quality, int speed, scaling, options={}):    #@DuplicatedSignature
        assert encoding in get_encodings(), "invalid encoding %s" % encoding
        log("init_context%s", (width, height, src_format, encoding, quality, speed, scaling, options))
        self.width = width
        self.height = height
        self.speed = speed
        self.quality = quality
        self.scaling = scaling
        v, u = scaling or (1,1)
        self.input_width = roundup(width, 32)
        self.input_height = roundup(height, 32)
        self.encoder_width = roundup(width*v/u, 32)
        self.encoder_height = roundup(height*v/u, 32)
        self.src_format = src_format
        self.codec_name = "H264"
        self.preset_name = None
        self.frames = 0
        self.cuda_device = None
        self.cuda_context = None
        self.pixel_format = ""
        self.last_frame_times = maxdeque(200)
        start = time.time()

        self.device_id = options.get("cuda_device", DEFAULT_CUDA_DEVICE_ID)
        self.init_cuda()

        end = time.time()
        log("init_context%s took %1.fms", (width, height, src_format, quality, speed, options), (end-start)*1000.0)

    cdef init_cuda(self):
        assert self.device_id in get_cuda_devices().keys(), "invalid device_id '%s' (available: %s)" % (self.device_id, cuda_devices)
        global context_counter, context_failures_history
        log("init_cuda() device_id=%s", self.device_id)
        try:
            self.cuda_device = driver.Device(DEFAULT_CUDA_DEVICE_ID)
            log("init_cuda() cuda_device=%s (%s)", self.cuda_device, device_info(self.cuda_device))
            self.cuda_context = self.cuda_device.make_context(flags=driver.ctx_flags.SCHED_AUTO | driver.ctx_flags.MAP_HOST)
            log("init_cuda() cuda_context=%s", self.cuda_context)
            self.cuda_device_info = {
                "device.name"       : self.cuda_device.name(),
                "device.pci_bus_id" : self.cuda_device.pci_bus_id(),
                "device.memory"     : self.cuda_device.total_memory()/1024/1024,
                "api_version"       : self.cuda_context.get_api_version()}
        except driver.MemoryError, e:
            context_failures_history.append((time.time(), context_counter.get()))
            log("init_cuda() %s", e)
            raise TransientCodecException("could not initialize cuda: %s" % e)
        #use alias to make code easier to read:
        d = self.cuda_device
        da = driver.device_attribute
        try:
            if USE_YUV444P:
                #FIXME: YUV444P doesn't work and I don't know why
                #No idea what "separateColourPlaneFlag" is meant to do either
                self.kernel_name, self.kernel = get_BGRA2YUV444P(self.device_id)
                self.bufferFmt = NV_ENC_BUFFER_FORMAT_YUV444_PL
                self.pixel_format = "YUV444P"
                #3 full planes:
                plane_size_div = 1
            else:
                self.kernel_name, self.kernel = get_BGRA2NV12(self.device_id)
                self.bufferFmt = NV_ENC_BUFFER_FORMAT_NV12_PL
                self.pixel_format = "NV12"
                #1 full Y plane and 2 U+V planes subsampled by 4:
                plane_size_div = 2

            #allocate CUDA input buffer (on device) 32-bit RGB
            #(and make it bigger just in case - subregions from XShm can have a huge rowstride):
            max_input_stride = max(2560, self.input_width)*4
            self.cudaInputBuffer, self.inputPitch = driver.mem_alloc_pitch(max_input_stride, self.input_height, 16)
            log("CUDA Input Buffer=%#x, pitch=%s", int(self.cudaInputBuffer), self.inputPitch)
            #allocate CUDA output buffer (on device):
            self.cudaOutputBuffer, self.outputPitch = driver.mem_alloc_pitch(self.encoder_width, self.encoder_height*3/plane_size_div, 16)
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

            self.init_nvenc()
        finally:
            self.cuda_context.pop()

    cdef init_nvenc(self):
        cdef GUID codec
        cdef GUID preset
        cdef GUID profile
        cdef NV_ENC_INITIALIZE_PARAMS params
        cdef NV_ENC_PRESET_CONFIG *presetConfig     #@DuplicatedSignature
        cdef NV_ENC_REGISTER_RESOURCE registerResource
        cdef NV_ENC_CREATE_INPUT_BUFFER createInputBufferParams
        cdef NV_ENC_CREATE_BITSTREAM_BUFFER createBitstreamBufferParams
        cdef long resource

        self.open_encode_session()
        codec = self.get_codec()
        preset = self.get_preset(codec)

        input_format = BUFFER_FORMAT[self.bufferFmt]
        input_formats = self.query_input_formats(codec)
        assert input_format in input_formats, "%s does not support %s (only: %s)" %  (self.codec_name, input_format, input_formats)
        try:
            presetConfig = self.get_preset_config(self.preset_name, codec, preset)

            #PROFILE
            profiles = self.query_profiles(NV_ENC_CODEC_H264_GUID)
            #self.gopLength = presetConfig.presetCfg.gopLength

            memset(&params, 0, sizeof(NV_ENC_INITIALIZE_PARAMS))
            params.version = NV_ENC_INITIALIZE_PARAMS_VER
            params.encodeGUID = codec    #ie: NV_ENC_CODEC_H264_GUID
            params.presetGUID = preset
            params.encodeWidth = self.encoder_width
            params.encodeHeight = self.encoder_height
            params.darWidth = self.encoder_width
            params.darHeight = self.encoder_height
            params.enableEncodeAsync = 0            #not supported on Linux
            params.enablePTD = 0                    #not supported in sync mode!?
            if presetConfig!=NULL:
                presetConfig.presetCfg.encodeCodecConfig.h264Config.enableVFR = 1
                presetConfig.presetCfg.encodeCodecConfig.h264Config.idrPeriod = NVENC_INFINITE_GOPLENGTH
                #needed for YUV444P?
                #presetConfig.presetCfg.encodeCodecConfig.h264Config.separateColourPlaneFlag = 1
                params.encodeConfig = &presetConfig.presetCfg
            else:
                self.preset_name = None
            raiseNVENC(self.functionList.nvEncInitializeEncoder(self.context, &params), "initializing encoder")
            log("NVENC initialized with '%s' codec and '%s' preset" % (self.codec_name, self.preset_name))

            #register CUDA input buffer:
            memset(&registerResource, 0, sizeof(NV_ENC_REGISTER_RESOURCE))
            registerResource.version = NV_ENC_REGISTER_RESOURCE_VER
            registerResource.resourceType = NV_ENC_INPUT_RESOURCE_TYPE_CUDADEVICEPTR
            resource = int(self.cudaOutputBuffer)
            registerResource.resourceToRegister = <void *> resource
            registerResource.width = self.encoder_width
            registerResource.height = self.encoder_height
            registerResource.pitch = self.outputPitch
            raiseNVENC(self.functionList.nvEncRegisterResource(self.context, &registerResource), "registering CUDA input buffer")
            self.inputHandle = registerResource.registeredResource
            log("input handle for CUDA buffer: %#x", <unsigned long> self.inputHandle)

            #allocate output buffer:
            memset(&createBitstreamBufferParams, 0, sizeof(NV_ENC_CREATE_BITSTREAM_BUFFER))
            createBitstreamBufferParams.version = NV_ENC_CREATE_BITSTREAM_BUFFER_VER
            #this is the uncompressed size - must be big enough for the compressed stream:
            createBitstreamBufferParams.size = self.encoder_width*self.encoder_height*3/2
            createBitstreamBufferParams.memoryHeap = NV_ENC_MEMORY_HEAP_SYSMEM_CACHED
            raiseNVENC(self.functionList.nvEncCreateBitstreamBuffer(self.context, &createBitstreamBufferParams), "creating output buffer")
            self.bitstreamBuffer = createBitstreamBufferParams.bitstreamBuffer
            log("output bitstream buffer=%#x", <unsigned long> self.bitstreamBuffer)
        finally:
            if presetConfig!=NULL:
                free(presetConfig)

    def get_info(self):                     #@DuplicatedSignature
        cdef float pps
        info = {"width"     : self.width,
                "height"    : self.height,
                "frames"    : self.frames,
                "codec"     : self.codec_name,
                "encoder_width"     : self.encoder_width,
                "encoder_height"    : self.encoder_height,
                "version"   : get_version()}
        if self.scaling!=(1,1):
            info.update({
                "input_width"       : self.input_width,
                "input_height"      : self.input_height,
                "scaling"           : self.scaling})
        info.update(self.cuda_device_info)
        if self.src_format:
            info["src_format"] = self.src_format
        if self.pixel_format:
            info["pixel_format"] = self.pixel_format
        if self.bytes_in>0 and self.bytes_out>0:
            info.update({
                "bytes_in"  : self.bytes_in,
                "bytes_out" : self.bytes_out,
                "ratio_pct" : int(100.0 * self.bytes_out / self.bytes_in)})
        if self.preset_name:
            info["preset"] = self.preset_name
        if self.frames>0 and self.time>0:
            pps = float(self.width) * float(self.height) * float(self.frames) / self.time
            info["total_time_ms"] = int(self.time*1000.0)
            info["pixels_per_second"] = int(pps)
        if total_memory>0:
            info["free_memory"] = int(free_memory)
            info["total_memory"] = int(total_memory)
            info["free_memory_pct"] = int(100.0*free_memory/total_memory)
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
            info["fps"] = int(f/(now-last_time))
            info["ms_per_frame"] = int(1000.0*ms_per_frame/f)
        return info

    def __str__(self):
        return "nvenc(%s/%s - %sx%s)" % (self.src_format, self.pixel_format, self.width, self.height)

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

    cdef cuda_clean(self):
        if self.context!=NULL:
            self.flushEncoder()
        if self.inputHandle!=NULL and self.context!=NULL:
            log("clean() unregistering CUDA output buffer input handle %#x", <unsigned long> self.inputHandle)
            raiseNVENC(self.functionList.nvEncUnregisterResource(self.context, self.inputHandle), "unregistering CUDA input buffer")
            self.inputHandle = NULL
        if self.inputBuffer is not None:
            log("clean() freeing CUDA host buffer %s", self.inputBuffer)
            self.inputBuffer = None
        if self.cudaInputBuffer is not None:
            log("clean() freeing CUDA input buffer %#x", int(self.cudaInputBuffer))
            self.cudaInputBuffer.free()
            self.cudaInputBuffer = None
        if self.cudaOutputBuffer is not None:
            log("clean() freeing CUDA output buffer %#x", int(self.cudaOutputBuffer))
            self.cudaOutputBuffer.free()
            self.cudaOutputBuffer = None
        if self.context!=NULL:
            if self.bitstreamBuffer!=NULL:
                log("clean() destroying output bitstream buffer %#x", <unsigned long> self.bitstreamBuffer)
                raiseNVENC(self.functionList.nvEncDestroyBitstreamBuffer(self.context, self.bitstreamBuffer), "destroying output buffer")
                self.bitstreamBuffer = NULL
            log("clean() destroying encoder %#x", <unsigned long> self.context)
            raiseNVENC(self.functionList.nvEncDestroyEncoder(self.context), "destroying context")
            self.context = NULL
            global context_counter
            context_counter.decrease()
            log("clean() (still %s contexts in use)", context_counter)

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

    def get_client_options(self, options):
        client_options = {"frame" : self.frames}
        if self.scaling!=(1,1):
            client_options["scaled_size"] = self.encoder_width, self.encoder_height
        return client_options

    def set_encoding_speed(self, speed):
        pass

    def set_encoding_quality(self, quality):
        pass


    cdef flushEncoder(self):
        cdef NV_ENC_PIC_PARAMS picParams
        memset(&picParams, 0, sizeof(NV_ENC_PIC_PARAMS))
        picParams.version = NV_ENC_PIC_PARAMS_VER
        picParams.encodePicFlags = NV_ENC_PIC_FLAG_EOS
        raiseNVENC(self.functionList.nvEncEncodePicture(self.context, &picParams), "flushing encoder buffer")

    def compress_image(self, image, options={}, retry=0):
        self.cuda_context.push()
        try:
            try:
                return self.do_compress_image(image, options)
            finally:
                self.cuda_context.pop()
        except driver.LogicError, e:
            if retry>0:
                raise e
            log.warn("PyCUDA error: %s", e)
            self.clean()
            self.init_cuda()
            return self.convert_image(image, options, retry+1)

    cdef do_compress_image(self, image, options={}):
        cdef const void* buf = NULL
        cdef Py_ssize_t buf_len = 0
        cdef NV_ENC_PIC_PARAMS picParams            #@DuplicatedSignature
        cdef NV_ENC_MAP_INPUT_RESOURCE mapInputResource
        cdef NV_ENC_LOCK_BITSTREAM lockOutputBuffer
        cdef size_t size
        cdef int input_size
        cdef int offset = 0
        cdef input_buf_len = 0
        cdef int x, y, image_stride, stride
        cdef int w, h
        cdef int i

        start = time.time()
        log("compress_image(%s, %s)", image, options)
        assert self.context!=NULL, "context is not initialized"
        assert image.get_planes()==ImageWrapper.PACKED, "invalid number of planes: %s" % image.get_planes()
        w = image.get_width()
        h = image.get_height()
        assert (w & WIDTH_MASK)<=self.input_width, "invalid width: %s" % w
        assert (h & HEIGHT_MASK)<=self.input_height, "invalid height: %s" % h
        pixels = image.get_pixels()
        image_stride = image.get_rowstride()
        input_size = self.inputPitch * self.input_height

        #FIXME: we should copy from pixels directly..
        #copy to input buffer:
        if image_stride<self.inputPitch:
            stride = image_stride
            assert len(pixels)<=input_size, "too many pixels (expected %s max, got %s) image: %sx%s stride=%s, input buffer: stride=%s, height=%s" % (input_size, len(pixels), w, h, stride, self.inputPitch, self.input_height)
            self.inputBuffer.data[:len(pixels)] = pixels
        else:
            #ouch, we need to copy the source pixels into the smaller buffer
            #before uploading to the device... this is probably costly!
            stride = self.inputPitch
            for i in range(h):
                self.inputBuffer.data[i*stride:(i+1)*stride] = pixels[i*image_stride:(i+1)*image_stride+stride]
        log("compress_image(..) host buffer populated with %s bytes (max %s)", len(pixels), input_size)

        #copy input buffer to CUDA buffer:
        driver.memcpy_htod(self.cudaInputBuffer, self.inputBuffer)
        log("compress_image(..) input buffer copied to device")

        #FIXME: find better values and validate against max_block/max_grid:
        blockw, blockh = 16, 16
        if self.pixel_format=="NV12":
            #(these values are derived from the kernel code - which we should know nothing about here..)
            #divide each dimension by 2 since we process 4 pixels at a time:
            dx, dy = 2, 2
        else:
            #YUV444P does one pixel at a time:
            dx, dy = 1, 1
        gridw = max(1, w/blockw/dx)
        if gridw*2*blockw<w:
            gridw += 1
        gridh = max(1, h/blockh/dy)
        #if dy made us round down, add one:
        if gridh*dy*blockh<h:
            gridh += 1
        log("compress_image(..) calling CUDA CSC kernel %s", self.kernel_name)
        in_w, in_h = self.input_width, self.input_height
        if self.scaling!=(1,1):
            #scaling so scale exact dimensions, not padded input dimensions:
            in_w, in_h = w, h
        self.kernel(self.cudaInputBuffer, numpy.int32(in_w), numpy.int32(in_h), numpy.int32(stride),
                    self.cudaOutputBuffer, numpy.int32(self.encoder_width), numpy.int32(self.encoder_height), numpy.int32(self.outputPitch),
                    numpy.int32(w), numpy.int32(h),
                    block=(blockw,blockh,1), grid=(gridw, gridh))
        #a block is a group of threads: (blockw * blockh) threads
        #a grid is a group of blocks: (gridw * gridh) blocks
        csc_end = time.time()
        log("compress_image(..) kernel executed - CSC took %.1f ms", (csc_end - start)*1000.0)

        #map buffer so nvenc can access it:
        memset(&mapInputResource, 0, sizeof(NV_ENC_MAP_INPUT_RESOURCE))
        mapInputResource.version = NV_ENC_MAP_INPUT_RESOURCE_VER
        mapInputResource.registeredResource  = self.inputHandle
        raiseNVENC(self.functionList.nvEncMapInputResource(self.context, &mapInputResource), "mapping input resource")
        log("compress_image(..) device buffer mapped to %#x", <unsigned long> mapInputResource.mappedResource)

        size = 0
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
            else:
                picParams.pictureType = NV_ENC_PIC_TYPE_P
            picParams.codecPicParams.h264PicParams.displayPOCSyntax = 2*self.frames
            picParams.codecPicParams.h264PicParams.refPicFlag = self.frames==0
            picParams.codecPicParams.h264PicParams.sliceMode = 3            #sliceModeData specifies the number of slices
            picParams.codecPicParams.h264PicParams.sliceModeData = 1        #1 slice!
            #picParams.encodePicFlags = NV_ENC_PIC_FLAG_OUTPUT_SPSPPS
            picParams.frameIdx = self.frames
            #picParams.inputTimeStamp = int(1000.0 * time.time())
            #inputDuration = 0      #FIXME: use frame delay?
            picParams.rcParams.rateControlMode = NV_ENC_PARAMS_RC_VBR     #FIXME: check NV_ENC_CAPS_SUPPORTED_RATECONTROL_MODES caps
            picParams.rcParams.averageBitRate = 5000000   #5Mbits/s
            picParams.rcParams.maxBitRate = 10000000      #10Mbits/s

            raiseNVENC(self.functionList.nvEncEncodePicture(self.context, &picParams), "error during picture encoding")
            encode_end = time.time()
            log("compress_image(..) encoded in %.1f ms", (encode_end-csc_end)*1000.0)

            #lock output buffer:
            memset(&lockOutputBuffer, 0, sizeof(NV_ENC_LOCK_BITSTREAM))
            lockOutputBuffer.version = NV_ENC_LOCK_BITSTREAM_VER
            lockOutputBuffer.doNotWait = 0
            lockOutputBuffer.outputBitstream = self.bitstreamBuffer
            raiseNVENC(self.functionList.nvEncLockBitstream(self.context, &lockOutputBuffer), "locking output buffer")
            log("compress_image(..) output buffer locked, bitstreamBufferPtr=%#x", <unsigned long> lockOutputBuffer.bitstreamBufferPtr)

            #copy to python buffer:
            size = lockOutputBuffer.bitstreamSizeInBytes
            data = (<char *> lockOutputBuffer.bitstreamBufferPtr)[:size]
        finally:
            raiseNVENC(self.functionList.nvEncUnlockBitstream(self.context, self.bitstreamBuffer), "unlocking output buffer")
            raiseNVENC(self.functionList.nvEncUnmapInputResource(self.context, mapInputResource.mappedResource), "unmapping input resource")

        end = time.time()
        log("compress_image(..) download took %.1f ms", (end-encode_end)*1000.0)
        global free_memory, total_memory
        free_memory, total_memory = driver.mem_get_info()

        self.last_frame_times.append((start, end))
        self.time += end-start
        log("compress_image(..) returning %s bytes (%.1f%%), complete compression for frame %s took %.1fms", size, 100.0*size/input_size, self.frames, 1000.0*(end-start))
        #log("pixels head: %s", binascii.hexlify(data[:128]))
        client_options = self.get_client_options(options)
        self.bytes_in += input_size
        self.bytes_out += size
        self.frames += 1
        return data, client_options


    cdef NV_ENC_PRESET_CONFIG *get_preset_config(self, name, GUID encode_GUID, GUID preset_GUID):
        """ you must free it after use! """
        cdef NV_ENC_PRESET_CONFIG *presetConfig     #@DuplicatedSignature
        cdef int ret
        presetConfig = <NV_ENC_PRESET_CONFIG*> malloc(sizeof(NV_ENC_PRESET_CONFIG))
        assert presetConfig!=NULL, "failed to allocate memory for preset config"
        memset(presetConfig, 0, sizeof(NV_ENC_PRESET_CONFIG))
        presetConfig.version = NV_ENC_PRESET_CONFIG_VER
        ret = self.functionList.nvEncGetEncodePresetConfig(self.context, encode_GUID, preset_GUID, presetConfig)
        if ret!=0:
            global API_V2_WARNING
            #API version 2.0 fails every time
            #we call nvEncGetEncodePresetConfig and I don't know why
            #so warn just once:
            if NVENCAPI_VERSION==0x20:
                log("failed to get preset config for %s", name)
                if not API_V2_WARNING:
                    log.warn("API version %#x fails on nvEncGetEncodePresetConfig (no further warnings will be shown)", NVENCAPI_VERSION)
                API_V2_WARNING = True
            else:
                log.warn("failed to get preset config for %s (%s / %s): %s", name, guidstr(encode_GUID), guidstr(preset_GUID), NV_ENC_STATUS_TXT.get(ret, ret))
            return NULL
        return presetConfig

    cdef object query_presets(self, GUID encode_GUID):
        cdef uint32_t presetCount
        cdef uint32_t presetsRetCount
        cdef GUID* preset_GUIDs
        cdef GUID preset_GUID
        cdef NV_ENC_PRESET_CONFIG *presetConfig
        cdef NV_ENC_CONFIG encConfig

        presets = {}
        raiseNVENC(self.functionList.nvEncGetEncodePresetCount(self.context, encode_GUID, &presetCount), "getting preset count for %s" % guidstr(encode_GUID))
        log("%s presets:", presetCount)
        assert presetCount<2**8
        preset_GUIDs = <GUID*> malloc(sizeof(GUID) * presetCount)
        assert preset_GUIDs!=NULL, "could not allocate memory for %s preset GUIDs!" % (presetCount)
        try:
            raiseNVENC(self.functionList.nvEncGetEncodePresetGUIDs(self.context, encode_GUID, preset_GUIDs, presetCount, &presetsRetCount), "getting encode presets")
            assert presetsRetCount==presetCount
            for x in range(presetCount):
                preset_GUID = preset_GUIDs[x]
                preset_name = CODEC_PRESETS_GUIDS.get(guidstr(preset_GUID))
                log("* %s : %s", guidstr(preset_GUID), preset_name)
                presetConfig = self.get_preset_config(preset_name, encode_GUID, preset_GUID)
                if presetConfig!=NULL:
                    try:
                        encConfig = presetConfig.presetCfg
                        #log("presetConfig.presetCfg=%s", <unsigned long> encConfig)
                        log("   gopLength=%s, frameIntervalP=%s", encConfig.gopLength, encConfig.frameIntervalP)
                    finally:
                        free(presetConfig)
                if preset_name is None:
                    log.warn("unknown preset found: %s", guidstr(preset_GUID))
                else:
                    presets[preset_name] = guidstr(preset_GUID)
        finally:
            free(preset_GUIDs)
        log("query_presets(%s)=%s", guidstr(encode_GUID), presets)
        return presets

    cdef object query_profiles(self, GUID encode_GUID):
        cdef uint32_t profileCount
        cdef uint32_t profilesRetCount
        cdef GUID* profile_GUIDs
        cdef GUID profile_GUID

        profiles = {}
        raiseNVENC(self.functionList.nvEncGetEncodeProfileGUIDCount(self.context, encode_GUID, &profileCount), "getting profile count")
        log("%s profiles:", profileCount)
        assert profileCount<2**8
        profile_GUIDs = <GUID*> malloc(sizeof(GUID) * profileCount)
        assert profile_GUIDs!=NULL, "could not allocate memory for %s profile GUIDs!" % (profileCount)
        PROFILES_GUIDS = CODEC_PROFILES_GUIDS.get(guidstr(encode_GUID), {})
        try:
            raiseNVENC(self.functionList.nvEncGetEncodeProfileGUIDs(self.context, encode_GUID, profile_GUIDs, profileCount, &profilesRetCount), "getting encode profiles")
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

        input_formats = {}
        raiseNVENC(self.functionList.nvEncGetInputFormatCount(self.context, encode_GUID, &inputFmtCount), "getting input format count")
        log("%s input format types:", inputFmtCount)
        assert inputFmtCount>0 and inputFmtCount<2**8
        inputFmts = <NV_ENC_BUFFER_FORMAT*> malloc(sizeof(int) * inputFmtCount)
        assert inputFmts!=NULL, "could not allocate memory for %s input formats!" % (inputFmtCount)
        try:
            raiseNVENC(self.functionList.nvEncGetInputFormats(self.context, encode_GUID, inputFmts, inputFmtCount, &inputFmtsRetCount), "getting input formats")
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

    cdef int query_encoder_caps(self, GUID encodeGUID, NV_ENC_CAPS caps_type):
        cdef int val
        cdef NV_ENC_CAPS_PARAM encCaps
        memset(&encCaps, 0, sizeof(NV_ENC_CAPS_PARAM))
        encCaps.version = NV_ENC_CAPS_PARAM_VER
        encCaps.capsToQuery = caps_type

        raiseNVENC(self.functionList.nvEncGetEncodeCaps(self.context, encodeGUID, &encCaps, &val), "getting encode caps")
        return val

    cdef query_codecs(self, full_query=False):
        cdef uint32_t GUIDCount
        cdef uint32_t GUIDRetCount
        cdef GUID* encode_GUIDs
        cdef GUID encode_GUID

        raiseNVENC(self.functionList.nvEncGetEncodeGUIDCount(self.context, &GUIDCount), "getting encoder count")
        log("found %s encode GUIDs", GUIDCount)
        assert GUIDCount<2**8
        encode_GUIDs = <GUID*> malloc(sizeof(GUID) * GUIDCount)
        assert encode_GUIDs!=NULL, "could not allocate memory for %s encode GUIDs!" % (GUIDCount)
        codecs = {}
        try:
            raiseNVENC(self.functionList.nvEncGetEncodeGUIDs(self.context, encode_GUIDs, GUIDCount, &GUIDRetCount), "getting list of encode GUIDs")
            assert GUIDRetCount==GUIDCount, "expected %s items but got %s" % (GUIDCount, GUIDRetCount)
            for x in range(GUIDRetCount):
                encode_GUID = encode_GUIDs[x]
                codec_name = CODEC_GUIDS.get(guidstr(encode_GUID))
                log("[%s] %s : %s", x, codec_name, guidstr(encode_GUID))
                codecs[codec_name] = guidstr(encode_GUID)

                maxw = self.query_encoder_caps(encode_GUID, NV_ENC_CAPS_WIDTH_MAX)
                maxh = self.query_encoder_caps(encode_GUID, NV_ENC_CAPS_HEIGHT_MAX)
                async = self.query_encoder_caps(encode_GUID, NV_ENC_CAPS_ASYNC_ENCODE_SUPPORT)
                sep_plane = self.query_encoder_caps(encode_GUID, NV_ENC_CAPS_SEPARATE_COLOUR_PLANE)
                log(" max dimensions: %sx%s (async=%s)", maxw, maxh, async)
                rate_countrol = self.query_encoder_caps(encode_GUID, NV_ENC_CAPS_SUPPORTED_RATECONTROL_MODES)
                log(" rate control: %s, separate colour plane: %s", rate_countrol, sep_plane)

                if full_query:
                    presets = self.query_presets(encode_GUID)
                    log("  presets=%s", presets)

                    profiles = self.query_profiles(encode_GUID)
                    log("  profiles=%s", profiles)

                    input_formats = self.query_input_formats(encode_GUID)
                    log("  input formats=%s", input_formats)
        finally:
            free(encode_GUIDs)
        log("codecs=%s", codecs)
        return codecs


    cdef open_encode_session(self):
        global context_counter, context_failures_history
        cdef NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS params
        log("open_encode_session() cuda_context=%s", self.cuda_context)

        #get NVENC function pointers:
        memset(&self.functionList, 0, sizeof(NV_ENCODE_API_FUNCTION_LIST))
        self.functionList.version = NV_ENCODE_API_FUNCTION_LIST_VER
        raiseNVENC(NvEncodeAPICreateInstance(&self.functionList), "getting API function list")
        assert self.functionList.nvEncOpenEncodeSessionEx!=NULL, "looks like NvEncodeAPICreateInstance failed!"

        #get the CUDA context (C pointer):
        cdef CUcontext cuda_context
        cdef CUresult result
        result = cuCtxGetCurrent(&cuda_context)
        assert result==0, "failed to get current cuda context"

        #NVENC init:
        memset(&params, 0, sizeof(NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS))
        params.version = NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER
        params.deviceType = NV_ENC_DEVICE_TYPE_CUDA
        params.device = <void*> cuda_context
        params.clientKeyPtr = &CLIENT_KEY_GUID
        params.apiVersion = NVENCAPI_VERSION
        log("calling nvEncOpenEncodeSessionEx @ %#x", <unsigned long> self.functionList.nvEncOpenEncodeSessionEx)
        cdef int ret            #@DuplicatedSignature
        ret = self.functionList.nvEncOpenEncodeSessionEx(&params, &self.context)
        if ret==NV_ENC_ERR_UNSUPPORTED_DEVICE:
            context_failures_history.append((time.time(), context_counter.get()))
            msg = "NV_ENC_ERR_UNSUPPORTED_DEVICE: could not open encode session (out of resources / no more codec contexts?)"
            log(msg)
            raise TransientCodecException(msg)
        raiseNVENC(ret, "opening session")
        context_counter.increase()
        log("success, encoder context=%#x (%s contexts in use)", <unsigned long> self.context, context_counter)


def init_module():
    #check that we have CUDA device(s):
    cuda_check()

    #check NVENC availibility:
    colorspaces = get_colorspaces()
    if len(colorspaces)==0:
        raise ImportError("cannot use NVENC: no colorspaces available")
    test_encoder = Encoder()
    for encoding in get_encodings():
        src_format = colorspaces[0]
        try:
            test_encoder.init_context(1920, 1080, src_format, encoding, 50, 50, (1,1), {})
        finally:
            test_encoder.clean()
