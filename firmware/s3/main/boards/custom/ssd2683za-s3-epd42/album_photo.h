#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

// Decode a JPEG and convert it to the SSD1683-compatible 400x300 B/W+red planes.
bool ConvertJpegToBwrPlanes(const uint8_t* jpeg, size_t jpeg_size,
                            uint8_t* bw_plane, uint8_t* red_plane,
                            std::string& error);
