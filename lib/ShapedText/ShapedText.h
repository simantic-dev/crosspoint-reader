#pragma once

// Bengali shaping + rasterizing path: HarfBuzz shapes conjuncts/reph/vowel
// reordering, stb_truetype rasterizes glyphs from a TTF on the SD card. Used
// only for Bengali-bearing strings; everything else stays on the built-in
// bitmap-font path in GfxRenderer.

#include <hb.h>

#include <cstdint>

class ShapedText {
 public:
  // Plots one pixel through GfxRenderer::drawPixel. `renderer` is the
  // GfxRenderer instance passed into draw(), opaque here so this library
  // doesn't need to depend on GfxRenderer's headers.
  using PixelPlotFn = void (*)(const void* renderer, int x, int y, bool state);

  static ShapedText& instance();

  // True if `utf8` contains any codepoint in the Bengali block (U+0980-U+09FF)
  // or the danda/double danda (U+0964/U+0965, shared from the Devanagari block).
  static bool hasBengali(const char* utf8);

  // Lazily loads /fonts/NotoSerifBengali-Regular.ttf from the SD card on
  // first use. False (permanently) if the file is missing or malformed.
  bool ready();

  int width(const char* utf8, int pixelSize);
  void draw(const void* renderer, PixelPlotFn plot, const char* utf8, int x, int baselineY, int pixelSize,
            bool black);

 private:
  ShapedText() = default;
  void load();

  bool loadAttempted_ = false;
  bool disabled_ = false;

  uint8_t* ttfBuffer_ = nullptr;  // PSRAM (or heap fallback), owns the whole file
  hb_blob_t* hbBlob_ = nullptr;
  hb_face_t* hbFace_ = nullptr;
  hb_font_t* hbFont_ = nullptr;
  int stbFontOffset_ = 0;

  // ponytail: fixed-size round-robin glyph bitmap cache, no LRU. Bump
  // CACHE_SIZE or add real LRU if profiling shows thrashing across pixel sizes.
  static constexpr int CACHE_SIZE = 1024;
  struct CacheEntry {
    bool valid = false;
    uint32_t glyphId = 0;
    int pixelSize = 0;
    uint8_t* bitmap = nullptr;  // PSRAM, 8-bit coverage, w*h bytes
    int w = 0, h = 0;
    int xOff = 0, yOff = 0;  // top-left offset relative to (penX, baseline)
  };
  CacheEntry* cache_ = nullptr;  // [CACHE_SIZE], PSRAM, allocated in load()
  int cacheNext_ = 0;

  void applyScale(int pixelSize);
  int scaledPixelSize_ = 0;
  const CacheEntry* rasterize(uint32_t glyphId, int pixelSize);

  // ponytail: round-robin cache of shaped words, so the four draw passes per
  // page and the layout width query shape each word once.
  struct ShapedGlyph {
    uint32_t glyphId;
    int32_t xOffset, yOffset, xAdvance;  // 26.6
  };
  // Fixed-size fields so the whole entry sits in the PSRAM table; nothing
  // here touches the internal heap. Words that don't fit are shaped uncached.
  static constexpr int SHAPE_TEXT_MAX = 64;
  static constexpr int SHAPE_GLYPHS_MAX = 32;
  struct ShapeEntry {
    char text[SHAPE_TEXT_MAX] = {0};
    int pixelSize = 0;
    int count = 0;
    ShapedGlyph glyphs[SHAPE_GLYPHS_MAX];
  };
  static constexpr int SHAPE_CACHE_SIZE = 512;
  ShapeEntry* shapeCache_ = nullptr;  // [SHAPE_CACHE_SIZE], PSRAM, allocated in load()
  int shapeNext_ = 0;
  // Shapes into `out` (caller-owned, SHAPE_GLYPHS_MAX big) and returns the count.
  int shape(const char* utf8, int pixelSize, ShapedGlyph* out);
};
