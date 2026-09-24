#include "ShapedText.h"

#include <HalStorage.h>
#include <Logging.h>
#ifdef SIMULATOR
#define heap_caps_malloc(n, caps) malloc(n)
#else
#include <esp_heap_caps.h>
#endif
#include <hb-ot.h>
#include <stb_truetype.h>

#include <cstring>
#include <new>

namespace {
constexpr const char* FONT_PATH = "/fonts/NotoSerifBengali-Regular.ttf";

uint8_t* allocPsram(size_t size) {
  uint8_t* p = static_cast<uint8_t*>(heap_caps_malloc(size, MALLOC_CAP_SPIRAM));
  if (!p) {
    p = static_cast<uint8_t*>(malloc(size));
  }
  return p;
}

uint32_t utf8Decode(const char*& p) {
  const auto b0 = static_cast<unsigned char>(*p);
  if (b0 < 0x80) {
    p += 1;
    return b0;
  }
  if ((b0 & 0xE0) == 0xC0 && p[1]) {
    uint32_t cp = ((b0 & 0x1F) << 6) | (static_cast<unsigned char>(p[1]) & 0x3F);
    p += 2;
    return cp;
  }
  if ((b0 & 0xF0) == 0xE0 && p[1] && p[2]) {
    uint32_t cp = ((b0 & 0x0F) << 12) | ((static_cast<unsigned char>(p[1]) & 0x3F) << 6) |
                  (static_cast<unsigned char>(p[2]) & 0x3F);
    p += 3;
    return cp;
  }
  if ((b0 & 0xF8) == 0xF0 && p[1] && p[2] && p[3]) {
    uint32_t cp = ((b0 & 0x07) << 18) | ((static_cast<unsigned char>(p[1]) & 0x3F) << 12) |
                  ((static_cast<unsigned char>(p[2]) & 0x3F) << 6) | (static_cast<unsigned char>(p[3]) & 0x3F);
    p += 4;
    return cp;
  }
  p += 1;
  return 0xFFFD;
}
}  // namespace

ShapedText& ShapedText::instance() {
  static ShapedText inst;
  return inst;
}

bool ShapedText::hasBengali(const char* utf8) {
  if (!utf8) return false;
  const char* p = utf8;
  while (*p) {
    const uint32_t cp = utf8Decode(p);
    if ((cp >= 0x0980 && cp <= 0x09FF) || cp == 0x0964 || cp == 0x0965) return true;
  }
  return false;
}

void ShapedText::load() {
  loadAttempted_ = true;

  HalFile file;
  if (!Storage.openFileForRead("SHAPEDTEXT", FONT_PATH, file)) {
    LOG_INF("SHAPEDTEXT", "%s not found, Bengali shaping disabled", FONT_PATH);
    disabled_ = true;
    return;
  }

  const size_t size = file.fileSize();
  ttfBuffer_ = allocPsram(size);
  if (!ttfBuffer_) {
    LOG_ERR("SHAPEDTEXT", "OOM allocating %u bytes for %s", static_cast<unsigned>(size), FONT_PATH);
    disabled_ = true;
    return;
  }
  if (file.read(ttfBuffer_, size) != static_cast<int>(size)) {
    LOG_ERR("SHAPEDTEXT", "short read on %s", FONT_PATH);
    free(ttfBuffer_);
    ttfBuffer_ = nullptr;
    disabled_ = true;
    return;
  }

  stbFontOffset_ = stbtt_GetFontOffsetForIndex(ttfBuffer_, 0);
  stbtt_fontinfo probe;
  if (stbFontOffset_ < 0 || !stbtt_InitFont(&probe, ttfBuffer_, stbFontOffset_)) {
    LOG_ERR("SHAPEDTEXT", "stbtt_InitFont failed on %s", FONT_PATH);
    free(ttfBuffer_);
    ttfBuffer_ = nullptr;
    disabled_ = true;
    return;
  }

  cache_ = new (allocPsram(sizeof(CacheEntry) * CACHE_SIZE)) CacheEntry[CACHE_SIZE];
  shapeCache_ = new (allocPsram(sizeof(ShapeEntry) * SHAPE_CACHE_SIZE)) ShapeEntry[SHAPE_CACHE_SIZE];
  if (!cache_ || !shapeCache_) {
    LOG_ERR("SHAPEDTEXT", "OOM allocating glyph caches");
    disabled_ = true;
    return;
  }

  hbBlob_ = hb_blob_create(reinterpret_cast<const char*>(ttfBuffer_), size, HB_MEMORY_MODE_READONLY, nullptr,
                            nullptr);
  hbFace_ = hb_face_create(hbBlob_, 0);
  hbFont_ = hb_font_create(hbFace_);
  hb_ot_font_set_funcs(hbFont_);

  LOG_INF("SHAPEDTEXT", "loaded %s (%u bytes)", FONT_PATH, static_cast<unsigned>(size));
}

bool ShapedText::ready() {
  if (!loadAttempted_) load();
  return !disabled_;
}

// One scale for both libraries: stb maps ascent-descent to pixelSize, and HarfBuzz
// is told the same pixels-per-em in 26.6 so advances and bitmaps agree.
static float stbScale(const stbtt_fontinfo& font, int pixelSize) {
  return stbtt_ScaleForPixelHeight(&font, static_cast<float>(pixelSize));
}

