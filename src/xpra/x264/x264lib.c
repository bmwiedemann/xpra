/* This file is part of Parti.
 * Copyright (C) 2012 Serviware (Arthur Huillet, <ahuillet@serviware.com>)
 * Copyright (C) 2012 Antoine Martin <antoine@devloop.org.uk>
 * Parti is released under the terms of the GNU GPL v2, or, at your option, any
 * later version. See the file COPYING for details.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <math.h>

//not honoured on MS Windows:
#define MEMALIGN 1
//not honoured on OSX:
#define MEMALIGN_ALIGNMENT 32
//comment this out to turn off csc 422 and 444 colourspace modes
//(ie: when not supported by the library we build against)
#define SUPPORT_CSC_MODES 1

#ifdef _WIN32
#include <malloc.h>
#include "stdint.h"
#include "inttypes.h"
#else
#include <stdint.h>
#include <unistd.h>
#endif

#ifdef _WIN32
typedef void x264_t;
#define inline __inline
#else
#include <x264.h>
#endif

#include <libswscale/swscale.h>
#include <libavcodec/avcodec.h>
#include "x264lib.h"

//beware that these macros may evaluate a or b twice!
//ie: do not use them for something like: MAX(i++, N)
#define MAX(a,b) ((a) > (b) ? a : b)
#define MIN(a,b) ((a) < (b) ? a : b)

struct x264lib_ctx {
	// Both
	int width;
	int height;
	int csc_format;				//PIX_FMT_YUV420P, X264_CSP_I422, PIX_FMT_YUV444P

	// Decoding
	AVCodec *codec;
	AVCodecContext *codec_ctx;
	struct SwsContext *yuv2rgb;

	// Encoding
	x264_t *encoder;
	struct SwsContext *rgb2yuv;

	int quality;				//percentage 0-100
	int supports_csc_option;	//can we change colour sampling
	int encoding_preset;		//index in preset_names 0-9
	float x264_quality;			//rc.f_rf_constant (1 - 50)
	int colour_sampling;		//X264_CSP_I420, X264_CSP_I422 or X264_CSP_I444
	const char* profile;		//PROFILE_BASELINE, PROFILE_HIGH422 or PROFILE_HIGH444_PREDICTIVE
	const char* preset;			//x264_preset_names, see below:
	//x264_preset_names[] = {
	// "ultrafast", "superfast", "veryfast", "faster", "fast", "medium",
	//"slow", "slower", "veryslow", "placebo", 0 }
	int csc_algo;
};

int get_encoder_pixel_format(struct x264lib_ctx *ctx) {
	return ctx->csc_format;
}
int get_encoder_quality(struct x264lib_ctx *ctx) {
	return ctx->quality;
}

#ifndef _WIN32
//Given a quality percentage (0 to 100),
//return the x264 quality constant to use
float get_x264_quality(int pct) {
	return	roundf(50.0 - (MIN(100, MAX(0, pct)) * 49.0 / 100.0));
}

#define I422_MIN_QUALITY 80
#define I444_MIN_QUALITY 90
//Given a quality percentage (0 to 100),
//return the x264 colour sampling to use
//IMPORTANT: changes here must be reflected in get_profile_for_quality
// as not all pixel formats are supported by all profiles.
int get_x264_colour_sampling(struct x264lib_ctx *ctx, int pct)
{
#ifdef SUPPORT_CSC_MODES
	if (!ctx->supports_csc_option || pct<I422_MIN_QUALITY)
		return	X264_CSP_I420;
	else if (pct<I444_MIN_QUALITY)
		return	X264_CSP_I422;
	return	X264_CSP_I444;
#else
	return	X264_CSP_I420;
#endif
}
//Given an x264 colour sampling constant,
//return the corresponding csc constant.
int get_csc_format_for_x264_format(int i_csp)
{
	if (i_csp == X264_CSP_I420)
		return	PIX_FMT_YUV420P;
#ifdef SUPPORT_CSC_MODES
	else if (i_csp == X264_CSP_I422)
		return	PIX_FMT_YUV422P;
	else if (i_csp == X264_CSP_I444)
		return	PIX_FMT_YUV444P;
#endif
	else {
		return -1;
		printf("invalid pixel format: %i", i_csp);
	}
}
#endif

int get_csc_algo_for_quality(int initial_quality) {
	//always use the best quality as lower quality options
	//do not offer a significant speed improvement
	return SWS_SINC | SWS_ACCURATE_RND;
}

#ifndef _WIN32
const char* PROFILE_BASELINE = "baseline";
const char* PROFILE_MAIN = "main";
const char* PROFILE_HIGH = "high";
const char* PROFILE_HIGH10 = "high10";
const char* PROFILE_HIGH422 = "high422";
const char* PROFILE_HIGH444_PREDICTIVE = "high444";
//Given a quality percentage (0 to 100)
//return the profile to use
//IMPORTANT: changes here must be reflected in get_x264_colour_sampling
// as not all pixel formats are supported by all profiles.
const char *get_profile_for_quality(int pct) {
	if (pct<I422_MIN_QUALITY)
		return	PROFILE_BASELINE;
	if (pct<I444_MIN_QUALITY)
		return	PROFILE_HIGH422;
	return	PROFILE_HIGH444_PREDICTIVE;
}


struct SwsContext *init_encoder_csc(struct x264lib_ctx *ctx)
{
	if (ctx->rgb2yuv) {
		sws_freeContext(ctx->rgb2yuv);
		ctx->rgb2yuv = NULL;
	}
	return sws_getContext(ctx->width, ctx->height, PIX_FMT_RGB24, ctx->width, ctx->height, ctx->csc_format, ctx->csc_algo, NULL, NULL, NULL);
}

void do_init_encoder(struct x264lib_ctx *ctx, int width, int height, int initial_quality, int supports_csc_option)
{
	ctx->quality = initial_quality;
	ctx->supports_csc_option = supports_csc_option;
	ctx->colour_sampling = get_x264_colour_sampling(ctx, initial_quality);
	ctx->x264_quality = get_x264_quality(initial_quality);
	ctx->csc_format = get_csc_format_for_x264_format(ctx->colour_sampling);
	ctx->encoding_preset = 2;
	ctx->preset = x264_preset_names[ctx->encoding_preset];
	ctx->profile = get_profile_for_quality(initial_quality);
	ctx->csc_algo = get_csc_algo_for_quality(initial_quality);
	//printf("do_init_encoder(%p, %i, %i, %i, %i) colour_sampling=%i, x264_quality=%f, profile=%s\n", ctx, width, height, initial_quality, supports_csc_option, ctx->colour_sampling, ctx->x264_quality, ctx->profile);

	x264_param_t param;
	x264_param_default_preset(&param, ctx->preset, "zerolatency");
	param.i_threads = 1;
	param.i_width = width;
	param.i_height = height;
	param.i_csp = ctx->colour_sampling;
	param.rc.f_rf_constant = ctx->x264_quality;
	param.i_log_level = 0;
	x264_param_apply_profile(&param, ctx->profile);
	ctx->encoder = x264_encoder_open(&param);
	ctx->width = width;
	ctx->height = height;
	ctx->rgb2yuv = init_encoder_csc(ctx);
}

struct x264lib_ctx *init_encoder(int width, int height, int initial_quality, int supports_csc_option)
{
	struct x264lib_ctx *ctx = malloc(sizeof(struct x264lib_ctx));
	memset(ctx, 0, sizeof(struct x264lib_ctx));
	do_init_encoder(ctx, width, height, initial_quality, supports_csc_option);
	return ctx;
}


void clean_encoder(struct x264lib_ctx *ctx)
{
	if (ctx->rgb2yuv) {
		sws_freeContext(ctx->rgb2yuv);
		ctx->rgb2yuv = NULL;
	}
	if (ctx->encoder) {
		x264_encoder_close(ctx->encoder);
		ctx->encoder = NULL;
	}
}

#else
struct x264lib_ctx *init_encoder(int width, int height)
{
	return NULL;
}

void clean_encoder(struct x264lib_ctx *ctx)
{
	return;
}
#endif

int init_decoder_context(struct x264lib_ctx *ctx, int width, int height, int csc_fmt)
{
	if (csc_fmt<0)
		csc_fmt = PIX_FMT_YUV420P;
	ctx->width = width;
	ctx->height = height;
	ctx->csc_format = csc_fmt;
	ctx->csc_algo = get_csc_algo_for_quality(100);
	ctx->yuv2rgb = sws_getContext(ctx->width, ctx->height, ctx->csc_format, ctx->width, ctx->height, PIX_FMT_RGB24, ctx->csc_algo, NULL, NULL, NULL);

	avcodec_register_all();

	ctx->codec = avcodec_find_decoder(CODEC_ID_H264);
	if (!ctx->codec) {
		fprintf(stderr, "codec H264 not found!\n");
		return 1;
	}
	ctx->codec_ctx = avcodec_alloc_context3(ctx->codec);
	ctx->codec_ctx->width = ctx->width;
	ctx->codec_ctx->height = ctx->height;
	ctx->codec_ctx->pix_fmt = csc_fmt;
	if (avcodec_open2(ctx->codec_ctx, ctx->codec, NULL) < 0) {
		fprintf(stderr, "could not open codec\n");
		return 1;
	}
	return 0;
}
struct x264lib_ctx *init_decoder(int width, int height, int csc_fmt)
{
	struct x264lib_ctx *ctx = malloc(sizeof(struct x264lib_ctx));
	memset(ctx, 0, sizeof(struct x264lib_ctx));
	if (init_decoder_context(ctx, width, height, csc_fmt)) {
		free(ctx);
		return NULL;
	}
	return ctx;
}

void clean_decoder(struct x264lib_ctx *ctx)
{
	if (ctx->codec_ctx) {
		avcodec_close(ctx->codec_ctx);
		av_free(ctx->codec_ctx);
		ctx->codec_ctx = NULL;
	}
	if (ctx->yuv2rgb) {
		sws_freeContext(ctx->yuv2rgb);
		ctx->yuv2rgb = NULL;
	}
}

#ifndef _WIN32
x264_picture_t *csc_image_rgb2yuv(struct x264lib_ctx *ctx, const uint8_t *in, int stride)
{
	if (!ctx->encoder || !ctx->rgb2yuv)
		return NULL;

	x264_picture_t *pic_in = malloc(sizeof(x264_picture_t));
	x264_picture_alloc(pic_in, ctx->colour_sampling, ctx->width, ctx->height);

	/* Colorspace conversion (RGB -> I4??) */
	sws_scale(ctx->rgb2yuv, &in, &stride, 0, ctx->height, pic_in->img.plane, pic_in->img.i_stride);
	return pic_in;
}

