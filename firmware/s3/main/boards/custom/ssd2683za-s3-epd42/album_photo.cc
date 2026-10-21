#include "album_photo.h"

#include <algorithm>
#include <cstring>

#include <esp_heap_caps.h>

#include "config.h"
#include "jpeg_to_image.h"

bool ConvertJpegToBwrPlanes(const uint8_t* jpeg, size_t jpeg_size,
                            uint8_t* bw_plane, uint8_t* red_plane,
                            std::string& error) {
    if (jpeg == nullptr || jpeg_size == 0 || bw_plane == nullptr ||
        red_plane == nullptr) {
        error = "图片数据无效";
        return false;
    }

    uint8_t* rgb565 = nullptr;
    size_t decoded_size = 0, width = 0, height = 0, stride = 0;
    const esp_err_t decode = jpeg_to_image(jpeg, jpeg_size, &rgb565,
                                           &decoded_size, &width, &height,
                                           &stride);
    if (decode != ESP_OK || rgb565 == nullptr || width == 0 || height == 0 ||
        stride < width * 2 || decoded_size < stride * height) {
        if (rgb565) heap_caps_free(rgb565);
        error = "JPEG 解码失败（请使用 JPG/JPEG）";
        return false;
    }

    constexpr size_t kStride = (FPC2_WIDTH + 7) / 8;
    constexpr size_t kPlaneBytes = kStride * FPC2_HEIGHT;
    std::memset(bw_plane, 0xff, kPlaneBytes);   // 1 = white
    std::memset(red_plane, 0x00, kPlaneBytes); // 1 = red

    // Center-crop to the 4:3 panel without allocating a second full RGB image.
    const double scale = std::max(static_cast<double>(FPC2_WIDTH) / width,
                                  static_cast<double>(FPC2_HEIGHT) / height);
    const double crop_w = FPC2_WIDTH / scale;
    const double crop_h = FPC2_HEIGHT / scale;
    const double left = (width - crop_w) * 0.5;
    const double top = (height - crop_h) * 0.5;

    // 4x4 ordered threshold maintains visible midtone detail without an
    // expensive full-frame error buffer on the ESP32-S3.
    static constexpr uint8_t kBayer4[16] = {
        0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5,
    };
    for (int y = 0; y < FPC2_HEIGHT; ++y) {
        const size_t sy = std::min(height - 1, static_cast<size_t>(
            top + (y + 0.5) / scale));
        for (int x = 0; x < FPC2_WIDTH; ++x) {
            const size_t sx = std::min(width - 1, static_cast<size_t>(
                left + (x + 0.5) / scale));
            const size_t offset = sy * stride + sx * 2;
            const uint16_t px = static_cast<uint16_t>(rgb565[offset]) |
                                (static_cast<uint16_t>(rgb565[offset + 1]) << 8);
            const int r = ((px >> 11) & 0x1f) * 255 / 31;
            const int g = ((px >> 5) & 0x3f) * 255 / 63;
            const int b = (px & 0x1f) * 255 / 31;
            const size_t byte = static_cast<size_t>(y) * kStride + x / 8;
            const uint8_t bit = static_cast<uint8_t>(0x80u >> (x & 7));

            const bool red = r > 92 && r > g * 1.22 + 12 && r > b * 1.22 + 12;
            if (red) {
                red_plane[byte] |= bit;
                // For SSD2683ZA, white-under-red encodes yellow; black-under-red
                // encodes red. Keep photo reds distinct from yellow.
                bw_plane[byte] &= static_cast<uint8_t>(~bit);
                continue;
            }

            const int luma = (299 * r + 587 * g + 114 * b) / 1000;
            const int threshold = kBayer4[(y & 3) * 4 + (x & 3)] * 16 + 8;
            if (luma < threshold) bw_plane[byte] &= static_cast<uint8_t>(~bit);
        }
    }
    heap_caps_free(rgb565);
    return true;
}