void ShapedText::applyScale(int pixelSize) {
  if (pixelSize == scaledPixelSize_) return;
  scaledPixelSize_ = pixelSize;
  stbtt_fontinfo font;
  stbtt_InitFont(&font, ttfBuffer_, stbFontOffset_);
  const int upem = static_cast<int>(hb_face_get_upem(hbFace_));
  const int s26 = static_cast<int>(stbScale(font, pixelSize) * upem * 64.0f + 0.5f);
  hb_font_set_scale(hbFont_, s26, s26);
}

const ShapedText::CacheEntry* ShapedText::rasterize(uint32_t glyphId, int pixelSize) {
  for (int i = 0; i < CACHE_SIZE; i++) {
    const CacheEntry& e = cache_[i];
    if (e.valid && e.glyphId == glyphId && e.pixelSize == pixelSize) return &e;
  }

  stbtt_fontinfo font;
  stbtt_InitFont(&font, ttfBuffer_, stbFontOffset_);
  const float scale = stbScale(font, pixelSize);

  int x0, y0, x1, y1;
  stbtt_GetGlyphBitmapBox(&font, static_cast<int>(glyphId), scale, scale, &x0, &y0, &x1, &y1);
  const int w = x1 - x0;
  const int h = y1 - y0;

  CacheEntry& slot = cache_[cacheNext_];
  cacheNext_ = (cacheNext_ + 1) % CACHE_SIZE;
  if (slot.bitmap) {
    free(slot.bitmap);
    slot.bitmap = nullptr;
  }

  slot.valid = true;
  slot.glyphId = glyphId;
  slot.pixelSize = pixelSize;
  slot.w = w;
  slot.h = h;
  slot.xOff = x0;
  slot.yOff = y0;

  if (w > 0 && h > 0) {
    slot.bitmap = allocPsram(static_cast<size_t>(w) * h);
    if (slot.bitmap) {
      stbtt_MakeGlyphBitmap(&font, slot.bitmap, w, h, w, scale, scale, static_cast<int>(glyphId));
    }
  }
  return &slot;
}

int ShapedText::shape(const char* utf8, int pixelSize, ShapedGlyph* out) {
  const size_t len = strlen(utf8);
  const bool cacheable = len < SHAPE_TEXT_MAX;
  if (cacheable) {
    for (int i = 0; i < SHAPE_CACHE_SIZE; i++) {
      const ShapeEntry& e = shapeCache_[i];
      if (e.pixelSize == pixelSize && strcmp(e.text, utf8) == 0) {
        memcpy(out, e.glyphs, sizeof(ShapedGlyph) * e.count);
        return e.count;
      }
    }
  }

  hb_buffer_t* buf = hb_buffer_create();
  hb_buffer_add_utf8(buf, utf8, static_cast<int>(len), 0, -1);
  hb_buffer_set_direction(buf, HB_DIRECTION_LTR);
  hb_buffer_set_script(buf, HB_SCRIPT_BENGALI);
  hb_buffer_set_language(buf, hb_language_from_string("bn", -1));

  applyScale(pixelSize);
  hb_shape(hbFont_, buf, nullptr, 0);

  unsigned int count = 0;
  hb_glyph_info_t* infos = hb_buffer_get_glyph_infos(buf, &count);
  hb_glyph_position_t* positions = hb_buffer_get_glyph_positions(buf, &count);
  if (count > SHAPE_GLYPHS_MAX) count = SHAPE_GLYPHS_MAX;  // ponytail: clip absurdly long "words"
  for (unsigned int i = 0; i < count; i++) {
    out[i] = {infos[i].codepoint, positions[i].x_offset, positions[i].y_offset, positions[i].x_advance};
  }
  hb_buffer_destroy(buf);

  if (cacheable) {
    ShapeEntry& slot = shapeCache_[shapeNext_];
    shapeNext_ = (shapeNext_ + 1) % SHAPE_CACHE_SIZE;
    memcpy(slot.text, utf8, len + 1);
    slot.pixelSize = pixelSize;
    slot.count = static_cast<int>(count);
    memcpy(slot.glyphs, out, sizeof(ShapedGlyph) * count);
  }
  return static_cast<int>(count);
}

int ShapedText::width(const char* utf8, int pixelSize) {
  if (!ready() || !utf8 || !*utf8) return 0;
  ShapedGlyph glyphs[SHAPE_GLYPHS_MAX];
  const int n = shape(utf8, pixelSize, glyphs);
  int total = 0;
  for (int i = 0; i < n; i++) total += glyphs[i].xAdvance;
  return (total + 32) >> 6;
}

void ShapedText::draw(const void* renderer, PixelPlotFn plot, const char* utf8, int x, int baselineY, int pixelSize,
                      bool black) {
  if (!ready() || !utf8 || !*utf8) return;

  ShapedGlyph glyphs[SHAPE_GLYPHS_MAX];
  const int n = shape(utf8, pixelSize, glyphs);
  int penX = x << 6;  // 26.6
  for (int i = 0; i < n; i++) {
    const ShapedGlyph& sg = glyphs[i];
    if (sg.glyphId != 0) {
      const CacheEntry* g = rasterize(sg.glyphId, pixelSize);
      if (g && g->bitmap) {
        const int gx = ((penX + sg.xOffset + 32) >> 6) + g->xOff;
        const int gy = baselineY - ((sg.yOffset + 32) >> 6) + g->yOff;
        for (int row = 0; row < g->h; row++) {
          for (int col = 0; col < g->w; col++) {
            if (g->bitmap[row * g->w + col] >= 128) {
              plot(renderer, gx + col, gy + row, black);
            }
          }
        }
      }
    }
    penX += sg.xAdvance;
  }
}