static void free_csc_image(x264_picture_t *image)
{
	x264_picture_clean(image);
	free(image);
}

int compress_image(struct x264lib_ctx *ctx, x264_picture_t *pic_in, uint8_t **out, int *outsz, int quality_override)
{
	if (!ctx->encoder || !ctx->rgb2yuv) {
		free_csc_image(pic_in);
		*out = NULL;
		*outsz = 0;
		return 1;
	}
	x264_picture_t pic_out;

	/* Encoding */
	pic_in->i_pts = 1;
	if (quality_override>=0) {
		// Retrieve current parameters and override quality for this frame
		float new_q = get_x264_quality(quality_override);
		if (new_q!=ctx->x264_quality) {
			x264_param_t param;
			x264_encoder_parameters(ctx->encoder, &param);
			param.rc.f_rf_constant = new_q;
			pic_in->param = &param;
		}
	}

	x264_nal_t* nals;
	int i_nals;
	int frame_size = x264_encoder_encode(ctx->encoder, &nals, &i_nals, pic_in, &pic_out);
	if (frame_size < 0) {
		fprintf(stderr, "Problem during x264_encoder_encode: frame_size is invalid!\n");
		free_csc_image(pic_in);
		*out = NULL;
		*outsz = 0;
		return 2;
	}
	free_csc_image(pic_in);
	/* Do not clean that! */
	*out = nals[0].p_payload;
	*outsz = frame_size;
	return 0;
}
#else
x264_picture_t* csc_image_rgb2yuv(struct x264lib_ctx *ctx, const uint8_t *in, int stride) 
{
	return	NULL;
}
int compress_image(struct x264lib_ctx *ctx, x264_picture_t *pic_in, uint8_t **out, int *outsz)
{
	return 1;
}
#endif

int csc_image_yuv2rgb(struct x264lib_ctx *ctx, uint8_t *in[3], const int stride[3], uint8_t **out, int *outsz, int *outstride)
{
	AVPicture pic;

	if (!ctx->yuv2rgb)
		return 1;

	avpicture_fill(&pic, malloc(ctx->height * ctx->width * 3), PIX_FMT_RGB24, ctx->width, ctx->height);

	sws_scale(ctx->yuv2rgb, (const uint8_t * const*) in, stride, 0, ctx->height, pic.data, pic.linesize);

	/* Output (must be freed!) */
	*out = pic.data[0];
	*outsz = pic.linesize[0] * ctx->height;
	*outstride = pic.linesize[0];

	return 0;
}

void set_decoder_csc_format(struct x264lib_ctx *ctx, int csc_fmt)
{
	if (csc_fmt<0)
		csc_fmt = PIX_FMT_YUV420P;
	if (ctx->csc_format!=csc_fmt) {
		//we need to re-initialize with the new format:
		clean_decoder(ctx);
		if (init_decoder_context(ctx, ctx->width, ctx->height, csc_fmt)) {
			fprintf(stderr, "Failed to reconfigure decoder\n");
		}
	}
}

int decompress_image(struct x264lib_ctx *ctx, uint8_t *in, int size, uint8_t *(*out)[3], int *outsize, int (*outstride)[3])
{
	int got_picture;
	int len;
	int i;
	AVFrame picture;
	AVPacket avpkt;

	av_init_packet(&avpkt);

	if (!ctx->codec_ctx || !ctx->codec)
		return 1;

	avcodec_get_frame_defaults(&picture);

	avpkt.data = in;
	avpkt.size = size;

	len = avcodec_decode_video2(ctx->codec_ctx, &picture, &got_picture, &avpkt);
	if (len < 0) {
		fprintf(stderr, "Error while decoding frame\n");
		memset(out, 0, sizeof(*out));
		return 2;
	}

	for (i = 0; i < 3; i++) {
		(*out)[i] = picture.data[i];
		*outsize += ctx->height * picture.linesize[i];
		(*outstride)[i] = picture.linesize[i];
	}

    if (*outsize == 0) {
        fprintf(stderr, "Decoded image, size %d %d %d, ptr %p %p %p\n", (*outstride)[0] * ctx->height, (*outstride)[1]*ctx->height, (*outstride)[2]*ctx->height, picture.data[0], picture.data[1], picture.data[2]);
        return 3;
    }

	return 0;
}

/**
 * Change the speed of encoding (x264 preset).
 * @param percent: 100 for maximum ("ultrafast") with lowest compression, 0 for highest compression (slower)
 */
#ifndef _WIN32
void set_encoding_speed(struct x264lib_ctx *ctx, int pct)
{
	x264_param_t param;
	x264_encoder_parameters(ctx->encoder, &param);
	int new_preset = 7-MAX(0, MIN(7, pct/12.5));
	if (new_preset==ctx->encoding_preset)
		return;
	ctx->encoding_preset = new_preset;
	//"tune" options: film, animation, grain, stillimage, psnr, ssim, fastdecode, zerolatency
	//Multiple tunings can be used if separated by a delimiter in ",./-+"
	//however multiple psy tunings cannot be used.
	//film, animation, grain, stillimage, psnr, and ssim are psy tunings.
	x264_param_default_preset(&param, x264_preset_names[ctx->encoding_preset], "zerolatency");
	x264_param_apply_profile(&param, "baseline");
	x264_encoder_reconfig(ctx->encoder, &param);
}
#else
void set_encoding_speed(struct x264lib_ctx *ctx, int pct)
{
	;
}
#endif

/**
 * Change the quality of encoding (x264 f_rf_constant).
 * @param percent: 100 for best quality, 0 for lowest quality.
 */
#ifndef _WIN32
void set_encoding_quality(struct x264lib_ctx *ctx, int pct)
{
	if (ctx->supports_csc_option) {
		int new_colour_sampling = get_x264_colour_sampling(ctx, pct);
		if (ctx->colour_sampling!=new_colour_sampling) {
			//pixel encoding has changed, we must re-init everything:
			//printf("set_encoding_quality(%i) old colour_sampling=%i, new colour_sampling %i\n", pct, ctx->colour_sampling, new_colour_sampling);
			clean_encoder(ctx);
			do_init_encoder(ctx, ctx->width , ctx->height, pct, ctx->supports_csc_option);
			return;
		}
	}
	if ((ctx->quality & ~0x1)!=(pct & ~0x1)) {
		float new_quality = get_x264_quality(pct);
		//float old_quality = ctx->x264_quality;
		//printf("set_encoding_quality(%i) was %i, new x264 quality %f was %f\n", pct, ctx->quality, new_quality, old_quality);
		//only f_rf_constant was changed,
		//read new configuration is sufficient
		x264_param_t param;
		// Retrieve current parameters
		x264_encoder_parameters(ctx->encoder, &param);
		ctx->quality = pct;
		ctx->x264_quality = new_quality;
		param.rc.f_rf_constant = new_quality;
		x264_encoder_reconfig(ctx->encoder, &param);
	}
	int old_csc_algo = ctx->csc_algo;
	ctx->csc_algo = get_csc_algo_for_quality(pct);
	if (old_csc_algo!=ctx->csc_algo) {
		ctx->rgb2yuv = init_encoder_csc(ctx);
	}
}
#else
void set_encoding_quality(struct x264lib_ctx *ctx, int pct)
{
	;
}
#endif


void* xmemalign(size_t size)
{
#ifdef MEMALIGN
#ifdef _WIN32
	//_aligned_malloc and _aligned_free lead to a memleak
	//well done Microsoft, I didn't think you could screw up this badly
	//and thank you for wasting my time once again
	return malloc(size);
#elif defined(__APPLE__) || defined(__OSX__)
	//Crapple version: "all memory allocations are 16-byte aligned"
	//no choice, this is what you get
	return malloc(size);
#else
	//not WIN32 and not APPLE/OSX, assume POSIX:
	void* memptr=NULL;
	if (posix_memalign(&memptr, MEMALIGN_ALIGNMENT, size))
		return	NULL;
	return	memptr;
#endif
//MEMALIGN not set:
#else
	return	malloc(size);
#endif
}

void xmemfree(void *ptr)
{
	free(ptr);
}
